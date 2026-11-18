"""Carga masiva por `POST /tables/{name}/rows`: NDJSON por partes, lotes que entran enteros
o no entran, índices al día tras un lote rechazado, y una carga que se corta a la mitad."""

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import Role, ServiceKey, digest_of
from api.ingest import Line, LineBatcher
from api.main import create_app
from api.service import Service
from config import EngineConfig
from query.engine import Engine
from query.table import Table
from storage.types import StorageError

BATCH_ROWS = 4
LINE_LIMIT = 512
SCHEMA = (
    "id INT PRIMARY KEY, nombre VARCHAR(12) NOT NULL, grupo INT INDEX HASH, emb VECTOR(3), "
    "lugar POINT"
)
INDEXES = (
    "CREATE INDEX idx_t_emb ON t USING HNSW (emb)",
    "CREATE INDEX idx_t_lugar ON t USING RTREE (lugar)",
)
GROUPS = 3
WAIT_STEP = 0.01
WAIT_LIMIT = 5.0
EXPIRED_SECONDS = 1e-9
WRITER_KEY = "clave-de-escritura-0123456789abcdef"
READER_KEY = "clave-de-lectura-0123456789abcdef"
SERVICE_KEYS = (
    ServiceKey("cargador", Role.WRITE, digest_of(WRITER_KEY)),
    ServiceKey("lector", Role.READ, digest_of(READER_KEY)),
)
FAILING_ID = 7
WHOLE_TABLE_RADIUS = 1e7
ROWS_PATH = "/tables/t/rows"


def row(key: int, **changes: Any) -> dict[str, Any]:
    values = {
        "id": key,
        "nombre": f"fila {key}",
        "grupo": key % GROUPS,
        "emb": [1.0, key / 8, 0.5],
        "lugar": {"lat": -12 + key / 100, "lon": -77 + key / 100},
    }
    return {**values, **changes}


def ndjson(*items: dict[str, Any] | str) -> bytes:
    return "".join(
        f"{item if isinstance(item, str) else json.dumps(item)}\n" for item in items
    ).encode()


@pytest.fixture
def ingest_config(config: EngineConfig) -> EngineConfig:
    return replace(config, ingest_batch_rows=BATCH_ROWS, max_ingest_line_bytes=LINE_LIMIT)


@pytest.fixture
def client(ingest_config: EngineConfig) -> Iterator[TestClient]:
    with TestClient(create_app(ingest_config)) as instance:
        create_table(instance)
        yield instance


def create_table(client: TestClient) -> None:
    for sql in (f"CREATE TABLE t ({SCHEMA})", *INDEXES):
        assert client.post("/query", json={"sql": sql}).status_code == 200


def sql(client: TestClient, statement: str) -> list[list[Any]]:
    response = client.post("/query", json={"sql": statement})
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def post_rows(client: TestClient, body: bytes, **headers: str) -> httpx.Response:
    return client.post(ROWS_PATH, content=body, headers=headers)


def loaded(client: TestClient, body: bytes) -> dict[str, Any]:
    response = post_rows(client, body)
    assert response.status_code == 200, response.text
    return response.json()


def ids(client: TestClient) -> list[int]:
    return [key for (key,) in sql(client, "SELECT id FROM t ORDER BY id")]


def ids_through_indexes(client: TestClient) -> dict[str, list[int]]:
    """Las filas que encuentra cada índice: si un lote deshecho dejara una entrada, o un
    lote confirmado faltara, alguna lista no coincidiría con la tabla."""
    count = len(ids(client))
    nearest = sql(client, f"SELECT id FROM t WHERE SIMILAR_TO(emb, [1, 0.5, 0.5], k = {count + 1})")
    radius = sql(
        client,
        f"SELECT id FROM t WHERE distancia(lugar, POINT(-12, -77)) < {WHOLE_TABLE_RADIUS}",
    )
    groups = [
        key
        for group in range(GROUPS)
        for (key,) in sql(client, f"SELECT id FROM t WHERE grupo = {group}")
    ]
    return {
        "hnsw": sorted(key for (key,) in nearest),
        "rtree": sorted(key for (key,) in radius),
        "hash": sorted(groups),
    }


def assert_indexes_match_the_table(client: TestClient) -> None:
    table = ids(client)
    assert ids_through_indexes(client) == {"hnsw": table, "rtree": table, "hash": table}


def test_the_batcher_numbers_lines_across_chunks():
    batcher = LineBatcher(LINE_LIMIT, batch_rows=2)
    assert batcher.feed(b'{"a": 1}\r\n\n{"a"') == []
    (first,) = batcher.feed(b': 2}\n  \n{"a": 3}')
    assert first == [Line(1, b'{"a": 1}'), Line(3, b'{"a": 2}')]
    assert batcher.pending == 0
    assert batcher.finish() == [[Line(5, b'{"a": 3}')]]
    assert batcher.lines_read == 5


def test_the_batcher_drops_an_oversize_line_without_keeping_it():
    batcher = LineBatcher(line_limit=8, batch_rows=10)
    batcher.feed(b'{"a": 1}\n{"texto": "')
    batcher.feed(b"x" * 1000)
    batcher.feed(b'"}\n{"b": 2}')
    assert batcher.finish() == [[Line(1, b'{"a": 1}'), Line(2, None), Line(3, b'{"b": 2}')]]


def test_rows_are_loaded_in_batches(client: TestClient):
    summary = loaded(client, ndjson(*(row(key) for key in range(1, 11))))
    assert summary["rows_loaded"] == 10
    assert summary["batches_committed"] == 3
    assert summary["rejected"] == []
    assert summary["last_line"] == 10
    assert summary["interrupted"] is None
    assert sql(client, "SELECT emb, lugar FROM t WHERE id = 2") == [
        [[1.0, 0.25, 0.5], {"lat": -11.98, "lon": -76.98}]
    ]
    assert_indexes_match_the_table(client)


def test_an_empty_body_loads_nothing(client: TestClient):
    summary = loaded(client, b"")
    assert (summary["rows_loaded"], summary["batches_committed"], summary["last_line"]) == (0, 0, 0)
    assert loaded(client, b"\n \n")["last_line"] == 0


@pytest.mark.parametrize(
    ("bad_line", "reason"),
    [
        ('{"id": 5, "nombre": ', "no es JSON válido"),
        ('[5, "cinco"]', "una fila es un objeto"),
        (json.dumps(row(5, id="cinco")), "entero"),
        (json.dumps(row(5, color="rojo")), "la tabla no tiene la columna 'color'"),
        (json.dumps(row(5, emb=[1, 2])), "3"),
        (json.dumps({"id": 5, "grupo": 1}), "nombre"),
        (json.dumps({"nombre": "sin clave"}), "id"),
        (json.dumps(row(5, lugar={"lat": 1, "lon": 2, "alt": 3})), "solo se admite un punto"),
        (json.dumps(row(1)), "la clave primaria 1 ya existe"),
    ],
)
def test_a_bad_line_rejects_its_batch_and_the_load_goes_on(
    client: TestClient, bad_line: str, reason: str
):
    body = ndjson(*(row(key) for key in range(1, 5)), row(6), bad_line, row(7), row(8), row(9))
    summary = loaded(client, body)
    (rejected,) = summary["rejected"]
    assert rejected["first_line"] == 5
    assert rejected["last_line"] == 8
    assert rejected["line"] == 6
    assert reason in rejected["reason"]
    assert (summary["rows_loaded"], summary["rows_rejected"]) == (5, 4)
    assert (summary["batches_committed"], summary["batches_rejected"]) == (2, 1)
    assert ids(client) == [1, 2, 3, 4, 9]
    assert_indexes_match_the_table(client)


@pytest.mark.parametrize("starts_empty", [True, False], ids=["diferida", "fila_a_fila"])
def test_a_batch_that_fails_while_writing_is_undone_with_its_index_entries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, starts_empty: bool
):
    """La fila 7 falla cuando las 5 y 6 de su lote ya están en el heap y en sus índices:
    el lote se deshace entero y los índices quedan como la tabla."""
    if not starts_empty:
        loaded(client, ndjson(row(100)))
    write = Table._store

    def failing_write(table: Table, values: tuple[Any, ...]) -> None:
        if values[0] == FAILING_ID:
            raise StorageError("el disco se llenó")
        write(table, values)

    monkeypatch.setattr(Table, "_store", failing_write)
    summary = loaded(client, ndjson(*(row(key) for key in range(1, 13))))
    (rejected,) = summary["rejected"]
    assert (rejected["first_line"], rejected["line"], rejected["reason"]) == (
        5,
        None,
        "el disco se llenó",
    )
    assert bool(summary["deferred_indexes"]) == starts_empty
    expected = [1, 2, 3, 4, 9, 10, 11, 12] + ([] if starts_empty else [100])
    assert ids(client) == expected
    assert_indexes_match_the_table(client)


def test_an_empty_table_builds_its_indexes_at_the_end_like_one_by_one(
    ingest_config: EngineConfig, client: TestClient, tmp_path
):
    """La misma carga, con la tabla vacía (índices diferidos) y con una fila previa
    (índices fila a fila), responde igual por cada índice."""
    body = ndjson(*(row(key) for key in range(1, 30)))
    deferred = loaded(client, body)
    assert deferred["deferred_indexes"] == ["idx_t_emb", "idx_t_lugar"]
    other = replace(ingest_config, data_directory=tmp_path / "fila_a_fila")
    with TestClient(create_app(other)) as incremental:
        create_table(incremental)
        loaded(incremental, ndjson(row(100)))
        assert loaded(incremental, body)["deferred_indexes"] == []
        sql(incremental, "DELETE FROM t WHERE id = 100")
        assert ids_through_indexes(incremental) == ids_through_indexes(client)


def test_a_reader_cannot_load_rows(ingest_config: EngineConfig):
    with TestClient(create_app(ingest_config, service_keys=SERVICE_KEYS)) as client:
        reader = post_rows(client, ndjson(row(1)), Authorization=f"Bearer {READER_KEY}")
        assert reader.status_code == 403
        assert post_rows(client, ndjson(row(1))).status_code == 401
        writer = post_rows(client, b"", Authorization=f"Bearer {WRITER_KEY}")
        assert writer.status_code == 400, "con la tabla sin crear, la clave sí alcanza"


def test_an_unknown_table_is_a_bad_request(client: TestClient):
    response = client.post("/tables/fantasma/rows", content=ndjson(row(1)))
    assert response.status_code == 400
    assert response.json()["detail"]["kind"] == "UnknownTableError"


def test_an_oversize_line_rejects_its_batch_and_the_body_has_no_total_limit(
    ingest_config: EngineConfig,
):
    limited = replace(ingest_config, max_request_bytes=LINE_LIMIT)
    with TestClient(create_app(limited)) as client:
        create_table(client)
        long_name = row(3, nombre="x" * LINE_LIMIT)
        body = ndjson(*(row(key) for key in (1, 2)), long_name, *(row(key) for key in range(4, 40)))
        assert len(body) > limited.max_request_bytes
        summary = loaded(client, body)
        (rejected,) = summary["rejected"]
        assert rejected["line"] == 3
        assert "max_ingest_line_bytes" in rejected["reason"]
        assert summary["rows_loaded"] == 39 - BATCH_ROWS


def test_a_batch_past_its_deadline_is_rejected_whole(ingest_config: EngineConfig):
    hurried = replace(ingest_config, statement_timeout_seconds=EXPIRED_SECONDS)
    with TestClient(create_app(hurried)) as client:
        create_table(client)
        summary = loaded(client, ndjson(row(1), row(2)))
    (rejected,) = summary["rejected"]
    assert rejected["line"] is None
    assert "statement_timeout" in rejected["reason"]


def test_an_empty_table_stays_locked_while_its_indexes_are_deferred(
    ingest_config: EngineConfig,
):
    app = create_app(replace(ingest_config, lock_timeout_seconds=WAIT_STEP))
    service: Service = app.state.service
    client = without_lifespan(app)
    create_table(client)
    readers: list[int] = []

    def read_meanwhile(chunk: int) -> None:
        if chunk == 1:
            readers.append(client.post("/query", json={"sql": "SELECT * FROM t"}).status_code)

    chunks = [ndjson(*(row(key) for key in range(1, 5))), ndjson(row(5))]
    status, summary = exchange(app, ROWS_PATH, chunks, before_chunk=read_meanwhile)
    assert (status, summary["rows_loaded"]) == (200, 5)
    assert readers == [400], "la lectura no tiene que ver la tabla con sus índices a medias"
    service.close()


def test_a_client_that_disconnects_keeps_its_committed_batches(
    ingest_config: EngineConfig, caplog: pytest.LogCaptureFixture
):
    app = create_app(ingest_config)
    create_table(without_lifespan(app))
    body = ndjson(*(row(key) for key in range(1, 7)))
    # La sexta línea queda a medias: el cliente se va sin terminarla.
    with caplog.at_level(logging.WARNING, logger="api.ingest"):
        status, summary = exchange(app, ROWS_PATH, [body[:-5]], disconnect=True)
    assert status == 200
    assert summary["rows_loaded"] == BATCH_ROWS
    assert summary["interrupted"] == "el cliente cortó la conexión tras la línea 5"
    assert "se descartan 1 fila(s) del lote en curso" in caplog.text
    app.state.service.close()
    with Engine(ingest_config) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM t").rows == ((BATCH_ROWS,),)
        nearest = reopened.execute("SELECT id FROM t WHERE SIMILAR_TO(emb, [1, 0, 0], k = 10)")
        assert sorted(key for (key,) in nearest.rows) == [1, 2, 3, 4]


def test_a_server_shutting_down_stops_the_load_after_its_last_batch(ingest_config: EngineConfig):
    app = create_app(ingest_config)
    service: Service = app.state.service
    client = without_lifespan(app)
    create_table(client)
    loaded(client, ndjson(row(100)))
    closing: list[threading.Thread] = []

    def shut_down_before_the_second_batch(chunk: int) -> None:
        if chunk == 1:
            closing.append(threading.Thread(target=service.close))
            closing[0].start()
            wait_until(lambda: not service.is_ready())

    chunks = [
        ndjson(*(row(key) for key in range(1, 5))),
        ndjson(*(row(key) for key in range(5, 9))),
    ]
    status, summary = exchange(
        app, ROWS_PATH, chunks, before_chunk=shut_down_before_the_second_batch
    )
    closing[0].join()
    assert status == 200
    assert (summary["rows_loaded"], summary["last_line"]) == (BATCH_ROWS, BATCH_ROWS)
    assert "se está apagando" in summary["interrupted"]
    with Engine(ingest_config) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM t").rows == ((BATCH_ROWS + 1,),)


def without_lifespan(app: FastAPI) -> TestClient:
    """Un cliente que no arranca ni apaga la aplicación: con `with`, al salir apagaría el
    servicio que la petición hecha a mano necesita abierto."""
    return TestClient(app)


def wait_until(condition: Callable[[], bool]) -> None:
    waited = 0.0
    while not condition() and waited < WAIT_LIMIT:
        time.sleep(WAIT_STEP)
        waited += WAIT_STEP
    assert condition()


@dataclass
class _Exchange:
    status: int = 0
    body: bytearray | None = None


def exchange(
    app: FastAPI,
    path: str,
    chunks: Sequence[bytes],
    disconnect: bool = False,
    before_chunk: Callable[[int], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Una petición hecha a mano contra la aplicación ASGI, con el cuerpo en `chunks`.

    El cliente de pruebas lee el cuerpo entero antes de entregarlo; aquí cada trozo llega
    por separado, `before_chunk` corre antes de entregar cada uno y, con `disconnect`, el
    cliente se va en lugar de terminar el cuerpo, como un proceso que muere a mitad.
    """
    return asyncio.run(_exchange(app, path, chunks, disconnect, before_chunk))


async def _exchange(
    app: FastAPI,
    path: str,
    chunks: Sequence[bytes],
    disconnect: bool,
    before_chunk: Callable[[int], None] | None,
) -> tuple[int, dict[str, Any]]:
    answered = asyncio.Event()
    outcome = _Exchange(body=bytearray())
    delivered = 0

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered < len(chunks):
            if before_chunk is not None:
                before_chunk(delivered)
            delivered += 1
            more = disconnect or delivered < len(chunks)
            return {"type": "http.request", "body": chunks[delivered - 1], "more_body": more}
        if not disconnect:
            await answered.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            outcome.status = message["status"]
        elif message["type"] == "http.response.body" and outcome.body is not None:
            outcome.body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                answered.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/x-ndjson")],
        "client": ("cliente", 1),
        "server": ("gestor", 80),
    }
    await app(scope, receive, send)
    return outcome.status, json.loads(bytes(outcome.body or b"{}"))
