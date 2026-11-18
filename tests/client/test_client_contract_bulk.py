"""La carga masiva del cliente contra la ruta real: NDJSON por trozos desde un generador,
vectores de numpy, puntos y lotes rechazados."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest
from tests.client.conftest import BATCH_ROWS

from minigestor_client import Client, NotFoundError, Point, UnencodableValueError

ROWS = 12
REPEATED_ID = 6
CREATE_FRAGMENTS = (
    "CREATE TABLE fragmentos (id INT PRIMARY KEY, titulo VARCHAR(30), emb VECTOR(3), lugar POINT)"
)
# Los trozos tan chicos que cada fila sale en el suyo: el gestor recibe el cuerpo por partes.
TINY_CHUNK = 1


def fragments(count: int, repeated: int | None = None) -> Iterator[dict[str, object]]:
    for number in range(1, count + 1):
        identifier = repeated if repeated is not None and number == repeated + 1 else number
        yield {
            "id": identifier,
            "titulo": f"fragmento {number}",
            "emb": np.array([number, 0.5, -1.0], dtype=np.float32),
            "lugar": Point(-12.0 - number / 100, -77.0),
        }


@pytest.fixture
def table(client: Client) -> Client:
    client.execute(CREATE_FRAGMENTS)
    return client


def test_rows_from_a_generator_are_loaded(table: Client):
    report = table.bulk_insert("fragmentos", fragments(ROWS), chunk_bytes=TINY_CHUNK)
    assert (report.table, report.rows_loaded, report.rows_rejected) == ("fragmentos", ROWS, 0)
    assert report.batches_committed == ROWS // BATCH_ROWS
    assert report.last_line == ROWS
    assert report.interrupted is None
    row = table.fetch_one("SELECT emb, lugar FROM fragmentos WHERE id = $1", [3])
    assert row == {"emb": [3.0, 0.5, -1.0], "lugar": {"lat": -12.03, "lon": -77.0}}


def test_a_rejected_batch_says_which_lines_to_send_again(table: Client):
    report = table.bulk_insert("fragmentos", fragments(ROWS, repeated=REPEATED_ID))
    assert (report.rows_loaded, report.rows_rejected) == (ROWS - BATCH_ROWS, BATCH_ROWS)
    (rejected,) = report.rejected
    assert (rejected.first_line, rejected.last_line, rejected.line) == (5, 8, REPEATED_ID + 1)
    assert "ya existe" in rejected.reason
    assert table.scalar("SELECT COUNT(*) FROM fragmentos") == ROWS - BATCH_ROWS


def test_a_load_reports_its_timing_and_request_id(table: Client):
    report = table.bulk_insert("fragmentos", fragments(BATCH_ROWS))
    assert report.request_id
    assert "total" in report.server_timing


def test_a_value_without_json_form_stops_the_load_where_it_is(table: Client):
    def broken() -> Iterator[dict[str, object]]:
        yield from fragments(BATCH_ROWS)
        yield {"id": BATCH_ROWS + 1, "titulo": object()}

    with pytest.raises(UnencodableValueError):
        table.bulk_insert("fragmentos", broken(), chunk_bytes=TINY_CHUNK)
    assert table.scalar("SELECT COUNT(*) FROM fragmentos") in {0, BATCH_ROWS}


def test_loading_into_a_missing_table_is_not_found(client: Client):
    with pytest.raises(NotFoundError):
        client.bulk_insert("fantasma", fragments(1))
