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


def test_a_script_runs_every_statement(client: TestClient):
    body = run(
        client,
        "CREATE TABLE a (id INT PRIMARY KEY);"
        "CREATE TABLE b (id INT PRIMARY KEY);"
        "INSERT INTO a VALUES (1), (2);",
    )
    assert len(body["statements"]) == 3
    assert body["statements"][2]["affected_rows"] == 2
    assert [table["name"] for table in client.get("/tables").json()] == ["a", "b"]


def test_a_script_returns_the_rows_of_its_last_query(client: TestClient):
    body = run(
        client,
        "CREATE TABLE t (id INT PRIMARY KEY, v INT);"
        "INSERT INTO t VALUES (1, 5), (2, 7);"
        "SELECT v FROM t ORDER BY v;",
    )
    assert body["columns"] == ["v"]
    assert body["rows"] == [[5], [7]]
    assert body["plan"] is not None


def test_a_failing_statement_stops_the_script(client: TestClient):
    response = client.post(
        "/query",
        json={"sql": "CREATE TABLE t (id INT PRIMARY KEY); INSERT INTO fantasma VALUES (1);"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["kind"] == "UnknownTableError"
    assert [table["name"] for table in client.get("/tables").json()] == ["t"]


def test_elapsed_time_adds_up_the_statements(client: TestClient):
    body = run(client, "CREATE TABLE t (id INT PRIMARY KEY); INSERT INTO t VALUES (1);")
    assert body["elapsed_ms"] >= sum(item["elapsed_ms"] for item in body["statements"]) - 0.01


CSV_CONTENT = "id,nombre,precio\n1,teclado,89.9\n2,monitor,450.0\n3,mouse,25.5\n"


def upload(client: TestClient, **fields: object) -> object:
    data = {"name": "productos", **fields}
    return client.post(
        "/tables/upload",
        data=data,
        files={"file": ("productos.csv", CSV_CONTENT.encode(), "text/csv")},
    )


def test_upload_creates_a_heap_table(client: TestClient):
    response = upload(client)
    assert response.status_code == 200, response.text
    assert response.json()["affected_rows"] == 3
    table = client.get("/tables").json()[0]
    assert table["name"] == "productos"
    assert table["organization"] == "heap"
    assert [column["name"] for column in table["columns"]] == ["id", "nombre", "precio"]


def test_upload_infers_the_column_types(client: TestClient):
    upload(client)
    types = {item["name"]: item["type"] for item in client.get("/tables").json()[0]["columns"]}
    assert types == {"id": "INT", "nombre": "STRING", "precio": "FLOAT"}


def test_upload_can_order_the_table_by_a_key(client: TestClient):
    response = upload(client, organization="clustered_btree", key_column="id")
    assert response.status_code == 200, response.text
    assert client.get("/tables").json()[0]["organization"] == "clustered_btree"


def test_uploaded_rows_are_queryable(client: TestClient):
    upload(client)
    body = run(client, "SELECT nombre FROM productos ORDER BY precio DESC;")
    assert body["rows"] == [["monitor"], ["teclado"], ["mouse"]]


def test_upload_rejects_an_invalid_table_name(client: TestClient):
    response = upload(client, name="productos; DROP TABLE x")
    assert response.status_code == 400
    assert response.json()["detail"]["kind"] == "UploadError"


def test_upload_rejects_an_unknown_organization(client: TestClient):
    assert upload(client, organization="magia").status_code == 400


def test_ordered_upload_without_a_key_is_rejected(client: TestClient):
    assert upload(client, organization="sequential").status_code == 400


def test_upload_rejects_an_empty_file(client: TestClient):
    response = client.post(
        "/tables/upload",
        data={"name": "vacia"},
        files={"file": ("vacia.csv", b"", "text/csv")},
    )
    assert response.status_code == 400


def test_uploading_the_same_name_twice_is_rejected(client: TestClient):
    upload(client)
    assert upload(client).status_code == 400


def test_dropping_every_table_leaves_the_database_empty(client: TestClient):
    run(client, "CREATE TABLE a (id INT PRIMARY KEY); CREATE TABLE b (id INT PRIMARY KEY);")
    run(client, "INSERT INTO a VALUES (1), (2);")
    response = client.delete("/tables")
    assert response.status_code == 200, response.text
    assert len(response.json()["statements"]) == 2
    assert client.get("/tables").json() == []


def test_dropping_every_table_on_an_empty_database_is_harmless(client: TestClient):
    response = client.delete("/tables")
    assert response.status_code == 200
    assert response.json()["statements"] == []


def test_tables_can_be_recreated_after_dropping_everything(client: TestClient):
    run(client, "CREATE TABLE a (id INT PRIMARY KEY, v VARCHAR(4));")
    run(client, "INSERT INTO a VALUES (1, 'x');")
    client.delete("/tables")
    run(client, "CREATE TABLE a (id INT PRIMARY KEY, v VARCHAR(4));")
    assert run(client, "SELECT * FROM a;")["rows"] == []


def test_a_csv_with_another_encoding_is_a_user_error(client: TestClient):
    latin1 = "id,nombre\n1,Peña\n".encode("latin-1")
    response = client.post(
        "/tables/upload",
        data={"name": "t"},
        files={"file": ("t.csv", latin1, "text/csv")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["kind"] == "LoaderError"


def test_a_script_with_a_syntax_error_reports_line_and_column(client: TestClient):
    response = client.post("/query", json={"sql": "SELECT *\nFROM"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert (detail["line"], detail["kind"]) == (2, "SqlSyntaxError")


def test_a_heap_upload_with_a_key_gets_a_primary_key_index(client: TestClient):
    response = upload(client, key_column="id")
    assert response.status_code == 200, response.text
    table = client.get("/tables").json()[0]
    assert (table["organization"], table["primary_key"]) == ("heap", "id")
    assert table["indexes"] == ["pk_productos"]
    plan = run(client, "SELECT * FROM productos WHERE id = 2;")["plan"]
    assert "IndexLookup" in str(plan)


def test_a_heap_upload_without_a_key_has_no_index(client: TestClient):
    upload(client, key_column="")
    table = client.get("/tables").json()[0]
    assert (table["primary_key"], table["indexes"]) == (None, [])


def test_an_upload_with_a_missing_key_column_leaves_nothing(client: TestClient):
    response = upload(client, organization="sequential", key_column="falsa")
    assert response.status_code == 400
    assert client.get("/tables").json() == []


def test_a_file_can_be_uploaded_without_creating_a_table(client: TestClient):
    response = client.post(
        "/files/upload",
        data={"name": "productos"},
        files={"file": ("productos.csv", CSV_CONTENT.encode(), "text/csv")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["columns"] == ["id", "nombre", "precio"]
    assert client.get("/tables").json() == []
    created = run(client, f"CREATE TABLE productos FROM FILE '{body['path']}' USING INDEX BTREE(\"id\");")
    assert created["affected_rows"] == 3
    assert client.get("/tables").json()[0]["organization"] == "clustered_btree"


def test_the_structure_of_a_clustered_table_shows_its_tree(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY INDEX BTREE, v INT);")
    rows = ", ".join(f"({key}, {key})" for key in range(300))
    run(client, f"INSERT INTO t VALUES {rows};")
    body = client.get("/tables/t/structure").json()
    tree = body["storage"]
    assert tree["kind"] == "bplustree"
    assert tree["height"] == len(tree["levels"]) >= 2
    assert tree["levels"][-1]["key_count"] == tree["entries"] == 300


def test_the_structure_of_a_hash_index_respects_its_invariant(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY, v INT INDEX HASH);")
    rows = ", ".join(f"({key}, {key})" for key in range(400))
    run(client, f"INSERT INTO t VALUES {rows};")
    index = next(
        item for item in client.get("/tables/t/structure").json()["indexes"]
        if item["method"] == "HASH"
    )["structure"]
    assert index["entries"] == 400
    assert index["directory_size"] == 2 ** index["global_depth"]
    for bucket in index["buckets"]:
        assert bucket["pointers"] == 2 ** (index["global_depth"] - bucket["local_depth"])


def test_the_structure_of_an_unknown_table_is_an_error(client: TestClient):
    assert client.get("/tables/fantasma/structure").status_code == 400
