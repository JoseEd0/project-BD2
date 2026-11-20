"""Logs en JSON a la salida estándar, con la misma forma que los del gestor
(`src/api/logs.py`): una línea por evento con hora UTC, nivel, logger, mensaje y el
`X-Request-ID` de la petición, y una línea de acceso por petición. Así `docker compose logs`
se lee igual en todo el sistema y un id se sigue de la app al gestor."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from minigestor_client import current_request_id

ACCESS_LOGGER = "appkit.access"
REQUEST_ID_FIELD = "request_id"
ACCESS_FIELDS = ("method", "path", "status", "service", "duration_ms")
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error")
UVICORN_ACCESS_LOGGER = "uvicorn.access"
HEALTH_PATH = "/health"
READY_PATH = "/ready"
METRICS_PATH = "/metrics"
PROBE_PATHS = frozenset({HEALTH_PATH, READY_PATH, METRICS_PATH})
FIRST_ERROR_STATUS = 400


class JsonFormatter(logging.Formatter):
    """Un objeto JSON por registro. El request id es el que trae el registro o, si no trae
    ninguno, el de la petición en curso."""

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
    """Manda todos los logs del proceso, en JSON, a la salida estándar; los de uvicorn
    también, salvo su línea de acceso, que la del kit reemplaza.

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


def log_request(
    service: str, method: str, path: str, status: int, request_id: str, duration_ms: float
) -> None:
    """La línea de acceso de una petición respondida. Las sondas que responden bien van en
    DEBUG: el healthcheck de Docker y Prometheus llaman cada pocos segundos y taparían lo
    demás."""
    logging.getLogger(ACCESS_LOGGER).log(
        _access_level(path, status),
        "%s %s %d",
        method,
        path,
        status,
        extra={
            REQUEST_ID_FIELD: request_id,
            "method": method,
            "path": path,
            "status": status,
            "service": service,
            "duration_ms": duration_ms,
        },
    )


def _access_level(path: str, status: int) -> int:
    if path in PROBE_PATHS and status < FIRST_ERROR_STATUS:
        return logging.DEBUG
    return logging.INFO


def _request_id_of(record: logging.LogRecord) -> str | None:
    stamped = getattr(record, REQUEST_ID_FIELD, None)
    return stamped if isinstance(stamped, str) else current_request_id()
