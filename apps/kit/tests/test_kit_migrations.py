"""Migraciones repetibles: solo `CREATE ... IF NOT EXISTS`, en orden, y un arranque que falla
fuerte si el gestor tiene otra definición."""

from __future__ import annotations

import pytest
from apps.kit.tests.kit_example import SCHEMA, ExampleSettings, create_example_app
from apps.kit.tests.kit_fakes import ENGINE_URL, FakeEngine
from fastapi.testclient import TestClient

from appkit import (
    MigrationError,
    NonRepeatableMigrationError,
    create_service_app,
    load_settings,
    migrate,
)
from appkit.migrations import check_repeatable
from minigestor_client import AsyncClient

MIGRATION_KEY = "clave-de-migraciones"


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE IF NOT EXISTS t (id INT PRIMARY KEY)",
        "  create index if not exists i ON t USING HASH (id)",
        "CREATE\n  TABLE IF NOT EXISTS t (id INT PRIMARY KEY)",
    ],
)
def test_create_if_not_exists_is_repeatable(sql: str):
    check_repeatable([sql])


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t (id INT PRIMARY KEY)",
        "DROP TABLE IF EXISTS t",
        "INSERT INTO t VALUES (1)",
        "CREATE INDEX i ON t USING HASH (id)",
    ],
)
def test_anything_else_is_refused_before_starting(sql: str, settings: ExampleSettings):
    with pytest.raises(NonRepeatableMigrationError, match="migración 2"):
        create_service_app(settings, name="x", version="0", migrations=[SCHEMA[0], sql])


@pytest.mark.anyio
async def test_statements_run_in_order(engine: FakeEngine):
    async with AsyncClient(ENGINE_URL, transport=engine.transport()) as client:
        await migrate(client, SCHEMA)
    assert [sql for sql, _ in engine.queries()] == list(SCHEMA)


def test_the_service_migrates_with_its_own_key_when_there_is_no_other(service, engine: FakeEngine):
    assert engine.queries() == [(sql, "Bearer clave-de-la-app") for sql in SCHEMA]


def test_a_migration_key_is_used_only_for_the_schema(environment, engine: FakeEngine):
    environment.setenv("EJEMPLO_MIGRATION_KEY", MIGRATION_KEY)
    with TestClient(
        create_example_app(load_settings(ExampleSettings), engine.transport())
    ) as client:
        client.get("/notas/1")
    keys = [key for _, key in engine.queries()]
    assert keys == [f"Bearer {MIGRATION_KEY}"] * len(SCHEMA) + ["Bearer clave-de-la-app"]


@pytest.mark.parametrize(
    ("status", "kind", "cause"),
    [
        (400, "DefinitionMismatchError", "otra definición"),
        (403, "PermissionError", "MIGRATION_KEY"),
    ],
)
def test_a_rejected_migration_stops_the_start(settings, engine: FakeEngine, status, kind, cause):
    engine.failures["CREATE INDEX"] = (status, kind)
    with (
        pytest.raises(MigrationError, match=cause) as raised,
        TestClient(create_example_app(settings, engine.transport())),
    ):
        pass
    assert "la migración 2 falló" in str(raised.value)
