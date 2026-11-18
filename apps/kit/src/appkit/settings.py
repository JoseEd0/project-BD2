"""La configuración de un servicio, leída de variables de entorno con su prefijo.

Cada servicio declara la suya heredando de `ServiceSettings` y fijando su prefijo:

    class ShopSettings(ServiceSettings):
        model_config = SettingsConfigDict(env_prefix="SHOP_")
        recommendations: int = 10

y la lee al arrancar con `load_settings(ShopSettings)`, que falla con un mensaje que nombra
cada variable que falta o no vale. Así un contenedor mal configurado no llega a atender.
"""

from __future__ import annotations

from typing import Literal, TypeVar

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from minigestor_client import PoolLimits, RetryPolicy, Timeouts
from minigestor_client.options import (
    DEFAULT_ATTEMPTS,
    DEFAULT_CONNECT_SECONDS,
    DEFAULT_LOAD_SECONDS,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_READ_SECONDS,
)

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
DEFAULT_LOG_LEVEL: LogLevel = "INFO"
# Una sonda de Docker cada pocos segundos por réplica no debe convertirse en una consulta al
# gestor por sonda; dos segundos de caché las reducen sin esconder una caída más que eso.
DEFAULT_READY_CACHE_SECONDS = 2.0
# La sonda de Docker tiene su propio plazo; la pregunta al gestor tiene que responder antes.
DEFAULT_READY_TIMEOUT_SECONDS = 2.0
MISSING = "missing"


class SettingsError(Exception):
    """La configuración del entorno está incompleta o tiene valores que no sirven."""


class ServiceSettings(BaseSettings):
    """Lo que necesita todo servicio para hablar con el gestor y operar.

    Attributes:
        engine_url: la dirección del API del gestor, por ejemplo `http://engine:8000`.
        engine_key: la clave del servicio en el gestor (`Authorization: Bearer`).
        migration_key: la clave con que corren las migraciones al arrancar. El DDL pide rol
            `admin`; con ella, la clave de cada petición puede quedarse en `read` o `write`.
            Sin ella, las migraciones usan `engine_key`, que entonces necesita `admin`.
        log_level: nivel de los logs JSON.
        engine_connect_timeout_seconds, engine_read_timeout_seconds,
        engine_load_timeout_seconds: plazos de conectar, de esperar una respuesta y de una
            carga masiva.
        engine_retry_attempts: intentos de una lectura que no recibe respuesta.
        engine_max_connections: conexiones abiertas a la vez con el gestor por réplica.
        ready_cache_seconds: cuánto vale la última respuesta del gestor a `/ready`.
        ready_timeout_seconds: plazo de esa pregunta.
    """

    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    engine_url: AnyHttpUrl
    engine_key: SecretStr
    migration_key: SecretStr | None = None
    log_level: LogLevel = DEFAULT_LOG_LEVEL
    engine_connect_timeout_seconds: float = Field(default=DEFAULT_CONNECT_SECONDS, gt=0)
    engine_read_timeout_seconds: float = Field(default=DEFAULT_READ_SECONDS, gt=0)
    engine_load_timeout_seconds: float = Field(default=DEFAULT_LOAD_SECONDS, gt=0)
    engine_retry_attempts: int = Field(default=DEFAULT_ATTEMPTS, ge=1)
    engine_max_connections: int = Field(default=DEFAULT_MAX_CONNECTIONS, ge=1)
    ready_cache_seconds: float = Field(default=DEFAULT_READY_CACHE_SECONDS, ge=0)
    ready_timeout_seconds: float = Field(default=DEFAULT_READY_TIMEOUT_SECONDS, gt=0)

    @field_validator("log_level", mode="before")
    @classmethod
    def uppercase_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @property
    def engine_base_url(self) -> str:
        return str(self.engine_url).rstrip("/")

    def timeouts(self) -> Timeouts:
        return Timeouts(
            connect_seconds=self.engine_connect_timeout_seconds,
            read_seconds=self.engine_read_timeout_seconds,
            write_seconds=self.engine_read_timeout_seconds,
            load_seconds=self.engine_load_timeout_seconds,
        )

    def retry(self) -> RetryPolicy:
        return RetryPolicy(attempts=self.engine_retry_attempts)

    def pool(self) -> PoolLimits:
        return PoolLimits(
            max_connections=self.engine_max_connections,
            max_keepalive_connections=self.engine_max_connections,
        )


Settings = TypeVar("Settings", bound=ServiceSettings)


def load_settings(settings_class: type[Settings]) -> Settings:
    """La configuración de `settings_class` leída del entorno.

    Raises:
        SettingsError: si la clase no fija `env_prefix` o si falta o no vale alguna variable;
            el mensaje las nombra todas, con su prefijo.
    """
    prefix = settings_class.model_config.get("env_prefix", "")
    if not prefix:
        raise SettingsError(f"{settings_class.__name__} tiene que fijar env_prefix")
    try:
        # Los campos obligatorios los llena pydantic-settings con el entorno; mypy solo ve el
        # constructor del modelo, que los pide como argumentos.
        return settings_class()  # type: ignore[call-arg]
    except ValidationError as error:
        raise SettingsError(_describe(settings_class, prefix, error)) from error


def _describe(settings_class: type[ServiceSettings], prefix: str, error: ValidationError) -> str:
    problems = []
    for problem in error.errors():
        variable = f"{prefix}{problem['loc'][0]}".upper() if problem["loc"] else prefix
        if problem["type"] == MISSING:
            problems.append(f"falta {variable}")
        else:
            problems.append(f"{variable}: {problem['msg']}")
    return f"configuración de {settings_class.__name__} inválida: " + "; ".join(problems)
