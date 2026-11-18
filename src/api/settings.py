"""Configuración del API tomada del entorno: nada de rutas, orígenes ni tiempos incrustados."""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from api.auth import Role, ServiceKey
from config import EngineConfig

DATA_DIRECTORY_VARIABLE = "MINIGESTOR_DATA_DIR"
IMPORT_DIRECTORIES_VARIABLE = "MINIGESTOR_IMPORT_DIRS"
CORS_ORIGINS_VARIABLE = "MINIGESTOR_CORS_ORIGINS"
LOCK_TIMEOUT_VARIABLE = "MINIGESTOR_LOCK_TIMEOUT"
STATEMENT_TIMEOUT_VARIABLE = "MINIGESTOR_STATEMENT_TIMEOUT"
MAX_REQUEST_BYTES_VARIABLE = "MINIGESTOR_MAX_REQUEST_BYTES"
INGEST_BATCH_ROWS_VARIABLE = "MINIGESTOR_INGEST_BATCH_ROWS"
MAX_INGEST_LINE_BYTES_VARIABLE = "MINIGESTOR_MAX_INGEST_LINE_BYTES"
SERVICE_KEYS_VARIABLE = "MINIGESTOR_API_KEYS"
LOG_LEVEL_VARIABLE = "MINIGESTOR_LOG_LEVEL"
DEFAULT_LOG_LEVEL = "INFO"
LIST_SEPARATOR = ","
KEY_FIELD_SEPARATOR = ":"
KEY_FIELDS = 3
SERVICE_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")
SHA256_HEX_PATTERN = re.compile(r"[0-9a-f]{64}")


class SettingsError(Exception):
    """Falta una variable de entorno obligatoria o su valor no se entiende."""


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """Lo que el servidor necesita para arrancar."""

    config: EngineConfig
    cors_origins: tuple[str, ...]
    service_keys: tuple[ServiceKey, ...] = ()
    log_level: str = DEFAULT_LOG_LEVEL


def settings_from_environment() -> ApiSettings:
    """Configuración según las variables de entorno (ver `.env.example`).

    `MINIGESTOR_CORS_ORIGINS` es obligatoria: sin ella el navegador rechazaría en silencio
    cada llamada del frontend, y es mejor que el servidor no arranque y diga por qué.

    Raises:
        SettingsError: si falta una variable obligatoria, un número no es un número finito,
            el directorio de datos viene vacío, una clave de servicio está mal escrita o el
            nivel de log no existe.
    """
    defaults = EngineConfig()
    config = EngineConfig(
        data_directory=_directory_or(DATA_DIRECTORY_VARIABLE, defaults.data_directory),
        import_directories=tuple(Path(item) for item in _list_of(IMPORT_DIRECTORIES_VARIABLE)),
        lock_timeout_seconds=_number_or(LOCK_TIMEOUT_VARIABLE, defaults.lock_timeout_seconds),
        statement_timeout_seconds=_number_or(
            STATEMENT_TIMEOUT_VARIABLE, defaults.statement_timeout_seconds
        ),
        max_request_bytes=_integer_or(MAX_REQUEST_BYTES_VARIABLE, defaults.max_request_bytes),
        ingest_batch_rows=_integer_or(INGEST_BATCH_ROWS_VARIABLE, defaults.ingest_batch_rows),
        max_ingest_line_bytes=_integer_or(
            MAX_INGEST_LINE_BYTES_VARIABLE, defaults.max_ingest_line_bytes
        ),
    )
    origins = _list_of(CORS_ORIGINS_VARIABLE)
    if not origins:
        raise SettingsError(
            f"falta {CORS_ORIGINS_VARIABLE}: los orígenes desde los que el frontend llama al "
            "API, separados por comas (ver .env.example)"
        )
    return ApiSettings(
        config=config,
        cors_origins=origins,
        service_keys=_service_keys(),
        log_level=_log_level(),
    )


def _service_keys() -> tuple[ServiceKey, ...]:
    """Las claves de `MINIGESTOR_API_KEYS`: entradas `servicio:rol:sha256` separadas por comas.

    Un servicio o un hash repetido es un error: dos servicios con la misma clave no se
    podrían distinguir en el log.
    """
    keys = tuple(_service_key(entry) for entry in _list_of(SERVICE_KEYS_VARIABLE))
    for attribute in ("service", "digest"):
        values = [getattr(key, attribute) for key in keys]
        if len(set(values)) != len(values):
            raise SettingsError(f"{SERVICE_KEYS_VARIABLE} repite un {attribute}")
    return keys


def _service_key(entry: str) -> ServiceKey:
    fields = [part.strip() for part in entry.split(KEY_FIELD_SEPARATOR)]
    if len(fields) != KEY_FIELDS:
        raise SettingsError(
            f"{SERVICE_KEYS_VARIABLE}: cada clave va como servicio:rol:sha256, no '{entry}'"
        )
    service, role, digest = fields
    if not SERVICE_NAME_PATTERN.fullmatch(service):
        raise SettingsError(f"{SERVICE_KEYS_VARIABLE}: '{service}' no es un nombre de servicio")
    if role not in {item.value for item in Role}:
        raise SettingsError(f"{SERVICE_KEYS_VARIABLE}: rol desconocido '{role}' en '{service}'")
    if not SHA256_HEX_PATTERN.fullmatch(digest):
        raise SettingsError(
            f"{SERVICE_KEYS_VARIABLE}: la clave de '{service}' va como su SHA-256 en "
            "hexadecimal (64 caracteres en minúscula), nunca en claro"
        )
    return ServiceKey(service=service, role=Role(role), digest=digest)


def _log_level() -> str:
    level = os.environ.get(LOG_LEVEL_VARIABLE, DEFAULT_LOG_LEVEL).strip().upper()
    if level not in logging.getLevelNamesMapping():
        raise SettingsError(f"{LOG_LEVEL_VARIABLE}: nivel de log desconocido '{level}'")
    return level


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


def _integer_or(variable: str, default: int) -> int:
    raw = os.environ.get(variable)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise SettingsError(f"{variable} debe ser un número entero, no '{raw}'") from error


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
