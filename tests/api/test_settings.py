"""Configuración del servidor leída del entorno."""

from pathlib import Path

import pytest

from api.auth import Role, digest_of
from api.settings import (
    CORS_ORIGINS_VARIABLE,
    DATA_DIRECTORY_VARIABLE,
    IMPORT_DIRECTORIES_VARIABLE,
    LOCK_TIMEOUT_VARIABLE,
    LOG_LEVEL_VARIABLE,
    MAX_REQUEST_BYTES_VARIABLE,
    SERVICE_KEYS_VARIABLE,
    STATEMENT_TIMEOUT_VARIABLE,
    SettingsError,
    settings_from_environment,
)

ORIGINS = "http://localhost:5173, http://127.0.0.1:5173"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        CORS_ORIGINS_VARIABLE,
        DATA_DIRECTORY_VARIABLE,
        IMPORT_DIRECTORIES_VARIABLE,
        LOCK_TIMEOUT_VARIABLE,
        LOG_LEVEL_VARIABLE,
        MAX_REQUEST_BYTES_VARIABLE,
        SERVICE_KEYS_VARIABLE,
        STATEMENT_TIMEOUT_VARIABLE,
    ):
        monkeypatch.delenv(variable, raising=False)


def test_the_cors_origins_are_required():
    with pytest.raises(SettingsError, match=CORS_ORIGINS_VARIABLE):
        settings_from_environment()


def test_lists_are_split_and_trimmed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(CORS_ORIGINS_VARIABLE, ORIGINS)
    monkeypatch.setenv(IMPORT_DIRECTORIES_VARIABLE, f"{tmp_path}/a, {tmp_path}/b")
    settings = settings_from_environment()
    assert settings.cors_origins == ("http://localhost:5173", "http://127.0.0.1:5173")
    assert settings.config.import_directories == (tmp_path / "a", tmp_path / "b")


def test_the_data_directory_and_lock_timeout_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv(CORS_ORIGINS_VARIABLE, ORIGINS)
    monkeypatch.setenv(DATA_DIRECTORY_VARIABLE, str(tmp_path))
    monkeypatch.setenv(LOCK_TIMEOUT_VARIABLE, "30")
    config = settings_from_environment().config
    assert config.data_directory == tmp_path
    assert config.lock_timeout_seconds == 30.0


def test_a_timeout_that_is_not_a_number_is_reported(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(CORS_ORIGINS_VARIABLE, ORIGINS)
    monkeypatch.setenv(LOCK_TIMEOUT_VARIABLE, "cinco")
    with pytest.raises(SettingsError, match=LOCK_TIMEOUT_VARIABLE):
        settings_from_environment()


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_a_timeout_that_is_not_finite_is_reported(monkeypatch: pytest.MonkeyPatch, raw: str):
    """Un plazo infinito dejaría a una transacción esperando un bloqueo para siempre."""
    monkeypatch.setenv(CORS_ORIGINS_VARIABLE, ORIGINS)
    monkeypatch.setenv(LOCK_TIMEOUT_VARIABLE, raw)
    with pytest.raises(SettingsError, match="finito"):
        settings_from_environment()


def test_an_empty_data_directory_is_reported(monkeypatch: pytest.MonkeyPatch):
    """`Path("")` es el directorio actual: la base acabaría mezclada con el código."""
    monkeypatch.setenv(CORS_ORIGINS_VARIABLE, ORIGINS)
    monkeypatch.setenv(DATA_DIRECTORY_VARIABLE, "  ")
    with pytest.raises(SettingsError, match=DATA_DIRECTORY_VARIABLE):
        settings_from_environment()


def test_limits_and_log_level_come_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(CORS_ORIGINS_VARIABLE, ORIGINS)
    monkeypatch.setenv(STATEMENT_TIMEOUT_VARIABLE, "2.5")
    monkeypatch.setenv(MAX_REQUEST_BYTES_VARIABLE, "4096")
    monkeypatch.setenv(LOG_LEVEL_VARIABLE, "debug")
    settings = settings_from_environment()
    assert settings.config.statement_timeout_seconds == 2.5
    assert settings.config.max_request_bytes == 4096
    assert settings.log_level == "DEBUG"


def test_an_unknown_log_level_is_reported(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(CORS_ORIGINS_VARIABLE, ORIGINS)
    monkeypatch.setenv(LOG_LEVEL_VARIABLE, "ruidoso")
    with pytest.raises(SettingsError, match=LOG_LEVEL_VARIABLE):
        settings_from_environment()


def test_without_keys_the_api_is_open(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(CORS_ORIGINS_VARIABLE, ORIGINS)
    monkeypatch.setenv(SERVICE_KEYS_VARIABLE, "")
    assert settings_from_environment().service_keys == ()


def test_service_keys_are_read_as_service_role_and_hash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(CORS_ORIGINS_VARIABLE, ORIGINS)
    monkeypatch.setenv(
        SERVICE_KEYS_VARIABLE, f"consola:admin:{digest_of('a')}, ventas : read : {digest_of('b')}"
    )
    keys = settings_from_environment().service_keys
    assert [(key.service, key.role, key.digest) for key in keys] == [
        ("consola", Role.ADMIN, digest_of("a")),
        ("ventas", Role.READ, digest_of("b")),
    ]


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        (f"consola:{digest_of('a')}", "servicio:rol:sha256"),
        (f"consola:root:{digest_of('a')}", "rol desconocido"),
        ("consola:admin:clave-en-claro", "SHA-256"),
        (f"consola:admin:{digest_of('a').upper()}", "SHA-256"),
        (f"con sola:admin:{digest_of('a')}", "nombre de servicio"),
        (f"a:read:{digest_of('x')},a:write:{digest_of('y')}", "repite"),
        (f"a:read:{digest_of('x')},b:write:{digest_of('x')}", "repite"),
    ],
)
def test_a_malformed_service_key_is_reported(
    monkeypatch: pytest.MonkeyPatch, entry: str, reason: str
):
    monkeypatch.setenv(CORS_ORIGINS_VARIABLE, ORIGINS)
    monkeypatch.setenv(SERVICE_KEYS_VARIABLE, entry)
    with pytest.raises(SettingsError, match=reason):
        settings_from_environment()
