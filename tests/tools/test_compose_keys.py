"""Las claves del compose: una por servicio y rol, conservadas al repetir, y el gestor que
las acepta tal como quedan en `.env`."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from tools.compose_keys import (
    APPS,
    CONSOLE_KEY_VARIABLE,
    ENGINE_KEYS_VARIABLE,
    ComposeKeysError,
    key_slots,
    main,
    parse_service,
    read_assignments,
    update_env,
)

from api.auth import Role
from api.settings import settings_from_environment

SLOTS = key_slots({"shop": "SHOP_"})


def test_an_empty_file_gets_every_key_and_the_engine_list():
    values = read_assignments(update_env([], SLOTS))
    assert set(values) == {
        CONSOLE_KEY_VARIABLE, "SHOP_ENGINE_KEY", "SHOP_MIGRATION_KEY", ENGINE_KEYS_VARIABLE
    }
    entries = values[ENGINE_KEYS_VARIABLE].split(",")
    assert [entry.rsplit(":", 1)[0] for entry in entries] == [
        "consola:admin", "shop:write", "shop-migraciones:admin"
    ]
    shop_digest = hashlib.sha256(values["SHOP_ENGINE_KEY"].encode()).hexdigest()
    assert entries[1] == f"shop:write:{shop_digest}"


def test_running_it_again_keeps_the_keys_and_the_other_lines():
    first = update_env(["# comentario", "OTRA=1", "SHOP_ENGINE_KEY=fija"], SLOTS)
    second = update_env(first, SLOTS)
    assert second == first
    assert first[:2] == ["# comentario", "OTRA=1"]
    assert read_assignments(first)["SHOP_ENGINE_KEY"] == "fija"


def test_an_empty_key_is_replaced():
    values = read_assignments(update_env([f"{CONSOLE_KEY_VARIABLE}="], SLOTS))
    assert values[CONSOLE_KEY_VARIABLE]


def test_every_app_of_the_compose_has_both_keys():
    variables = {slot.variable for slot in key_slots(APPS)}
    for prefix in APPS.values():
        assert {prefix + "ENGINE_KEY", prefix + "MIGRATION_KEY"} <= variables


@pytest.mark.parametrize("text", ["ejemplo", "ejemplo:EJEMPLO", ":EJEMPLO_"])
def test_a_malformed_service_is_rejected(text: str):
    with pytest.raises(ComposeKeysError):
        parse_service(text)


def test_the_engine_accepts_the_written_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_file = tmp_path / ".env"
    main(["--env-file", str(env_file), "--service", "ejemplo:EJEMPLO_"])
    values = read_assignments(env_file.read_text().splitlines())
    assert env_file.stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv(ENGINE_KEYS_VARIABLE, values[ENGINE_KEYS_VARIABLE])
    monkeypatch.setenv("MINIGESTOR_CORS_ORIGINS", "http://localhost")
    keys = {key.service: key for key in settings_from_environment().service_keys}
    assert keys["ejemplo"].role is Role.WRITE
    assert keys["ejemplo-migraciones"].role is Role.ADMIN
    assert keys["consola"].digest == hashlib.sha256(values[CONSOLE_KEY_VARIABLE].encode()).hexdigest()
