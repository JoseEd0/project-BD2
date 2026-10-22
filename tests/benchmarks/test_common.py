"""Las utilidades que comparten los experimentos: datos, memoria pico y PostgreSQL."""

import argparse
import tracemalloc

import pytest
from benchmarks.common import (
    BYTES_PER_KIB,
    POSTGRES_DSN_VARIABLE,
    add_postgres_argument,
    build_rows,
    deletion_order,
    peak_memory_kib,
    sample_keys,
)

ALLOCATED_BYTES = 4 * 1024 * 1024
SEED = 20260827
ROWS = 2000
QUERIES = 400
# Con claves al azar, una de cada diez cae en el último décimo insertado; con el sesgo que
# este test vigila caían casi todas.
LAST_TENTH = 0.9
MOST_IN_THE_LAST_TENTH = 0.2


def insertion_rank(seed: int) -> dict[int, float]:
    inserted = [row[0] for row in build_rows(ROWS, seed)]
    return {key: position / ROWS for position, key in enumerate(inserted)}


def test_the_queried_keys_do_not_follow_the_insertion_order():
    rank = insertion_rank(SEED)
    keys = sample_keys(ROWS, QUERIES, SEED)
    late = sum(rank[key] >= LAST_TENTH for key in keys) / len(keys)
    assert late < MOST_IN_THE_LAST_TENTH


def test_the_deletions_do_not_follow_the_insertion_order():
    rank = insertion_rank(SEED)
    first_deleted = deletion_order(ROWS, SEED)[:QUERIES]
    late = sum(rank[key] >= LAST_TENTH for key in first_deleted) / len(first_deleted)
    assert late < MOST_IN_THE_LAST_TENTH
    assert sorted(deletion_order(ROWS, SEED)) == list(range(ROWS))


def test_the_peak_memory_includes_what_the_action_allocates():
    peak = peak_memory_kib(lambda: bytearray(ALLOCATED_BYTES))
    assert peak >= ALLOCATED_BYTES / BYTES_PER_KIB


def test_tracing_stops_even_when_the_action_fails():
    def failing() -> None:
        raise ValueError("falla a propósito")

    with pytest.raises(ValueError, match="a propósito"):
        peak_memory_kib(failing)
    assert not tracemalloc.is_tracing()


def test_the_postgres_connection_defaults_to_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(POSTGRES_DSN_VARIABLE, "dbname=desde_el_entorno")
    parser = argparse.ArgumentParser()
    add_postgres_argument(parser)
    assert parser.parse_args([]).postgres_dsn == "dbname=desde_el_entorno"
    assert parser.parse_args(["--postgres-dsn", "dbname=otra"]).postgres_dsn == "dbname=otra"


def test_without_a_connection_the_reference_is_not_measured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(POSTGRES_DSN_VARIABLE, raising=False)
    parser = argparse.ArgumentParser()
    add_postgres_argument(parser)
    assert parser.parse_args([]).postgres_dsn is None
