import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config import EngineConfig

SESSION = "demo"


@pytest.fixture
def client(config: EngineConfig):
    with TestClient(create_app(config)) as instance:
        yield instance


def run(client: TestClient, sql: str, session: str | None = None) -> dict:
    payload = {"sql": sql} if session is None else {"sql": sql, "session_id": session}
    response = client.post("/query", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_health(client: TestClient):
    assert client.get("/health").json()["status"] == "ok"


def test_tables_are_empty_at_first(client: TestClient):
    assert client.get("/tables").json() == []


def test_create_table_appears_in_the_file_panel(client: TestClient):
    run(client, "CREATE TABLE alumnos (id INT PRIMARY KEY, nombre VARCHAR(20))")
    tables = client.get("/tables").json()
    assert len(tables) == 1
    assert tables[0]["name"] == "alumnos"
    assert tables[0]["organization"] == "heap"
    assert [column["name"] for column in tables[0]["columns"]] == ["id", "nombre"]
    assert tables[0]["columns"][0]["primary_key"]


def test_row_count_is_reported(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY)")
    run(client, "INSERT INTO t VALUES (1), (2)")
    assert client.get("/tables").json()[0]["row_count"] == 2


def test_select_returns_columns_rows_and_plan(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY, v VARCHAR(4))")
    run(client, "INSERT INTO t VALUES (1, 'a')")
    body = run(client, "SELECT * FROM t WHERE id = 1")
    assert body["columns"] == ["id", "v"]
    assert body["rows"] == [[1, "a"]]
    assert body["plan"]["operation"] == "Projection"
    assert body["elapsed_ms"] >= 0


def test_syntax_error_reports_its_position(client: TestClient):
    response = client.post("/query", json={"sql": "SELECT FROM"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["kind"] == "SqlSyntaxError"
    assert detail["line"] == 1
    assert detail["column"] is not None


def test_unknown_table_is_reported(client: TestClient):
    response = client.post("/query", json={"sql": "SELECT * FROM fantasma"})
    assert response.status_code == 400
    assert response.json()["detail"]["kind"] == "UnknownTableError"


def test_a_session_keeps_the_transaction_open(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    run(client, "INSERT INTO t VALUES (1, 10)")
    assert run(client, "BEGIN TRANSACTION", SESSION)["in_transaction"]
    run(client, "UPDATE t SET v = 99 WHERE id = 1", SESSION)
    assert not run(client, "ROLLBACK", SESSION)["in_transaction"]
    assert run(client, "SELECT v FROM t")["rows"] == [[10]]


def test_committing_from_a_session_persists(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    run(client, "INSERT INTO t VALUES (1, 10)")
    run(client, "BEGIN", SESSION)
    run(client, "UPDATE t SET v = 42 WHERE id = 1", SESSION)
    run(client, "COMMIT", SESSION)
    assert run(client, "SELECT v FROM t")["rows"] == [[42]]


def test_closing_a_session_rolls_back(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY, v INT)")
    run(client, "INSERT INTO t VALUES (1, 10)")
    run(client, "BEGIN", SESSION)
    run(client, "UPDATE t SET v = 7 WHERE id = 1", SESSION)
    assert client.delete(f"/sessions/{SESSION}").json() == {"closed": True}
    assert run(client, "SELECT v FROM t")["rows"] == [[10]]


def test_closing_an_unknown_session(client: TestClient):
    assert client.delete("/sessions/fantasma").json() == {"closed": False}


def test_dates_travel_as_text(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY, alta DATE)")
    run(client, "INSERT INTO t VALUES (1, NULL)")
    assert run(client, "SELECT alta FROM t")["rows"] == [[None]]
