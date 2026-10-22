"""Configuración del API tomada del entorno: nada de rutas, orígenes ni tiempos incrustados."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig

DATA_DIRECTORY_VARIABLE = "MINIGESTOR_DATA_DIR"
IMPORT_DIRECTORIES_VARIABLE = "MINIGESTOR_IMPORT_DIRS"
CORS_ORIGINS_VARIABLE = "MINIGESTOR_CORS_ORIGINS"
LOCK_TIMEOUT_VARIABLE = "MINIGESTOR_LOCK_TIMEOUT"
LIST_SEPARATOR = ","


class SettingsError(Exception):
    """Falta una variable de entorno obligatoria o su valor no se entiende."""


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """Lo que el servidor necesita para arrancar."""

    config: EngineConfig
    cors_origins: tuple[str, ...]


def settings_from_environment() -> ApiSettings:
    """Configuración según las variables de entorno (ver `.env.example`).

    `MINIGESTOR_CORS_ORIGINS` es obligatoria: sin ella el navegador rechazaría en silencio
    cada llamada del frontend, y es mejor que el servidor no arranque y diga por qué.

    Raises:
        SettingsError: si falta una variable obligatoria, un número no es un número finito
            o el directorio de datos viene vacío.
    """
    defaults = EngineConfig()
    config = EngineConfig(
        data_directory=_directory_or(DATA_DIRECTORY_VARIABLE, defaults.data_directory),
        import_directories=tuple(Path(item) for item in _list_of(IMPORT_DIRECTORIES_VARIABLE)),
        lock_timeout_seconds=_number_or(LOCK_TIMEOUT_VARIABLE, defaults.lock_timeout_seconds),
    )
    origins = _list_of(CORS_ORIGINS_VARIABLE)
    if not origins:
        raise SettingsError(
            f"falta {CORS_ORIGINS_VARIABLE}: los orígenes desde los que el frontend llama al "
            "API, separados por comas (ver .env.example)"
        )
    return ApiSettings(config=config, cors_origins=origins)


def _list_of(variable: str) -> tuple[str, ...]:
    raw = os.environ.get(variable, "")
    return tuple(item.strip() for item in raw.split(LIST_SEPARATOR) if item.strip())


def _directory_or(variable: str, default: Path) -> Path:
    """Una variable vacía no es «sin definir»: `Path("")` es el directorio actual, y la base
    de datos acabaría mezclada con el código del servidor."""
    raw = os.environ.get(variable)
    if raw is None:
        return default
    if not raw.strip():
        raise SettingsError(f"{variable} está vacía: quítala o indica un directorio")
    return Path(raw)


def _number_or(variable: str, default: float) -> float:
    raw = os.environ.get(variable)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise SettingsError(f"{variable} debe ser un número de segundos, no '{raw}'") from error
    if not math.isfinite(value):
        raise SettingsError(f"{variable} debe ser un número finito de segundos, no '{raw}'")
    return value
