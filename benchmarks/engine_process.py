"""El API del gestor en otro proceso, como en Docker: uvicorn con `app_from_environment`.

Los experimentos que miden por HTTP lo usan para que el cliente y el servidor no compartan
el GIL ni el bucle de eventos, y para que el envío por partes de la carga masiva salga de
verdad a la red local.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import httpx

from api.settings import (
    CORS_ORIGINS_VARIABLE,
    DATA_DIRECTORY_VARIABLE,
    LOG_LEVEL_VARIABLE,
    SERVICE_KEYS_VARIABLE,
)

LOOPBACK = "127.0.0.1"
ANY_FREE_PORT = 0
CORS_ORIGIN = "http://localhost"
SERVER_LOG_LEVEL = "WARNING"
SERVER_START_SECONDS = 60.0
SERVER_POLL_SECONDS = 0.05
HEALTH_PATH = "/health"


@contextmanager
def engine_server(directory: Path, environment: Mapping[str, str]) -> Iterator[str]:
    """La URL de un uvicorn en otro proceso sobre el directorio de datos `directory`, con las
    variables de `environment` además de las del proceso, hasta salir del bloque. Sin claves
    de servicio: el experimento no mide la autenticación."""
    port = _free_port()
    variables = {key: value for key, value in os.environ.items() if key != SERVICE_KEYS_VARIABLE}
    variables.update(
        {
            DATA_DIRECTORY_VARIABLE: str(directory),
            CORS_ORIGINS_VARIABLE: CORS_ORIGIN,
            LOG_LEVEL_VARIABLE: SERVER_LOG_LEVEL,
            **environment,
        }
    )
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "--factory",
        "api.main:app_from_environment",
        "--host",
        LOOPBACK,
        "--port",
        str(port),
        "--log-level",
        SERVER_LOG_LEVEL.lower(),
    ]
    server = subprocess.Popen(command, env=variables)
    base_url = f"http://{LOOPBACK}:{port}"
    try:
        _wait_until_healthy(base_url, server)
        yield base_url
    finally:
        server.terminate()
        server.wait()


def _wait_until_healthy(base_url: str, server: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + SERVER_START_SECONDS
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"el servidor terminó al arrancar con código {server.returncode}")
        try:
            if httpx.get(base_url + HEALTH_PATH).status_code == httpx.codes.OK:
                return
        except httpx.TransportError:
            pass
        time.sleep(SERVER_POLL_SECONDS)
    raise RuntimeError(f"el servidor no respondió en {SERVER_START_SECONDS:g} s")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, ANY_FREE_PORT))
        port: int = probe.getsockname()[1]
        return port
