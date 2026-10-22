"""Configuración del API tomada del entorno: nada de rutas, orígenes ni tiempos incrustados."""

from __future__ import annotations

import os
from pathlib import Path

from config import EngineConfig

DATA_DIRECTORY_VARIABLE = "MINIGESTOR_DATA_DIR"
CORS_ORIGINS_VARIABLE = "MINIGESTOR_CORS_ORIGINS"
DEFAULT_CORS_ORIGINS = "http://localhost:5173"
LOCK_TIMEOUT_VARIABLE = "MINIGESTOR_LOCK_TIMEOUT"


def build_config() -> EngineConfig:
    """Configuración del motor según las variables de entorno.

    `MINIGESTOR_LOCK_TIMEOUT` sube la espera máxima por un bloqueo: en una demostración en
    vivo conviene ampliarla para que una transacción abierta no caduque mientras se explica.
    """
    defaults = EngineConfig()
    directory = os.environ.get(DATA_DIRECTORY_VARIABLE)
    timeout = os.environ.get(LOCK_TIMEOUT_VARIABLE)
    return EngineConfig(
        data_directory=defaults.data_directory if directory is None else Path(directory),
        lock_timeout_seconds=(
            defaults.lock_timeout_seconds if timeout is None else float(timeout)
        ),
    )


def cors_origins() -> list[str]:
    return os.environ.get(CORS_ORIGINS_VARIABLE, DEFAULT_CORS_ORIGINS).split(",")
