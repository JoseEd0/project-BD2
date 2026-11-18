"""HNSW e IVF sobre una columna VECTOR(n), de punta a punta por SQL (issue #68).

Es lo que pagará un servicio que guarde embeddings en el gestor —el RAG con
multilingual-e5-small, de 384 dimensiones— y los busque con
`SIMILAR_TO(emb, $1, k = 10)`:

    .venv/bin/python -m benchmarks.vector_benchmark --sizes 1000 10000 100000

**Los vectores son sintéticos**, de una mezcla de gaussianas normalizada que se parece a
un conjunto de embeddings (ver `benchmarks.vectors.mixture`): uniformes en 384 dimensiones
serían un caso patológico para cualquier índice aproximado. Los tamaños son subconjuntos
anidados de la misma colección, y las consultas, de la misma mezcla con otra semilla.

Por cada tamaño, sobre una tabla `vectores (id INT PRIMARY KEY, parte INT, emb VECTOR(n))`:

- **construcción**: `CREATE INDEX ... USING HNSW | IVF (emb) WITH (metric = 'cosine')` con
  los parámetros por omisión del gestor, y el espacio en disco de cada índice;
- **consulta**: cada `SIMILAR_TO` por SQL —analizar, planificar, buscar y leer las filas—
  se responde `--repetitions` veces y cuenta su mediana; de las consultas salen la mediana
  (p50) y el percentil 95. **Recall@k** contra la búsqueda exacta calculada aparte con
  numpy en `float64`;
- **búsqueda exacta por SQL**: la misma consulta con `WITH METRIC = dot`, que el índice no
  ordena y por eso compara contra todas las filas. Con vectores de norma 1, el producto
  punto ordena igual que el coseno, así que su Recall@k es 1. La primera consulta arma la
  búsqueda exacta leyendo la tabla: se reporta como su construcción;
- **con filtro**: `AND parte = p`, que deja el 10 % de las filas por un índice hash; el
  filtro va dentro de la búsqueda;
- **curvas** de recall contra latencia en el tamaño más grande, barriendo `ef_search` y
  `probes`.

FAISS construye y busca con `--threads` hilos (uno por omisión), como en el experimento de
la Parte 4.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from benchmarks.common import (
    BYTES_PER_KIB,
    Report,
    integer_of,
    load_average,
    machine_description,
    results_directory,
    time_it,
)
from benchmarks.multimedia.quality import GroundTruth, recall_at_k
from benchmarks.vectors.mixture import Mixture
from benchmarks.vectors.names import (
    BUILD,
    CURVE,
    FILTERED_QUERY,
    LOAD,
    QUERY,
    REPORT,
    SPACE,
    SQL_EXACT,
    SQL_HNSW,
    SQL_IVF,
    TABLE_LOAD,
)
from config import EngineConfig
from index.vector import VectorMetric
from index.vector.metrics import Vectors
from query.engine import Engine
from query.plan import MILLISECONDS_PER_SECOND

DEFAULT_SIZES = (1_000, 10_000, 100_000)
DEFAULT_DIMENSION = 384
DEFAULT_CLUSTERS = 1_000
DEFAULT_INTRINSIC_DIMENSION = 24
DEFAULT_SPREAD = 1.0
DEFAULT_NOISE = 0.5
DEFAULT_QUERIES = 500
DEFAULT_FILTER_QUERIES = 100
DEFAULT_REPETITIONS = 3
DEFAULT_K = 10
DEFAULT_THREADS = 1
DEFAULT_SEED = 20261117
DEFAULT_EF_SEARCH = (10, 20, 40, 80, 160, 320)
DEFAULT_PROBES = (1, 2, 4, 8, 16, 32, 64, 128)
TAIL_PERCENTILE = 95
STRUCTURE_KEYS = ("lists", "probes", "m", "ef_search", "vectors", "zero_vectors")
# `parte = id % 10`: el filtro de una consulta deja una de cada diez filas.
FILTER_PARTS = 10
FIRST_ID = 1
TABLE = "vectores"
METRIC = VectorMetric.COSINE
# Con la norma 1 de la mezcla, el producto punto ordena igual que el coseno: es la métrica
# que el índice no conoce y que obliga a la búsqueda exacta.
EXACT_METRIC = VectorMetric.DOT


@dataclass(frozen=True, slots=True)
class SqlMethod:
    """Un índice por SQL: su técnica en los resultados, su método y su esfuerzo."""

    technique: str
    method: str
    effort_option: str

    @property
    def index_name(self) -> str:
        return f"idx_{self.method.lower()}"


# La búsqueda de una consulta, con su número entre las consultas.
QuerySearch = Callable[[Vectors, int], list[int]]


HNSW = SqlMethod(SQL_HNSW, "HNSW", "ef_search")
IVF = SqlMethod(SQL_IVF, "IVF", "probes")
METHODS = (HNSW, IVF)


@dataclass(frozen=True, slots=True)
class Settings:
    sizes: tuple[int, ...]
    mixture: Mixture
    queries: int
    filter_queries: int
    repetitions: int
    k: int
    threads: int
    ef_search: tuple[int, ...]
    probes: tuple[int, ...]

    def efforts(self, method: SqlMethod) -> tuple[int, ...]:
        return self.ef_search if method is HNSW else self.probes


@dataclass(frozen=True, slots=True)
class Timed:
    """Las posiciones que devolvió cada consulta y su mediana de tiempo, en ms."""

    found: list[list[int]]
    milliseconds: list[float]

    @property
    def median(self) -> float:
        return float(np.median(self.milliseconds))

    @property
    def tail(self) -> float:
        return float(np.percentile(self.milliseconds, TAIL_PERCENTILE))


class VectorTable:
    """La tabla del experimento en un gestor propio, con sus índices."""

    def __init__(self, vectors: Vectors, directory: Path) -> None:
        self._config = EngineConfig(data_directory=directory)
        self._engine = Engine(self._config)
        self._engine.execute(
            f"CREATE TABLE {TABLE} (id INT PRIMARY KEY, parte INT, emb VECTOR({vectors.shape[1]}))"
        )
        self._engine.execute(f"CREATE INDEX idx_parte ON {TABLE} USING HASH (parte)")
        self.load_ms = time_it(lambda: self._insert(vectors))

    def _insert(self, vectors: Vectors) -> None:
        table = self._engine.table(TABLE)
        for position, vector in enumerate(vectors):
            key = FIRST_ID + position
            table.insert((key, key % FILTER_PARTS, tuple(vector.tolist())))

    def create_index(self, method: SqlMethod) -> float:
        statement = (
            f"CREATE INDEX {method.index_name} ON {TABLE} USING {method.method} (emb) "
            f"WITH (metric = '{METRIC.value}')"
        )
        return time_it(lambda: self._engine.execute(statement))

    def structure(self, method: SqlMethod) -> dict[str, Any]:
        for described in self._engine.describe_table(TABLE)["indexes"]:
            if described["name"] == method.index_name:
                found: dict[str, Any] = described["structure"]
                return found
        raise KeyError(method.index_name)

    def index_kib(self, method: SqlMethod) -> float:
        files = self._config.data_directory.glob(f"{TABLE}.{method.index_name}.idx*")
        return sum(path.stat().st_size for path in files) / BYTES_PER_KIB

    def searcher(self, k: int, tail: str, filtered: bool) -> QuerySearch:
        """La búsqueda de cada consulta con esa cola de SQL; con `filtered`, la consulta
        número `n` filtra la parte `n % 10`."""

        def search(query: Vectors, number: int) -> list[int]:
            part = number % FILTER_PARTS if filtered else None
            return self.similar(query, k, tail, part)

        return search

    def similar(self, query: Vectors, k: int, tail: str, filter_part: int | None) -> list[int]:
        condition = "" if filter_part is None else f" AND parte = {filter_part}"
        statement = f"SELECT id FROM {TABLE} WHERE SIMILAR_TO(emb, $1, k = {k}){condition} {tail}"
        rows = self._engine.execute(statement, parameters=[query.tolist()]).rows
        return [integer_of(row[0]) - FIRST_ID for row in rows]

    def close(self) -> None:
        self._engine.close()


def run(settings: Settings, workspace: Path) -> Report:
    """Mide cada tamaño en su propia base dentro de `workspace`."""
    largest = max(settings.sizes)
    collection, _ = settings.mixture.collection(largest)
    queries = settings.mixture.queries(settings.queries)
    report = Report(REPORT, _parameters(settings))
    with _faiss_threads(settings.threads):
        for size in sorted(settings.sizes):
            print(f"{size} vectores de {settings.mixture.dimension} dimensiones")
            _measure_size(report, settings, collection[:size], queries, size == largest, workspace)
    report.parameters["carga_media_al_terminar"] = load_average()
    return report


def _measure_size(
    report: Report,
    settings: Settings,
    vectors: Vectors,
    queries: Vectors,
    sweep: bool,
    workspace: Path,
) -> None:
    size = len(vectors)
    truth = GroundTruth(vectors, queries, METRIC, settings.k)
    filtered = FilteredTruth(vectors, queries[: settings.filter_queries], settings.k)
    table = VectorTable(vectors, workspace / str(size))
    try:
        report.add(TABLE_LOAD, LOAD, size, table.load_ms)
        for method in METHODS:
            _measure_index(report, settings, table, method, queries, truth, filtered, size)
            if sweep:
                _measure_curve(report, settings, table, method, queries, truth, size)
        _measure_exact(report, settings, table, queries, truth, size)
    finally:
        table.close()


def _measure_index(
    report: Report,
    settings: Settings,
    table: VectorTable,
    method: SqlMethod,
    queries: Vectors,
    truth: GroundTruth,
    filtered: FilteredTruth,
    size: int,
) -> None:
    built_ms = table.create_index(method)
    structure = table.structure(method)
    kept = {key: structure[key] for key in STRUCTURE_KEYS if key in structure}
    report.add(method.technique, BUILD, size, built_ms, **kept)
    report.add(method.technique, SPACE, size, 0.0, kib=round(table.index_kib(method), 1))
    using = f"USING {method.method}"
    timed = _timed(table.searcher(settings.k, using, False), queries, settings.repetitions)
    report.add(
        method.technique,
        QUERY,
        size,
        timed.median,
        p95_ms=timed.tail,
        recall_en_k=_mean_recall(truth, timed.found),
    )
    _measure_filtered(report, settings, table, method.technique, using, queries, filtered, size)
    seconds = built_ms / MILLISECONDS_PER_SECOND
    print(f"  {method.technique}: {seconds:.1f} s, {timed.median:.2f} ms por consulta")


def _measure_exact(
    report: Report,
    settings: Settings,
    table: VectorTable,
    queries: Vectors,
    truth: GroundTruth,
    size: int,
) -> None:
    exact = f"USING {HNSW.method} WITH METRIC = {EXACT_METRIC.value}"
    first_ms = time_it(lambda: table.similar(queries[0], settings.k, exact, None))
    report.add(SQL_EXACT, BUILD, size, first_ms, nota="primera consulta: lee la tabla")
    timed = _timed(table.searcher(settings.k, exact, False), queries, settings.repetitions)
    report.add(
        SQL_EXACT,
        QUERY,
        size,
        timed.median,
        p95_ms=timed.tail,
        recall_en_k=_mean_recall(truth, timed.found),
    )


def _measure_filtered(
    report: Report,
    settings: Settings,
    table: VectorTable,
    technique: str,
    using: str,
    queries: Vectors,
    filtered: FilteredTruth,
    size: int,
) -> None:
    chosen = queries[: settings.filter_queries]
    timed = _timed(table.searcher(settings.k, using, True), chosen, settings.repetitions)
    recalls = [filtered.recall(number, result) for number, result in enumerate(timed.found)]
    report.add(
        technique,
        FILTERED_QUERY,
        size,
        timed.median,
        p95_ms=timed.tail,
        recall_en_k=float(np.mean(recalls)),
    )


def _measure_curve(
    report: Report,
    settings: Settings,
    table: VectorTable,
    method: SqlMethod,
    queries: Vectors,
    truth: GroundTruth,
    size: int,
) -> None:
    default = table.structure(method)[method.effort_option]
    for effort in sorted({*settings.efforts(method), default}):
        tail = f"USING {method.method} WITH {method.effort_option} = {effort}"
        timed = _timed(table.searcher(settings.k, tail, False), queries, settings.repetitions)
        report.add(
            method.technique,
            CURVE,
            size,
            timed.median,
            esfuerzo=effort,
            por_omision=effort == default,
            recall_en_k=_mean_recall(truth, timed.found),
        )


class FilteredTruth:
    """La verdad de las consultas con `parte = número % 10`: el k-ésimo puntaje exacto
    entre las filas de su parte."""

    def __init__(self, vectors: Vectors, queries: Vectors, k: int) -> None:
        self._vectors = vectors.astype(np.float64)
        self._queries = queries.astype(np.float64)
        self._k = k
        self._kth = [self._kth_score(number) for number in range(len(queries))]

    def recall(self, number: int, found: Sequence[int]) -> float:
        scores = self._vectors[np.asarray(found, dtype=np.int64)] @ self._queries[number]
        return recall_at_k(scores.tolist(), self._kth[number], self._k)

    def _kth_score(self, number: int) -> float:
        part = number % FILTER_PARTS
        keys = np.arange(len(self._vectors)) + FIRST_ID
        positions = np.flatnonzero(keys % FILTER_PARTS == part)
        scores = self._vectors[positions] @ self._queries[number]
        return float(np.partition(scores, -self._k)[-self._k])


def _timed(search: QuerySearch, queries: Vectors, repetitions: int) -> Timed:
    """Cada consulta se responde `repetitions` veces y cuenta su mediana: el equipo puede
    estar compartido, y la mediana no se mueve por una pasada interrumpida."""
    found: list[list[int]] = []
    times: list[float] = []
    for number, query in enumerate(queries):
        measured = []
        for _ in range(repetitions):
            started = time.perf_counter()
            result = search(query, number)
            measured.append((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
        found.append(result)
        times.append(statistics.median(measured))
    return Timed(found, times)


def _mean_recall(truth: GroundTruth, found: list[list[int]]) -> float:
    return float(np.mean([truth.recall(number, result) for number, result in enumerate(found)]))


def _parameters(settings: Settings) -> dict[str, Any]:
    mixture = settings.mixture
    return {
        "tamanos": list(settings.sizes),
        "dimension": mixture.dimension,
        "mezcla": {
            "grupos": mixture.clusters,
            "dimension_intrinseca": mixture.intrinsic_dimension,
            "dispersion": mixture.spread,
            "ruido": mixture.noise,
            "semilla": mixture.seed,
        },
        "metrica": METRIC.value,
        "consultas": settings.queries,
        "consultas_con_filtro": settings.filter_queries,
        "filtro": f"parte = número de consulta % {FILTER_PARTS} (10 % de las filas, índice hash)",
        "repeticiones": settings.repetitions,
        "k": settings.k,
        "hilos": settings.threads,
        "ef_search_barridos": list(settings.ef_search),
        "probes_barridos": list(settings.probes),
        "versiones": {"faiss": faiss.__version__, "numpy": np.__version__},
        "maquina": machine_description(),
    }


@contextmanager
def _faiss_threads(threads: int) -> Iterator[None]:
    previous = faiss.omp_get_max_threads()
    faiss.omp_set_num_threads(threads)
    try:
        yield
    finally:
        faiss.omp_set_num_threads(previous)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--dimension", type=int, default=DEFAULT_DIMENSION)
    parser.add_argument("--clusters", type=int, default=DEFAULT_CLUSTERS)
    parser.add_argument("--intrinsic-dimension", type=int, default=DEFAULT_INTRINSIC_DIMENSION)
    parser.add_argument("--spread", type=float, default=DEFAULT_SPREAD)
    parser.add_argument("--noise", type=float, default=DEFAULT_NOISE)
    parser.add_argument("--queries", type=int, default=DEFAULT_QUERIES)
    parser.add_argument("--filter-queries", type=int, default=DEFAULT_FILTER_QUERIES)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--ef-search", type=int, nargs="+", default=list(DEFAULT_EF_SEARCH))
    parser.add_argument("--probes", type=int, nargs="+", default=list(DEFAULT_PROBES))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    settings = Settings(
        sizes=tuple(arguments.sizes),
        mixture=Mixture(
            dimension=arguments.dimension,
            clusters=arguments.clusters,
            intrinsic_dimension=arguments.intrinsic_dimension,
            spread=arguments.spread,
            noise=arguments.noise,
            seed=arguments.seed,
        ),
        queries=arguments.queries,
        filter_queries=arguments.filter_queries,
        repetitions=arguments.repetitions,
        k=arguments.k,
        threads=arguments.threads,
        ef_search=tuple(arguments.ef_search),
        probes=tuple(arguments.probes),
    )
    workspace = Path(tempfile.mkdtemp(prefix="minigestor-vectores-"))
    try:
        report = run(settings, workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    path = report.save(arguments.output)
    print(report.render())
    print(f"\nResultados crudos en {path}")


if __name__ == "__main__":
    main()
