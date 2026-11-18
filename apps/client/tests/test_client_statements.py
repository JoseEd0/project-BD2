"""Qué scripts se pueden reintentar: solo los que leen."""

from __future__ import annotations

import pytest

from minigestor_client import is_read_only


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t",
        "  select id from t where nombre = $1",
        "SELECT 1; SELECT 2;",
        "-- comentario\nSELECT 1",
        "/* INSERT */ SELECT 1",
        "EXPLAIN SELECT * FROM t",
        "EXPLAIN INSERT INTO t VALUES (1)",
        "EXPLAIN ANALYZE SELECT * FROM t",
        "SELECT ';DROP TABLE t' FROM t",
        "SELECT 'it''s; DELETE FROM t' FROM t",
        'SELECT "raro;DELETE" FROM t',
    ],
)
def test_reading_scripts_are_read_only(sql: str):
    assert is_read_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "CREATE TABLE IF NOT EXISTS t (id INT)",
        "DROP TABLE t",
        "BEGIN TRANSACTION",
        "COMMIT",
        "ROLLBACK",
        "SELECT 1; INSERT INTO t VALUES (1)",
        "EXPLAIN ANALYZE INSERT INTO t VALUES (1)",
        "-- SELECT\nDELETE FROM t",
        "",
        "   ;  ",
    ],
)
def test_anything_else_is_a_write(sql: str):
    assert not is_read_only(sql)
