"""Lo común de los servicios de la Parte 5: configuración, logs, trazas, salud, errores,
migraciones, caché opcional en Redis y descargas verificadas."""

from appkit.cache import OptionalRedis, RedisFactory, redis_factory
from appkit.engine import Engine, engine_client
from appkit.errors import (
    BadRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServiceError,
)
from appkit.health import Degradable, Readiness, ReadinessProbe
from appkit.logs import configure_logging
from appkit.migrations import MigrationError, NonRepeatableMigrationError, migrate
from appkit.service import create_service_app
from appkit.settings import ServiceSettings, SettingsError, load_settings
from appkit.tracing import record_stage, timed

__all__ = [
    "BadRequestError",
    "Degradable",
    "Engine",
    "MigrationError",
    "NonRepeatableMigrationError",
    "OptionalRedis",
    "Readiness",
    "ReadinessProbe",
    "RedisFactory",
    "ResourceConflictError",
    "ResourceNotFoundError",
    "ServiceError",
    "ServiceSettings",
    "SettingsError",
    "configure_logging",
    "create_service_app",
    "engine_client",
    "load_settings",
    "migrate",
    "record_stage",
    "redis_factory",
    "timed",
]
