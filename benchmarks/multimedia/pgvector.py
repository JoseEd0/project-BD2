"""`ivfflat` y `hnsw` de pgvector, la referencia de base de datos del experimento.

Los vectores se cargan una vez por tamaño en una tabla normal (no temporal: una tabla
temporal pasa por los 8 MB de `temp_buffers` y no por `shared_buffers`) y cada índice se
construye y se borra sobre ella. Construir es solo `CREATE INDEX`, con la tabla ya cargada,
igual que en el gestor la construcción empieza con los vectores ya calculados.

Cada consulta es un `ORDER BY v <op> q LIMIT k` desde el cliente, con el viaje por el
socket incluido. `enable_seqscan = off` obliga a usar el índice aunque la tabla sea chica.
El esfuerzo es `ivfflat.probes` o `hnsw.ef_search` de la sesión; los *iterative scans* de
pgvector 0.8 quedan apagados, como vienen.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from benchmarks.common import connect_postgres, relation_kib
from benchmarks.multimedia.names import PGVECTOR_HNSW, PGVECTOR_IVF
from benchmarks.multimedia.techniques import (
    BuiltIndex,
    IndexParameters,
    SearchEffort,
    TechniqueSpec,
)
from index.ivf.index import default_list_count, default_probe_count
from index.vector import VectorMetric
from index.vector.metrics import Vectors

TABLE = "bench_vectores"
INDEX = "bench_vectores_idx"
OPERATOR_CLASSES = {
    VectorMetric.EUCLIDEAN: ("vector_l2_ops", "<->"),
    VectorMetric.COSINE: ("vector_cosine_ops", "<=>"),
    VectorMetric.DOT: ("vector_ip_ops", "<#>"),
}
EFFORT_SETTINGS = {SearchEffort.PROBES: "ivfflat.probes", SearchEffort.EF_SEARCH: "hnsw.ef_search"}
# El tope de `hnsw.ef_search` en pgvector.
MAX_EF_SEARCH = 1000
POSTGRES_SETTINGS = (
    "server_version",
    "shared_buffers",
    "maintenance_work_mem",
    "work_mem",
    "max_parallel_maintenance_workers",
)
SIGNIFICANT_DIGITS = 9
EXPLAIN_SQL = "EXPLAIN (ANALYZE, FORMAT JSON) "


class Postgres:
    """Una conexión al PostgreSQL de referencia con la tabla de vectores de un tamaño."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @property
    def cursor(self) -> Any:
        return self._cursor

    def describe(self) -> dict[str, str]:
        """La versión de PostgreSQL y de pgvector y la memoria con que construye y busca."""
        found = {}
        for name in POSTGRES_SETTINGS:
            self._cursor.execute(f"SHOW {name}")
            (found[f"postgres_{name}"],) = self._cursor.fetchone()
        self._cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        (found["pgvector_version"],) = self._cursor.fetchone()
        return found

    def load(self, vectors: Vectors) -> float:
        """Reemplaza la tabla por una con estos vectores; devuelve sus KiB."""
        self._cursor.execute(f"DROP TABLE IF EXISTS {TABLE}")
        self._cursor.execute(
            f"CREATE TABLE {TABLE} (id integer PRIMARY KEY, v vector({vectors.shape[1]}))"
        )
        with self._cursor.copy(f"COPY {TABLE} (id, v) FROM STDIN") as copy:
            for position, vector in enumerate(vectors):
                copy.write_row((position, vector_literal(vector)))
        self._cursor.execute(f"VACUUM ANALYZE {TABLE}")
        return relation_kib(self._cursor, TABLE)

    def drop(self) -> None:
        self._cursor.execute(f"DROP TABLE IF EXISTS {TABLE}")


@contextmanager
def postgres(dsn: str, maintenance_work_mem: str | None, threads: int) -> Iterator[Postgres]:
    """La conexión, con pgvector creado y la memoria y los procesos de `CREATE INDEX`
    fijados para la sesión; al salir borra la tabla."""
    with connect_postgres(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cursor.execute("SET enable_seqscan = off")
        cursor.execute(
            "SELECT set_config('max_parallel_maintenance_workers', %s, false)",
            (str(threads - 1),),
        )
        if maintenance_work_mem is not None:
            cursor.execute(
                "SELECT set_config('maintenance_work_mem', %s, false)", (maintenance_work_mem,)
            )
        database = Postgres(cursor)
        try:
            yield database
        finally:
            database.drop()


def vector_literal(vector: Vectors) -> str:
    """El vector en el formato de texto de pgvector; nueve cifras guardan un `float32`
    exacto."""
    return "[" + ",".join(f"{value:.{SIGNIFICANT_DIGITS}g}" for value in vector.tolist()) + "]"


class PgVectorIndex:
    """Un índice de pgvector sobre la tabla cargada; se borra al cerrarlo."""

    def __init__(self, cursor: Any, operator: str, effort: SearchEffort) -> None:
        self._cursor = cursor
        self._setting = EFFORT_SETTINGS[effort]
        self._effort: int | None = None
        self._statement = f"SELECT id FROM {TABLE} ORDER BY v {operator} %s::vector LIMIT %s"

    def prepare(self, query: Vectors) -> Any:
        return vector_literal(query)

    def search(self, prepared: Any, k: int, effort: int | None) -> list[int]:
        self._set_effort(effort)
        self._cursor.execute(self._statement, (prepared, k))
        return [int(row[0]) for row in self._cursor.fetchall()]

    def server_milliseconds(self, prepared: Any, k: int, effort: int | None) -> tuple[float, bool]:
        """Lo que tarda la consulta dentro del servidor, según `EXPLAIN ANALYZE`, y si
        usó el índice."""
        self._set_effort(effort)
        self._cursor.execute(EXPLAIN_SQL + self._statement, (prepared, k))
        ((explained,),) = self._cursor.fetchall()
        plan = explained[0]
        return float(plan["Execution Time"]), INDEX in str(plan["Plan"])

    def footprint(self, workspace: Path) -> dict[str, float]:
        size = relation_kib(self._cursor, INDEX)
        return {"memoria_kib": size, "disco_kib": size}

    def close(self) -> None:
        self._cursor.execute(f"DROP INDEX IF EXISTS {INDEX}")

    def _set_effort(self, effort: int | None) -> None:
        if effort is not None and effort != self._effort:
            self._cursor.execute("SELECT set_config(%s, %s, false)", (self._setting, str(effort)))
            self._effort = effort


def pgvector_techniques(
    database: Postgres, count: int, parameters: IndexParameters
) -> list[TechniqueSpec]:
    """`ivfflat` con las listas del IVF del gestor y `hnsw` con su `M` y `ef_construction`."""
    operator_class, operator = OPERATOR_CLASSES[parameters.metric]
    lists = default_list_count(count)
    cursor = database.cursor

    def ivfflat(vectors: Vectors) -> BuiltIndex:
        cursor.execute(f"DROP INDEX IF EXISTS {INDEX}")
        cursor.execute(
            f"CREATE INDEX {INDEX} ON {TABLE} USING ivfflat (v {operator_class}) "
            f"WITH (lists = {lists})"
        )
        return PgVectorIndex(cursor, operator, SearchEffort.PROBES)

    def hnsw(vectors: Vectors) -> BuiltIndex:
        cursor.execute(f"DROP INDEX IF EXISTS {INDEX}")
        cursor.execute(
            f"CREATE INDEX {INDEX} ON {TABLE} USING hnsw (v {operator_class}) "
            f"WITH (m = {parameters.hnsw_m}, ef_construction = {parameters.hnsw_ef_construction})"
        )
        return PgVectorIndex(cursor, operator, SearchEffort.EF_SEARCH)

    return [
        TechniqueSpec(
            PGVECTOR_IVF, ivfflat, SearchEffort.PROBES, default_probe_count(lists), lists
        ),
        TechniqueSpec(
            PGVECTOR_HNSW,
            hnsw,
            SearchEffort.EF_SEARCH,
            parameters.hnsw_ef_search,
            MAX_EF_SEARCH,
        ),
    ]


def median_server_milliseconds(
    index: PgVectorIndex, prepared: list[Any], k: int, effort: int | None
) -> tuple[float, int]:
    """La mediana del tiempo en el servidor y cuántas consultas usaron el índice."""
    measured = [index.server_milliseconds(query, k, effort) for query in prepared]
    return statistics.median(ms for ms, _ in measured), sum(used for _, used in measured)
