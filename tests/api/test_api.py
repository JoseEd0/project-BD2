import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO

import pytest
from fastapi.testclient import TestClient

import api.routes.uploads as uploads_route
from api.main import create_app
from api.service import Service
from config import EngineConfig
from query.engine import Engine

SESSION = "demo"
IMPATIENT_TIMEOUT = 0.1


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
    assert client.post(f"/sessions/{SESSION}/close").json() == {"closed": True}
    assert run(client, "SELECT v FROM t")["rows"] == [[10]]


def test_closing_an_unknown_session(client: TestClient):
    assert client.post("/sessions/fantasma/close").json() == {"closed": False}


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


STORES = (
    "INSERT INTO tiendas VALUES "
    "(1, 'plaza', POINT(-12.0464, -77.0428)), "
    "(2, 'kennedy', POINT(-12.1211, -77.0297)), "
    "(3, 'cusco', POINT(-13.5167, -71.9781))"
)
NEAR_THE_SQUARE = "distancia(ubicacion, POINT(-12.0464, -77.0428)) < 20000"


@pytest.fixture
def stores(client: TestClient) -> TestClient:
    run(client, "CREATE TABLE tiendas (id INT PRIMARY KEY, nombre VARCHAR(12), ubicacion POINT)")
    run(client, STORES)
    run(client, "CREATE INDEX idx_tiendas_ubicacion ON tiendas USING RTREE (ubicacion)")
    return client


def test_points_travel_as_latitude_and_longitude(stores: TestClient):
    body = run(stores, "SELECT id, ubicacion FROM tiendas WHERE id = 1")
    assert body["rows"] == [[1, {"lat": -12.0464, "lon": -77.0428}]]


def test_the_file_panel_shows_the_spatial_index(stores: TestClient):
    (table,) = stores.get("/tables").json()
    column = next(item for item in table["columns"] if item["name"] == "ubicacion")
    assert column["type"] == "POINT"
    assert column["indexed_with"] == ["RTREE"]


def test_a_radius_query_describes_its_circle(stores: TestClient):
    body = run(stores, f"SELECT * FROM tiendas WHERE {NEAR_THE_SQUARE}")
    assert sorted(row[0] for row in body["rows"]) == [1, 2]
    assert body["plan"]["children"][0]["children"][0]["operation"] == "SpatialRangeScan"
    assert body["spatial"] == {
        "table": "tiendas",
        "column": "ubicacion",
        "overlays": [
            {
                "kind": "radius",
                "center": {"lat": -12.0464, "lon": -77.0428},
                "radius": 20000.0,
                "metric": "haversine",
                "unit": "m",
                "vertices": [],
            }
        ],
    }


def test_a_nearest_query_describes_its_reference_point(stores: TestClient):
    body = run(
        stores, "SELECT * FROM tiendas ORDER BY distancia(ubicacion, POINT(-13.5, -72.0)) LIMIT 1"
    )
    assert [row[0] for row in body["rows"]] == [3]
    (overlay,) = body["spatial"]["overlays"]
    assert overlay["kind"] == "nearest"
    assert overlay["center"] == {"lat": -13.5, "lon": -72.0}


def test_a_polygon_query_describes_its_vertices(stores: TestClient):
    polygon = "POLYGON((-12.0, -77.1), (-12.0, -77.0), (-12.2, -77.0), (-12.2, -77.1))"
    body = run(stores, f"SELECT id FROM tiendas WHERE intersecta(ubicacion, {polygon})")
    assert sorted(row[0] for row in body["rows"]) == [1, 2]
    (overlay,) = body["spatial"]["overlays"]
    assert overlay["kind"] == "polygon"
    assert overlay["vertices"][0] == {"lat": -12.0, "lon": -77.1}
    assert len(overlay["vertices"]) == 4


def test_a_query_without_points_has_no_spatial_part(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY)")
    assert run(client, "SELECT * FROM t")["spatial"] is None


def test_table_points_feed_the_map(stores: TestClient):
    body = stores.get("/tables/tiendas/points").json()
    assert body["table"] == "tiendas"
    assert body["column"] == "ubicacion"
    assert body["total"] == 3
    assert sorted(body["points"]) == [[-13.5167, -71.9781], [-12.1211, -77.0297], [-12.0464, -77.0428]]


def test_table_points_are_sampled_evenly_when_there_are_too_many(client: TestClient):
    run(client, "CREATE TABLE puntos (id INT PRIMARY KEY, lugar POINT)")
    values = ", ".join(f"({number}, POINT({number / 100}, 0))" for number in range(200))
    run(client, f"INSERT INTO puntos VALUES {values}")
    body = client.get("/tables/puntos/points", params={"limit": 20}).json()
    assert body["total"] == 200
    assert len(body["points"]) == 20
    latitudes = sorted(point[0] for point in body["points"])
    assert latitudes[0] == 0.0
    assert latitudes[-1] >= 1.8


def test_table_points_leave_out_missing_locations(stores: TestClient):
    run(stores, "INSERT INTO tiendas VALUES (4, 'sin sitio', NULL)")
    body = stores.get("/tables/tiendas/points").json()
    assert body["total"] == 4
    assert len(body["points"]) == 3


def test_table_points_need_a_point_column(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY, nombre VARCHAR(8))")
    response = client.get("/tables/t/points")
    assert response.status_code == 400
    assert "POINT" in response.json()["detail"]["error"]
    wrong = client.get("/tables/t/points", params={"column": "nombre"})
    assert wrong.status_code == 400


def test_table_points_of_an_unknown_table(client: TestClient):
    assert client.get("/tables/nadie/points").status_code == 400


def test_table_points_reject_a_non_positive_limit(stores: TestClient):
    assert stores.get("/tables/tiendas/points", params={"limit": 0}).status_code == 422


def test_structure_describes_the_rtree(stores: TestClient):
    body = stores.get("/tables/tiendas/structure").json()
    spatial = next(index for index in body["indexes"] if index["method"] == "RTREE")
    assert spatial["structure"]["kind"] == "rtree"
    assert spatial["structure"]["entries"] == 3
    assert spatial["structure"]["levels"][0]["nodes"][0]["bounds"] == [
        -13.5167,
        -77.0428,
        -12.0464,
        -71.9781,
    ]


def test_spatial_mistakes_are_reported_as_the_users_error(stores: TestClient):
    response = stores.post(
        "/query", json={"sql": "SELECT * FROM tiendas WHERE distancia(ubicacion, POINT(99, 0)) < 1"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["kind"] == "ExpressionError"
    assert "latitud" in response.json()["detail"]["error"]


def test_csv_upload_with_points(client: TestClient):
    content = 'id,nombre,ubicacion\n1,plaza,"POINT(-12.0464, -77.0428)"\n2,cusco,"POINT(-13.5167, -71.9781)"\n'
    response = client.post(
        "/tables/upload",
        files={"file": ("sitios.csv", content.encode(), "text/csv")},
        data={"name": "sitios", "organization": "heap", "key_column": "id"},
    )
    assert response.status_code == 200, response.text
    run(client, "CREATE INDEX idx_sitios_ubicacion ON sitios USING RTREE (ubicacion)")
    body = run(client, "SELECT id FROM sitios ORDER BY distancia(ubicacion, POINT(-13.5, -72.0)) LIMIT 1")
    assert body["rows"] == [[2]]


def post_csv(client: TestClient, endpoint: str, name: str, content: bytes, **fields: object):
    return client.post(
        endpoint, data={"name": name, **fields}, files={"file": (f"{name}.csv", content, "text/csv")}
    )


def uploads_of(config: EngineConfig) -> list[str]:
    directory = config.data_directory / "uploads"
    return sorted(path.name for path in directory.iterdir()) if directory.exists() else []


def test_every_value_type_travels_as_json(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY, x FLOAT, ok BOOL, f DATE, s VARCHAR(4), p POINT)")
    run(client, "INSERT INTO t VALUES (1, 2.5, TRUE, '2026-03-15', 'ana', POINT(-12.0, -77.0))")
    run(client, "INSERT INTO t VALUES (2, NULL, NULL, NULL, NULL, NULL)")
    body = run(client, "SELECT id, x, ok, f, s, p, (id, x) FROM t ORDER BY id")
    assert body["rows"] == [
        [1, 2.5, True, "2026-03-15", "ana", {"lat": -12.0, "lon": -77.0}, [1.0, 2.5]],
        [2, None, None, None, None, None, None],
    ]


def test_a_statement_without_rows_has_no_plan(client: TestClient):
    body = run(client, "CREATE TABLE t (id INT PRIMARY KEY)")
    assert body["plan"] is None
    assert body["columns"] == []
    assert "creada" in body["message"]


def test_the_structure_of_a_sequential_table_shows_its_pages(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY INDEX SEQ, v INT)")
    rows = ", ".join(f"({key}, {key})" for key in range(60))
    run(client, f"INSERT INTO t VALUES {rows}")
    run(client, "DELETE FROM t WHERE id = 7")
    storage = client.get("/tables/t/structure").json()["storage"]
    assert storage["kind"] == "sequential"
    assert storage["records"] == 59
    assert storage["main_pages"] >= 2
    assert storage["deleted_records"] <= 1
    assert 0 <= storage["waste_ratio"] < 0.3


def test_a_file_upload_needs_a_plain_name(client: TestClient, config: EngineConfig):
    response = post_csv(client, "/files/upload", "../fuera", CSV_CONTENT.encode())
    assert response.status_code == 400
    assert "no es un nombre de archivo válido" in response.json()["detail"]["error"]
    assert uploads_of(config) == []


def test_a_file_that_is_not_a_csv_is_not_kept(client: TestClient, config: EngineConfig):
    response = post_csv(client, "/files/upload", "binario", b"\xff\xfe\x00\x01")
    assert response.status_code == 400
    assert "no se puede leer como CSV" in response.json()["detail"]["error"]
    assert uploads_of(config) == []


def test_an_upload_rejects_a_key_that_is_not_a_column_name(client: TestClient):
    response = upload(client, organization="clustered_btree", key_column="id; DROP")
    assert response.status_code == 400
    assert "no es un nombre de columna válido" in response.json()["detail"]["error"]
    assert client.get("/tables").json() == []


def test_an_upload_larger_than_the_limit_is_rejected(config: EngineConfig):
    small = EngineConfig(
        page_size=config.page_size,
        max_upload_bytes=len(CSV_CONTENT) - 1,
        data_directory=config.data_directory,
    )
    with TestClient(create_app(small)) as limited:
        response = upload(limited)
        assert response.status_code == 413
        assert "el máximo permitido" in response.json()["detail"]["error"]
        assert limited.get("/tables").json() == []


def test_a_file_too_wide_for_a_page_leaves_no_table(client: TestClient):
    """Con páginas de 256 bytes, seis columnas de texto largo no caben en una fila. La
    subida falla con un mensaje claro y la tabla no queda a medio crear."""
    wide = ",".join("x" * 60 for _ in range(6))
    response = post_csv(client, "/tables/upload", "ancha", f"a,b,c,d,e,f\n{wide}\n".encode())
    assert response.status_code == 400
    assert "no cabe en páginas" in response.json()["detail"]["error"]
    assert client.get("/tables").json() == []
    assert post_csv(client, "/tables/upload", "ancha", CSV_CONTENT.encode()).status_code == 200


def test_emptying_the_database_waits_for_open_transactions(config: EngineConfig):
    """Otra sesión tiene una transacción abierta sobre la tabla: vaciar la base no puede
    borrarla por debajo. Agotada la espera, responde con un error y la tabla sigue ahí."""
    impatient = EngineConfig(
        page_size=config.page_size, lock_timeout_seconds=0.1, data_directory=config.data_directory
    )
    with TestClient(create_app(impatient)) as busy:
        run(busy, "CREATE TABLE t (id INT PRIMARY KEY)")
        run(busy, "BEGIN", session="uno")
        run(busy, "INSERT INTO t VALUES (1)", session="uno")
        response = busy.delete("/tables", params={"session_id": "dos"})
        assert response.status_code == 400
        assert response.json()["detail"]["kind"] == "LockTimeoutError"
        run(busy, "COMMIT", session="uno")
        assert run(busy, "SELECT * FROM t", session="dos")["rows"] == [[1]]
        assert busy.delete("/tables", params={"session_id": "dos"}).status_code == 200
        assert busy.get("/tables").json() == []


def impatient_config(config: EngineConfig, **overrides: object) -> EngineConfig:
    return replace(config, lock_timeout_seconds=IMPATIENT_TIMEOUT, **overrides)


def test_begin_without_a_session_is_rejected_and_runs_nothing(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY)")
    response = client.post("/query", json={"sql": "BEGIN; INSERT INTO t VALUES (1);"})
    assert response.status_code == 400
    assert "session_id" in response.json()["detail"]["error"]
    assert response.json()["detail"]["kind"] == "SessionError"
    assert run(client, "SELECT * FROM t")["rows"] == []


def test_requests_without_a_session_leave_no_transaction_behind(client: TestClient):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY)")
    run(client, "INSERT INTO t VALUES (1)")
    client.post("/query", json={"sql": "INSERT INTO t VALUES (1)"})
    service = client.app.state.service
    assert service.transactions.active_count == 0
    assert service.locks.holders_of("t") == {}


def test_an_aborted_transaction_is_reported_with_the_error(config: EngineConfig):
    with TestClient(create_app(impatient_config(config))) as busy:
        run(busy, "CREATE TABLE t (id INT PRIMARY KEY, v INT)")
        run(busy, "INSERT INTO t VALUES (1, 1)")
        run(busy, "BEGIN", session="uno")
        run(busy, "UPDATE t SET v = 2", session="uno")
        run(busy, "BEGIN", session="dos")
        response = busy.post("/query", json={"sql": "UPDATE t SET v = 3", "session_id": "dos"})
        assert response.status_code == 400
        assert response.json()["detail"]["kind"] == "LockTimeoutError"
        assert response.json()["detail"]["in_transaction"] is False


def test_an_idle_session_expires_and_releases_its_locks(config: EngineConfig):
    idle = impatient_config(config, session_idle_seconds=IMPATIENT_TIMEOUT)
    with TestClient(create_app(idle)) as client:
        run(client, "CREATE TABLE t (id INT PRIMARY KEY, v INT)")
        run(client, "INSERT INTO t VALUES (1, 1)")
        run(client, "BEGIN", session="olvidada")
        run(client, "UPDATE t SET v = 99", session="olvidada")
        time.sleep(IMPATIENT_TIMEOUT * 2)
        run(client, "UPDATE t SET v = 2", session="otra")
        assert run(client, "SELECT v FROM t")["rows"] == [[2]]
        assert run(client, "SELECT v FROM t", session="olvidada")["in_transaction"] is False


def test_closing_the_server_saves_committed_data_even_if_a_session_fails(
    config: EngineConfig, monkeypatch: pytest.MonkeyPatch
):
    service = Service(config)
    with service.session(None) as autocommit:
        autocommit.execute("CREATE TABLE t (id INT PRIMARY KEY)")
        autocommit.execute("INSERT INTO t VALUES (1), (2), (3)")
    with service.session("rota") as broken:
        broken.execute("BEGIN")

        def fail() -> None:
            raise RuntimeError("no se pudo deshacer")

        monkeypatch.setattr(broken, "close", fail)
    with pytest.raises(RuntimeError):
        service.close()
    with Engine(config) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM t").rows == ((3,),)


def test_a_name_with_a_trailing_newline_is_rejected(client: TestClient, config: EngineConfig):
    response = post_csv(client, "/files/upload", "nombre\n", CSV_CONTENT.encode())
    assert response.status_code == 400
    assert uploads_of(config) == []


def test_creating_a_table_from_an_upload_keeps_no_file(client: TestClient, config: EngineConfig):
    assert upload(client).status_code == 200
    assert upload(client).status_code == 400
    assert uploads_of(config) == []


def test_a_file_upload_replaces_the_previous_one(client: TestClient):
    post_csv(client, "/files/upload", "datos", b"a,b\n1,2\n")
    body = post_csv(client, "/files/upload", "datos", b"x,y,z\n1,2,3\n").json()
    assert body["columns"] == ["x", "y", "z"]


def test_a_table_cannot_be_loaded_from_outside_the_allowed_directories(
    client: TestClient, tmp_path_factory: pytest.TempPathFactory
):
    outside = tmp_path_factory.mktemp("fuera") / "secreto.csv"
    outside.write_text("usuario,clave\nadmin,hunter2\n")
    response = client.post("/query", json={"sql": f"CREATE TABLE robo FROM FILE '{outside}'"})
    assert response.status_code == 400
    assert response.json()["detail"]["kind"] == "ForbiddenFileError"
    assert client.get("/tables").json() == []


def test_a_query_returns_at_most_the_configured_rows(config: EngineConfig):
    with TestClient(create_app(replace(config, max_result_rows=3))) as client:
        run(client, "CREATE TABLE t (id INT)")
        run(client, "INSERT INTO t VALUES " + ", ".join(f"({i})" for i in range(10)))
        body = run(client, "SELECT * FROM t")
        assert len(body["rows"]) == 3
        assert body["total_rows"] == 10


def test_inspecting_a_table_waits_for_a_writing_transaction(config: EngineConfig):
    with TestClient(create_app(impatient_config(config))) as client:
        run(client, "CREATE TABLE t (id INT PRIMARY KEY, lugar POINT)")
        run(client, "BEGIN", session="escribe")
        run(client, "INSERT INTO t VALUES (1, POINT(-12.0, -77.0))", session="escribe")
        for endpoint in ("/tables/t/structure", "/tables/t/points"):
            response = client.get(endpoint)
            assert response.status_code == 400
            assert response.json()["detail"]["kind"] == "LockTimeoutError"
            assert client.get(endpoint, params={"session_id": "escribe"}).status_code == 200
        assert client.get("/tables").status_code == 200
        run(client, "COMMIT", session="escribe")
        assert client.get("/tables/t/points").json()["points"] == [[-12.0, -77.0]]


def test_a_request_that_does_not_fit_the_schema_has_the_usual_error_shape(client: TestClient):
    response = client.post("/query", json={"sql": ""})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["kind"] == "RequestValidationError"
    assert "sql" in detail["error"]
    assert set(detail) == {"error", "kind", "line", "column", "in_transaction"}


def test_a_bug_in_the_engine_is_a_server_error_not_the_users(
    config: EngineConfig, monkeypatch: pytest.MonkeyPatch
):
    def broken(*_: object) -> None:
        raise RuntimeError("fallo interno")

    with TestClient(create_app(config), raise_server_exceptions=False) as client:
        monkeypatch.setattr(client.app.state.service.engine, "run", broken)
        assert client.post("/query", json={"sql": "SELECT 1 FROM t"}).status_code == 500


def test_an_upload_that_announces_too_many_bytes_is_refused_before_reading_it(
    config: EngineConfig, monkeypatch: pytest.MonkeyPatch
):
    def never_copied(*_: object) -> None:
        raise AssertionError("la ruta no debía recibir el archivo")

    monkeypatch.setattr(uploads_route, "_copy_limited", never_copied)
    small = replace(config, max_upload_bytes=1024)
    oversized = b"id\n" + b"1\n" * uploads_route.MULTIPART_ALLOWANCE_BYTES
    with TestClient(create_app(small)) as limited:
        response = post_csv(limited, "/tables/upload", "grande", oversized)
        assert response.status_code == 413
        assert response.json()["detail"]["kind"] == "UploadError"
    assert uploads_of(config) == []


def test_each_upload_writes_to_its_own_temporary_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Dos subidas con el mismo nombre a la vez no pueden escribir en el mismo temporal."""
    targets: list[Path] = []
    original = uploads_route._copy_limited

    def recording(source: BinaryIO, target: Path, limit: int) -> None:
        targets.append(target)
        original(source, target, limit)

    monkeypatch.setattr(uploads_route, "_copy_limited", recording)
    for _ in range(2):
        assert post_csv(client, "/files/upload", "datos", CSV_CONTENT.encode()).status_code == 200
    assert len(set(targets)) == 2


def test_a_server_error_keeps_the_error_shape_and_the_cors_headers(
    config: EngineConfig, monkeypatch: pytest.MonkeyPatch
):
    """Sin las cabeceras CORS el navegador descarta la respuesta y el frontend solo ve un
    fallo de red, sin el mensaje."""
    origin = "http://localhost:5173"

    def broken(*_: object, **__: object) -> None:
        raise RuntimeError("fallo interno")

    with TestClient(create_app(config, (origin,)), raise_server_exceptions=False) as client:
        monkeypatch.setattr(client.app.state.service.engine, "run", broken)
        response = client.post(
            "/query", json={"sql": "SELECT 1 FROM t"}, headers={"Origin": origin}
        )
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == origin
    assert response.json()["detail"]["kind"] == "RuntimeError"
    assert "fallo interno" in response.json()["detail"]["error"]


def test_a_table_dropped_while_listing_is_left_out(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    run(client, "CREATE TABLE t (id INT PRIMARY KEY)")
    engine = client.app.state.service.engine
    monkeypatch.setattr(engine, "table_names", lambda: ["borrada", "t"])
    assert [table["name"] for table in client.get("/tables").json()] == ["t"]


THREAD_JOIN_SECONDS = 5.0


def _eventually(condition: Callable[[], bool]) -> bool:
    """Una espera acotada, para que un test de hilos falle en vez de colgarse."""
    deadline = time.monotonic() + THREAD_JOIN_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            return False
        threading.Event().wait(0.001)
    return True


def test_closing_a_session_with_a_request_in_flight_waits_for_it(config: EngineConfig):
    """La pestaña se cierra mientras una petición suya espera turno. El cierre no espera a
    esa petición: queda pendiente y lo hace ella al terminar. Si la sesión se cerrara
    antes, la petición abriría una transacción que nadie podría cerrar, con sus bloqueos."""
    service = Service(config)
    with service.session(None) as autocommit:
        autocommit.execute("CREATE TABLE t (id INT PRIMARY KEY, v INT)")
        autocommit.execute("INSERT INTO t VALUES (1, 1)")
    with service.session(SESSION):
        pass
    slot = service._sessions[SESSION]
    slot.guard.acquire()

    def late_request() -> None:
        with service.session(SESSION) as session:
            session.execute("BEGIN")
            session.execute("UPDATE t SET v = 2 WHERE id = 1")

    worker = threading.Thread(target=late_request)
    closer = threading.Thread(target=service.close_session, args=(SESSION,))
    worker.start()
    queued = _eventually(lambda: slot.requests == 1)
    closer.start()
    closer.join(THREAD_JOIN_SECONDS)
    closed_without_waiting = not closer.is_alive()
    slot.guard.release()
    worker.join(THREAD_JOIN_SECONDS)
    closer.join(THREAD_JOIN_SECONDS)
    assert queued and closed_without_waiting
    assert not worker.is_alive()
    assert not slot.session.in_transaction
    assert service.locks.holders_of("t") == {}
    with service.session(None) as autocommit:
        assert autocommit.execute("SELECT v FROM t").rows == ((1,),)
    service.close()


def test_one_expired_session_that_fails_to_close_does_not_keep_the_others_open(
    config: EngineConfig, monkeypatch: pytest.MonkeyPatch
):
    service = Service(impatient_config(config, session_idle_seconds=IMPATIENT_TIMEOUT))
    with service.session(None) as autocommit:
        autocommit.execute("CREATE TABLE a (id INT PRIMARY KEY)")
        autocommit.execute("CREATE TABLE b (id INT PRIMARY KEY)")
    for identifier, table in (("rota", "a"), ("sana", "b")):
        with service.session(identifier) as session:
            session.execute("BEGIN")
            session.execute(f"INSERT INTO {table} VALUES (1)")

    def fail() -> None:
        raise RuntimeError("no se pudo deshacer")

    monkeypatch.setattr(service._sessions["rota"].session, "close", fail)
    time.sleep(IMPATIENT_TIMEOUT * 2)
    with pytest.raises(RuntimeError), service.session("nueva"):
        pass
    assert service.locks.holders_of("b") == {}
    assert service._sessions["nueva"].requests == 0
    monkeypatch.undo()
    service.close()
