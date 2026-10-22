"""Configuración del servidor leída del entorno."""

from pathlib import Path

import pytest

from api.settings import (
    CORS_ORIGINS_VARIABLE,
    DATA_DIRECTORY_VARIABLE,
    IMPORT_DIRECTORIES_VARIABLE,
    LOCK_TIMEOUT_VARIABLE,
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
