"""La configuración de un servicio: valores por omisión y un arranque que falla diciendo qué
variable falta o no vale."""

from __future__ import annotations

import pytest
from apps.kit.tests.kit_example import ENV_PREFIX, ExampleSettings
from apps.kit.tests.kit_fakes import ENGINE_URL

from appkit import ServiceSettings, SettingsError, load_settings


def test_the_defaults_fill_what_is_not_given(settings: ExampleSettings):
    assert settings.engine_base_url == ENGINE_URL
    assert settings.engine_key.get_secret_value() == "clave-de-la-app"
    assert settings.migration_key is None
    assert settings.log_level == "INFO"
    assert settings.retry().attempts == 1
    assert settings.timeouts().connect_seconds == settings.engine_connect_timeout_seconds


def test_the_key_never_shows_in_its_representation(settings: ExampleSettings):
    assert "clave-de-la-app" not in repr(settings)


def test_the_log_level_is_read_in_any_case(environment: pytest.MonkeyPatch):
    environment.setenv(f"{ENV_PREFIX}LOG_LEVEL", "debug")
    assert load_settings(ExampleSettings).log_level == "DEBUG"


def test_every_missing_variable_is_named(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(f"{ENV_PREFIX}ENGINE_URL", raising=False)
    monkeypatch.delenv(f"{ENV_PREFIX}ENGINE_KEY", raising=False)
    with pytest.raises(SettingsError) as raised:
        load_settings(ExampleSettings)
    message = str(raised.value)
    assert "falta EJEMPLO_ENGINE_URL" in message
    assert "falta EJEMPLO_ENGINE_KEY" in message


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("ENGINE_URL", "no-es-una-url"),
        ("LOG_LEVEL", "VERBOSE"),
        ("ENGINE_READ_TIMEOUT_SECONDS", "0"),
        ("ENGINE_RETRY_ATTEMPTS", "cero"),
    ],
)
def test_an_invalid_value_is_named(environment: pytest.MonkeyPatch, variable: str, value: str):
    environment.setenv(f"{ENV_PREFIX}{variable}", value)
    with pytest.raises(SettingsError, match=f"{ENV_PREFIX}{variable}"):
        load_settings(ExampleSettings)


def test_a_service_without_its_own_prefix_is_refused():
    with pytest.raises(SettingsError, match="env_prefix"):
        load_settings(ServiceSettings)
