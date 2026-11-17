"""Claves por servicio: sin clave o con una ajena, `401`; con un rol que no alcanza, `403`."""

from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from api.auth import Role, ServiceKey, digest_of, required_role
from api.main import create_app
from config import EngineConfig
from sql import parse_script

READER_KEY = "clave-de-lectura-0123456789abcdef"
WRITER_KEY = "clave-de-escritura-0123456789abcdef"
ADMIN_KEY = "clave-de-administracion-0123456789abcdef"
SERVICE_KEYS = (
    ServiceKey("catalogo", Role.READ, digest_of(READER_KEY)),
    ServiceKey("ventas", Role.WRITE, digest_of(WRITER_KEY)),
    ServiceKey("consola", Role.ADMIN, digest_of(ADMIN_KEY)),
)
CSV_CONTENT = b"id,nombre\n1,Ana\n"


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def client(config: EngineConfig) -> Iterator[TestClient]:
    with TestClient(create_app(config, service_keys=SERVICE_KEYS)) as instance:
        administrator = query(instance, ADMIN_KEY, "CREATE TABLE t (id INT PRIMARY KEY)")
        assert administrator.status_code == 200, administrator.text
        yield instance


def query(client: TestClient, key: str, sql: str) -> httpx.Response:
    return client.post("/query", json={"sql": sql}, headers=bearer(key))


def test_a_request_without_a_key_is_unauthenticated(client: TestClient):
    response = client.post("/query", json={"sql": "SELECT * FROM t"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"]["kind"] == "AuthenticationError"


@pytest.mark.parametrize("authorization", ["Bearer otra-clave", "Basic abc", "Bearer ", READER_KEY])
def test_a_wrong_key_or_scheme_is_unauthenticated(client: TestClient, authorization: str):
    response = client.get("/tables", headers={"Authorization": authorization})
    assert response.status_code == 401


def test_a_reader_can_query_but_not_write(client: TestClient):
    assert query(client, READER_KEY, "SELECT * FROM t").status_code == 200
    response = query(client, READER_KEY, "INSERT INTO t VALUES (1)")
    assert response.status_code == 403
    assert response.json()["detail"]["kind"] == "PermissionError"
    assert "'catalogo'" in response.json()["detail"]["error"]
    assert query(client, READER_KEY, "SELECT * FROM t").json()["rows"] == []


def test_a_writer_can_change_rows_but_not_definitions(client: TestClient):
    assert query(client, WRITER_KEY, "INSERT INTO t VALUES (1)").status_code == 200
    assert query(client, WRITER_KEY, "UPDATE t SET id = 2 WHERE id = 1").status_code == 200
    assert query(client, WRITER_KEY, "CREATE TABLE u (id INT)").status_code == 403
    assert query(client, WRITER_KEY, "DROP TABLE t").status_code == 403
    assert query(client, WRITER_KEY, "CREATE INDEX i ON t USING HASH (id)").status_code == 403


def test_a_script_is_authorized_as_a_whole_before_running_anything(client: TestClient):
    response = query(client, WRITER_KEY, "INSERT INTO t VALUES (7); DROP TABLE t;")
    assert response.status_code == 403
    assert query(client, READER_KEY, "SELECT * FROM t").json()["rows"] == []


def test_emptying_the_database_and_loading_files_need_an_administrator(client: TestClient):
    files = {"file": ("datos.csv", CSV_CONTENT, "text/csv")}
    assert client.delete("/tables", headers=bearer(WRITER_KEY)).status_code == 403
    for path in ("/files/upload", "/tables/upload"):
        response = client.post(path, files=files, data={"name": "datos"}, headers=bearer(WRITER_KEY))
        assert response.status_code == 403
    response = client.post(
        "/tables/upload", files=files, data={"name": "datos"}, headers=bearer(ADMIN_KEY)
    )
    assert response.status_code == 200
    assert client.delete("/tables", headers=bearer(ADMIN_KEY)).status_code == 200


def test_reading_routes_accept_a_reader(client: TestClient):
    headers = bearer(READER_KEY)
    assert client.get("/tables", headers=headers).status_code == 200
    assert client.get("/tables/t/structure", headers=headers).status_code == 200
    assert client.post("/sessions/s/close", headers=headers).status_code == 200


def test_health_and_readiness_need_no_key(client: TestClient):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_without_keys_the_api_is_open_and_says_so(
    config: EngineConfig, caplog: pytest.LogCaptureFixture
):
    with TestClient(create_app(config)) as open_client:
        assert open_client.post("/query", json={"sql": "CREATE TABLE t (id INT)"}).status_code == 200
        assert open_client.delete("/tables").status_code == 200
    assert any("abierto" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize(
    ("sql", "role"),
    [
        ("SELECT * FROM t", Role.READ),
        ("EXPLAIN ANALYZE SELECT * FROM t", Role.READ),
        ("BEGIN; SELECT * FROM t; COMMIT;", Role.READ),
        ("DELETE FROM t WHERE id = 1", Role.WRITE),
        ("SELECT * FROM t; INSERT INTO t VALUES (1);", Role.WRITE),
        ("CREATE TABLE u (id INT)", Role.ADMIN),
        ("DROP INDEX i", Role.ADMIN),
    ],
)
def test_the_role_comes_from_the_parsed_statements(sql: str, role: Role):
    assert required_role(parse_script(sql)) is role
