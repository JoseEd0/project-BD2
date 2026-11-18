"""El API del gestor servido de verdad, por HTTP en un puerto libre, para el cliente síncrono.

Se usa uvicorn en un hilo y no un puente ASGI en memoria: el cliente síncrono habla con un
socket, así que se prueba el envío por trozos de la carga masiva tal como sale a la red, el
pool de conexiones y una conexión rechazada de verdad. El transporte de prueba de Starlette
es privado y junta el cuerpo entero antes de entregarlo. El cliente asíncrono usa
`httpx.ASGITransport`, que sí entrega el cuerpo por partes.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager

import uvicorn
from fastapi import FastAPI

LOCALHOST = "127.0.0.1"
STARTUP_LIMIT_SECONDS = 10.0
POLL_SECONDS = 0.01


@contextmanager
def serving(app: FastAPI) -> Iterator[str]:
    """La URL base de `app`, servida mientras dura el bloque; al salir corre su `lifespan`."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((LOCALHOST, 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, loop="asyncio", log_config=None, access_log=False, lifespan="on")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        _wait_until_started(server)
        yield f"http://{LOCALHOST}:{port}"
    finally:
        server.should_exit = True
        thread.join()
        listener.close()


def unused_port_url() -> str:
    """Una URL a la que nadie escucha: el puerto se pidió libre y se soltó enseguida."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind((LOCALHOST, 0))
        port = probe.getsockname()[1]
    return f"http://{LOCALHOST}:{port}"


def _wait_until_started(server: uvicorn.Server) -> None:
    waited = 0.0
    while not server.started:
        if waited > STARTUP_LIMIT_SECONDS:
            raise TimeoutError("uvicorn no arrancó")
        time.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
