"""Logs en JSON a la salida estándar, una línea por evento, con el `X-Request-ID` de la
petición en cada una: así un servicio que llama puede buscar en estos logs lo que pasó con
su petición."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from api.tracing import RequestTrace, Stage, current_trace

ACCESS_LOGGER = "api.access"
REQUEST_ID_FIELD = "request_id"
# Campos que la línea de acceso añade con `extra`; el formateador los copia si están.
ACCESS_FIELDS = ("method", "path", "status", "service", "duration_ms")
# uvicorn trae sus propios formatos de texto; se reencaminan a la raíz para que todo salga
# en JSON. Su log de acceso sobra: la línea de `api.access` dice lo mismo y más.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error")
UVICORN_ACCESS_LOGGER = "uvicorn.access"
HEALTH_PATH = "/health"
READY_PATH = "/ready"
METRICS_PATH = "/metrics"
PROBE_PATHS = frozenset({HEALTH_PATH, READY_PATH, METRICS_PATH})
FIRST_ERROR_STATUS = 400


class JsonFormatter(logging.Formatter):
    """Un objeto JSON por registro: hora UTC, nivel, logger, mensaje y request id.

    El request id es el que trae el registro o, si no trae ninguno, el de la petición en
    curso: un registro se formatea en el hilo que lo emitió, dentro de su petición.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            REQUEST_ID_FIELD: _request_id_of(record),
        }
        entry.update(
            (name, getattr(record, name)) for name in ACCESS_FIELDS if hasattr(record, name)
        )
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Manda todos los logs del proceso, en JSON, a la salida estándar.

    Raises:
        ValueError: si `level` no es un nivel de `logging`.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger(UVICORN_ACCESS_LOGGER).disabled = True


def log_request(method: str, path: str, status: int, trace: RequestTrace) -> None:
    """La línea de acceso de una petición ya respondida, con su tiempo total.

    Las sondas de salud que responden bien van en DEBUG: el healthcheck de Docker llama cada
    pocos segundos y taparía las líneas que importan. Una sonda que falla sí se ve.
    """
    logging.getLogger(ACCESS_LOGGER).log(
        _access_level(path, status),
        "%s %s %d",
        method,
        path,
        status,
        extra={
            REQUEST_ID_FIELD: trace.request_id,
            "method": method,
            "path": path,
            "status": status,
            "service": trace.caller,
            "duration_ms": trace.stages.get(Stage.TOTAL),
        },
    )


def _access_level(path: str, status: int) -> int:
    if path in PROBE_PATHS and status < FIRST_ERROR_STATUS:
        return logging.DEBUG
    return logging.INFO


def _request_id_of(record: logging.LogRecord) -> str | None:
    stamped = getattr(record, REQUEST_ID_FIELD, None)
    if isinstance(stamped, str):
        return stamped
    trace = current_trace()
    return None if trace is None else trace.request_id
