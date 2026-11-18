"""Cada error del gestor llega al cliente como su excepción, con el `X-Request-ID`."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest
from fastapi import FastAPI
from tests.client.engine_server import serving

from api.auth import Role, ServiceKey, digest_of
from api.main import create_app
from config import EngineConfig
from minigestor_client import (
    AuthenticationError,
    Client,
    ConflictError,
    DefinitionMismatchError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    ServiceUnavailableError,
    SqlSyntaxError,
    StatementTimeoutError,
    request_id_scope,
)

READ_KEY = "clave-de-lectura-0123456789abcdef"
WRITE_KEY = "clave-de-escritura-0123456789abcdef"
ROWS = 1_500
# Sin cortar, el producto de 1 500 filas tarda varios segundos.
SLOW_SQL = "SELECT COUNT(*) FROM grande a JOIN grande b ON a.id < b.id"
STATEMENT_TIMEOUT_SECONDS = 0.2


@pytest.fixture
def numbers(client: Client) -> Client:
    client.execute("CREATE TABLE numeros (id INT PRIMARY KEY, nombre VARCHAR(20))")
    client.execute("INSERT INTO numeros VALUES ($1, $2)", [1, "uno"])
    return client


@pytest.fixture
def keyed_url(engine_config: EngineConfig) -> Iterator[str]:
    keys = (
        ServiceKey("lector", Role.READ, digest_of(READ_KEY)),
        ServiceKey("escritor", Role.WRITE, digest_of(WRITE_KEY)),
    )
    with serving(create_app(engine_config, service_keys=keys)) as url:
        yield url


def test_a_syntax_error_carries_its_position_and_cursor(client: Client):
    sql = "SELECT *\nFROM WHERE"
    with pytest.raises(SqlSyntaxError) as raised:
        client.query(sql)
    error = raised.value
    assert (error.status, error.kind) == (400, "SqlSyntaxError")
    assert (error.line, error.column) == (2, 6)
    assert error.cursor == "FROM WHERE\n     ^"


def test_an_unknown_table_is_not_found(client: Client):
    with pytest.raises(NotFoundError) as raised:
        client.query("SELECT * FROM fantasma")
    assert raised.value.kind == "UnknownTableError"


def test_a_repeated_key_is_a_conflict(numbers: Client):
    with pytest.raises(ConflictError) as raised:
        numbers.execute("INSERT INTO numeros VALUES ($1, $2)", [1, "otra vez"])
    assert raised.value.kind == "DuplicatePrimaryKeyError"


def test_a_migration_against_another_definition_is_a_mismatch(numbers: Client):
    numbers.execute("CREATE TABLE IF NOT EXISTS numeros (id INT PRIMARY KEY, nombre VARCHAR(20))")
    with pytest.raises(DefinitionMismatchError):
        numbers.execute(
            "CREATE TABLE IF NOT EXISTS numeros (id INT PRIMARY KEY, nombre VARCHAR(30))"
        )


def test_a_parameter_of_the_wrong_type_is_a_domain_error(numbers: Client):
    with pytest.raises(DomainError) as raised:
        numbers.query("SELECT * FROM numeros WHERE id = $1", ["uno"])
    assert type(raised.value) is DomainError


def test_a_slow_statement_is_cut_by_the_engine(client: Client, engine_app: FastAPI):
    client.execute("CREATE TABLE grande (id INT PRIMARY KEY)")
    client.bulk_insert("grande", ({"id": number} for number in range(ROWS)))
    service = engine_app.state.service
    service.config = replace(service.config, statement_timeout_seconds=STATEMENT_TIMEOUT_SECONDS)
    with pytest.raises(StatementTimeoutError):
        client.query(SLOW_SQL)


def test_a_stopping_engine_is_unavailable(client: Client, engine_app: FastAPI):
    engine_app.state.service.close()
    assert client.ready() is False
    with pytest.raises(ServiceUnavailableError) as raised:
        client.query("SELECT * FROM t")
    assert raised.value.status == 503


@pytest.mark.parametrize("key", [None, "una-clave-que-no-es-de-nadie"])
def test_without_a_valid_key_the_engine_refuses(keyed_url: str, key: str | None):
    with Client(keyed_url, key) as stranger, pytest.raises(AuthenticationError) as raised:
        stranger.tables()
    assert raised.value.status == 401


def test_a_role_that_falls_short_is_denied(keyed_url: str):
    with Client(keyed_url, WRITE_KEY) as writer, pytest.raises(PermissionDeniedError) as raised:
        writer.execute("CREATE TABLE t (id INT PRIMARY KEY)")
    assert raised.value.status == 403


def test_a_role_that_reaches_is_served(keyed_url: str):
    with Client(keyed_url, READ_KEY) as reader:
        assert reader.tables() == []


def test_every_error_carries_the_request_id(client: Client):
    with request_id_scope("pedido-con-error"), pytest.raises(NotFoundError) as raised:
        client.query("SELECT * FROM fantasma")
    assert raised.value.request_id == "pedido-con-error"
