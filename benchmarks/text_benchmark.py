"""Comparación experimental: TF-IDF + coseno vs BM25 vs GIN de PostgreSQL.

Mide lo que pide el enunciado sobre los subconjuntos del corpus ESCI en español: tiempo de
construcción del índice, espacio en disco, memoria, tiempo de consulta por longitud de la
consulta (1, 3 y 5 o más palabras) y relevancia (Precision@k y Recall@k, con relevante =
etiqueta E).

    .venv/bin/python -m benchmarks.text_benchmark --corpus data/datasets/esci_es \\
        --sizes 1000 10000 100000 --repetitions 5

Los subconjuntos se leen de `<corpus>/subconjuntos/<tamaño>/` y tienen que existir: los
escribe `benchmarks.esci.subsets`, con la semilla y el cupo de consultas que fija ese
comando. El experimento no los construye por su cuenta para que no haya dos sitios donde
se decide qué consultas se miden.

**Las tres técnicas responden las mismas consultas sobre los mismos productos:**

- **TF-IDF + coseno** y **BM25** comparten un solo `InvertedIndex` construido con SPIMI
  sobre la columna `texto`; cambia solo la fórmula de `Ranker.top_k`. Cada documento es el
  par (dirección inventada, texto): el índice no lee la tabla, así que medir su
  construcción no necesita cargarla, y el `doc_id` es la posición del producto en el CSV.
- **GIN** es una tabla temporal de PostgreSQL con los mismos productos, una columna
  `tsvector` generada con `to_tsvector` y un índice GIN sobre ella. La consulta es el OR de
  sus términos, ordenado con `ts_rank_cd`. La conexión se toma de `--postgres-dsn` o de la
  variable `BENCH_POSTGRES_DSN`; sin ninguna de las dos se miden solo las técnicas del
  motor. Requiere `pip install -e ".[postgres]"`.

En los dos lados la construcción empieza con los textos ya cargados: en el motor, en una
lista en memoria; en PostgreSQL, en la tabla. El tiempo cuenta el análisis del texto y el
índice, no la lectura del CSV.

Las consultas se repiten `--repetitions` veces y las construcciones `--build-repetitions`;
cuenta la más rápida, porque otro proceso que ocupa la CPU solo puede sumar tiempo. Todas
quedan en el JSON.

Antes de anotar la relevancia se comparan los **candidatos**, los productos que contienen
algún término de la consulta: los dos analizadores no son el mismo (tokens, stopwords y
raíces), así que no tienen por qué coincidir. Las diferencias se anotan, no se esconden.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from benchmarks.common import (
    BYTES_PER_KIB,
    POSTGRES_DSN_VARIABLE,
    Report,
    add_postgres_argument,
    connect_postgres,
    directory_size,
    peak_memory_kib,
    relation_kib,
    results_directory,
    synthetic_record_id,
    time_it,
)
from benchmarks.esci.corpus import PRODUCTS_CSV, CorpusError, read_products
from benchmarks.esci.subsets import (
    DEFAULT_SIZES,
    SUBSETS_DIRECTORY,
    QueryBucket,
    load_corpus,
)
from config import EngineConfig
from index.inverted import InvertedIndex, Ranker, RankingMethod, RankingStats
from storage.record_id import RecordId
from text import DEFAULT_LANGUAGE, Analyzer

BENCHMARK_NAME = "texto"
DEFAULT_REPETITIONS = 5
DEFAULT_BUILD_REPETITIONS = 3
DEFAULT_K = 10
# El diccionario de PostgreSQL más cercano al analizador del motor: Snowball en español
# con su lista de stopwords.
DEFAULT_POSTGRES_CONFIGURATION = "spanish"
# 0 es la opción por omisión de `ts_rank_cd`: no divide por la longitud del documento.
DEFAULT_TS_RANK_NORMALIZATION = 0
# Cuántas de las consultas con más candidatos distintos entre el motor y GIN se guardan,
# con sus términos, para explicar de dónde sale la diferencia.
DIFFERENCE_EXAMPLES = 10

INVERTED = "índice invertido (SPIMI)"
TF_IDF = "TF-IDF + coseno"
BM25 = "BM25"
GIN = "GIN (PostgreSQL)"

BUILD = "construcción"
INDEX_SPACE = "espacio del índice"
BUILD_MEMORY = "memoria pico al construir"
QUERY_MEMORY = "memoria pico al consultar"
ALL_QUERIES = "todas las consultas"
CANDIDATES = "candidatos frente al motor"
BUCKET_OPERATIONS = {
    QueryBucket.ONE_WORD: "1 palabra",
    QueryBucket.THREE_WORDS: "3 palabras",
    QueryBucket.FIVE_OR_MORE_WORDS: "5+ palabras",
}

POSTGRES_SETTINGS = (
    "server_version",
    "maintenance_work_mem",
    "work_mem",
    "shared_buffers",
    "temp_buffers",
)
TABLE = "productos"
INDEX = "idx_productos_vector"
INDEX_FILE = "productos.inv"
MEMORY_RUN_DIRECTORY = "memoria"
# `plainto_tsquery` analiza la consulta como `to_tsvector` y une sus lexemas con `&`. Un
# lexema nunca contiene espacios, así que cambiar cada ` & ` por ` | ` da el OR de esos
# mismos lexemas sin volver a analizarlos. Con `regconfig` constante la expresión es
# inmutable: PostgreSQL la calcula al planificar y la lleva al índice como una constante.
OR_QUERY_SQL = (
    "SELECT replace(plainto_tsquery(%(configuration)s::regconfig, %(text)s)::text, "
    "' & ', ' | ')::tsquery AS consulta"
)
TOP_K_SQL = (
    f"SELECT posicion FROM {TABLE}, ({OR_QUERY_SQL}) AS q WHERE vector @@ consulta "
    "ORDER BY ts_rank_cd(vector, consulta, %(normalization)s) DESC, posicion LIMIT %(k)s"
)
MATCHING_SQL = f"SELECT posicion FROM {TABLE}, ({OR_QUERY_SQL}) AS q WHERE vector @@ consulta"
# El plan en JSON dice qué nodos usan el índice y cuánto tardó cada uno: la búsqueda en el
# GIN frente a releer y puntuar los `tsvector` de las filas que coinciden.
EXPLAIN_SQL = "EXPLAIN (ANALYZE, FORMAT JSON) "
LEXEMES_SQL = "SELECT tsvector_to_array(to_tsvector(%(configuration)s::regconfig, %(text)s))"


class MissingSubsetError(CorpusError):
    """Falta el subconjunto de un tamaño: hay que escribirlo antes con
    `benchmarks.esci.subsets`."""


@dataclass(frozen=True, slots=True)
class BenchmarkQuery:
    identifier: int
    text: str


@dataclass(frozen=True, slots=True)
class Workload:
    """Los productos y las consultas de un subconjunto.

    La posición de un producto en `product_ids` y `texts` es su `doc_id` en el índice del
    motor y su `posicion` en la tabla de PostgreSQL.
    """

    product_ids: tuple[str, ...]
    texts: tuple[str, ...]
    queries: dict[QueryBucket, tuple[BenchmarkQuery, ...]]
    relevant: dict[int, frozenset[str]]

    @property
    def size(self) -> int:
        return len(self.product_ids)


@dataclass(frozen=True, slots=True)
class Inspection:
    """Lo que hizo una técnica con una consulta, fuera de la medida de tiempo.

    Attributes:
        top: posiciones de los `k` primeros, en orden.
        candidates: posiciones de todos los productos con algún término de la consulta.
        terms: los términos de la consulta tras el análisis de la técnica.
        postings_read: postings recorridos; `None` si la técnica no lo dice.
        uses_index: si el plan resolvió la consulta con el índice.
        phases: milisegundos por fase según la propia técnica (el `EXPLAIN ANALYZE` de
            PostgreSQL); vacío si no los da.
    """

    top: tuple[int, ...]
    candidates: frozenset[int]
    terms: frozenset[str]
    postings_read: int | None
    uses_index: bool
    phases: dict[str, float]


class Searcher(Protocol):
    """Una técnica de búsqueda: el top-k que se cronometra y la inspección que se anota."""

    @property
    def name(self) -> str: ...

    def top_k(self, text: str) -> list[int]: ...

    def inspect(self, text: str) -> Inspection: ...


class EngineSearcher:
    """TF-IDF o BM25 sobre el índice invertido del motor, por `Ranker.top_k`."""

    def __init__(self, name: str, ranker: Ranker, method: RankingMethod, k: int) -> None:
        self._name = name
        self._ranker = ranker
        self._method = method
        self._k = k

    @property
    def name(self) -> str:
        return self._name

    def top_k(self, text: str) -> list[int]:
        return [found.doc_id for found in self._ranker.top_k(text, self._method, self._k)]

    def inspect(self, text: str) -> Inspection:
        stats = RankingStats()
        top = tuple(
            found.doc_id for found in self._ranker.top_k(text, self._method, self._k, stats=stats)
        )
        candidates = frozenset(found.doc_id for found in self._ranker.ranked(text, self._method))
        return Inspection(
            top=top,
            candidates=candidates,
            terms=frozenset(stats.document_frequencies),
            postings_read=stats.postings_read,
            uses_index=True,
            phases={},
        )


class GinSearcher:
    """El OR de los términos con el índice GIN, ordenado con `ts_rank_cd`."""

    def __init__(self, cursor: Any, configuration: str, normalization: int, k: int) -> None:
        self._cursor = cursor
        self._parameters: dict[str, Any] = {
            "configuration": configuration,
            "normalization": normalization,
            "k": k,
        }

    @property
    def name(self) -> str:
        return GIN

    def top_k(self, text: str) -> list[int]:
        return [int(row[0]) for row in self._fetch(TOP_K_SQL, text)]

    def inspect(self, text: str) -> Inspection:
        top = tuple(self.top_k(text))
        candidates = frozenset(int(row[0]) for row in self._fetch(MATCHING_SQL, text))
        ((lexemes,),) = self._fetch(LEXEMES_SQL, text)
        (((explained,),),) = self._fetch(EXPLAIN_SQL + TOP_K_SQL, text)
        index_scans = [node for node in _plan_nodes(explained["Plan"]) if _scans_index(node)]
        return Inspection(
            top=top,
            candidates=candidates,
            terms=frozenset(lexemes),
            postings_read=None,
            uses_index=bool(index_scans),
            phases={
                "planificacion": float(explained["Planning Time"]),
                "indice": sum(float(node["Actual Total Time"]) for node in index_scans),
                "ejecucion": float(explained["Execution Time"]),
            },
        )

    def _fetch(self, statement: str, text: str) -> list[tuple[Any, ...]]:
        self._cursor.execute(statement, {**self._parameters, "text": text})
        rows: list[tuple[Any, ...]] = self._cursor.fetchall()
        return rows


def _plan_nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("Plans", ()):
        yield from _plan_nodes(child)


def _scans_index(node: dict[str, Any]) -> bool:
    return node.get("Index Name") == INDEX


@dataclass(frozen=True, slots=True)
class Settings:
    """Los parámetros de una ejecución que no dependen del tamaño."""

    k: int
    repetitions: int
    build_repetitions: int
    queries_per_bucket: int | None
    k1: float
    b: float
    language: str
    postgres_dsn: str | None
    postgres_configuration: str
    ts_rank_normalization: int


def precision_at_k(top: Sequence[str], relevant: frozenset[str], k: int) -> float:
    """Fracción de los `k` primeros que son relevantes. Si la técnica devolvió menos de `k`,
    los huecos cuentan como no relevantes: el denominador es siempre `k`."""
    return sum(1 for product in top[:k] if product in relevant) / k


def recall_at_k(top: Sequence[str], relevant: frozenset[str], k: int) -> float:
    """Fracción de los relevantes que están entre los `k` primeros.

    Raises:
        ValueError: si no hay ningún relevante, porque el recall no está definido.
    """
    if not relevant:
        raise ValueError("el recall de una consulta sin relevantes no está definido")
    return sum(1 for product in top[:k] if product in relevant) / len(relevant)


def jaccard(first: frozenset[int], second: frozenset[int]) -> float:
    """Tamaño de la intersección sobre el de la unión; dos conjuntos vacíos coinciden."""
    union = first | second
    return len(first & second) / len(union) if union else 1.0


def subset_directory(corpus: Path, size: int) -> Path:
    return corpus / SUBSETS_DIRECTORY / str(size)


def load_workload(directory: Path, queries_per_bucket: int | None) -> Workload:
    """Productos, consultas por cubeta y relevantes de un subconjunto ya escrito.

    Raises:
        MissingSubsetError: si el subconjunto no existe.
    """
    if not (directory / PRODUCTS_CSV).is_file():
        raise MissingSubsetError(
            f"no hay subconjunto en {directory}: escríbelo antes con "
            "`python -m benchmarks.esci.subsets`"
        )
    subset = load_corpus(directory)
    products = list(read_products(directory / PRODUCTS_CSV))
    queries = {
        bucket: tuple(
            BenchmarkQuery(query.identifier, query.text)
            for query in subset.queries_in(bucket)[:queries_per_bucket]
        )
        for bucket in QueryBucket
    }
    return Workload(
        product_ids=tuple(product.identifier for product in products),
        texts=tuple(product.text for product in products),
        queries=queries,
        relevant={
            query.identifier: subset.relevant_products(query.identifier)
            for bucket_queries in queries.values()
            for query in bucket_queries
        },
    )


def run(corpus: Path, sizes: Sequence[int], settings: Settings, workspace: Path) -> Report:
    defaults = EngineConfig()
    report = Report(
        name=BENCHMARK_NAME,
        parameters={
            "sizes": list(sizes),
            "corpus": corpus.name,
            "k": settings.k,
            "repetitions": settings.repetitions,
            "build_repetitions": settings.build_repetitions,
            "queries_per_bucket": settings.queries_per_bucket,
            "bm25_k1": settings.k1,
            "bm25_b": settings.b,
            "language": settings.language,
            "postgres": settings.postgres_dsn is not None,
            "postgres_configuration": settings.postgres_configuration,
            "ts_rank_normalization": settings.ts_rank_normalization,
            "page_size": defaults.page_size,
            "spimi_block_bytes": defaults.spimi_block_bytes,
        },
    )
    for size in sizes:
        workload = load_workload(subset_directory(corpus, size), settings.queries_per_bucket)
        config = EngineConfig(
            data_directory=workspace / f"size-{size}", bm25_k1=settings.k1, bm25_b=settings.b
        )
        reference = _measure_engine(report, workload, config, settings)
        if settings.postgres_dsn is not None:
            _measure_postgres(report, workload, settings, settings.postgres_dsn, reference)
    return report


def _measure_engine(
    report: Report, workload: Workload, config: EngineConfig, settings: Settings
) -> list[Inspection]:
    """Construye el índice, mide los dos rankings sobre él y devuelve lo que vio TF-IDF de
    cada consulta, que es la referencia con la que se comparan los candidatos de GIN."""
    directory = config.data_directory
    directory.mkdir(parents=True, exist_ok=True)
    documents = [
        (synthetic_record_id(position), text) for position, text in enumerate(workload.texts)
    ]
    path = directory / INDEX_FILE
    builds = [
        _time_engine_build(path, documents, config, settings.language)
        for _ in range(settings.build_repetitions)
    ]
    report.add(INVERTED, BUILD, workload.size, min(builds), construcciones_ms=builds)
    _measure_build_memory(report, workload.size, documents, config, settings.language)
    with InvertedIndex(path, config) as index:
        _record_index_space(report, workload.size, index, path)
        ranker = Ranker(index, config)
        searchers = [
            EngineSearcher(TF_IDF, ranker, RankingMethod.TF_IDF, settings.k),
            EngineSearcher(BM25, ranker, RankingMethod.BM25, settings.k),
        ]
        inspected = [
            _measure_searcher(report, searcher, workload, settings) for searcher in searchers
        ]
        for searcher in searchers:
            _measure_query_memory(report, searcher, workload)
    return inspected[0]


def _time_engine_build(
    path: Path, documents: list[tuple[RecordId, str]], config: EngineConfig, language: str
) -> float:
    """Milisegundos de una construcción desde cero; borra antes el índice de la anterior."""
    for file in InvertedIndex.files_of(path):
        file.unlink(missing_ok=True)
    return time_it(
        lambda: InvertedIndex.build(path, documents, Analyzer(config, language), config).close()
    )


def _measure_build_memory(
    report: Report,
    size: int,
    documents: list[tuple[RecordId, str]],
    config: EngineConfig,
    language: str,
) -> None:
    """Pico de `tracemalloc` de una segunda construcción, en otra carpeta: trazar la
    memoria frena la construcción y no puede ir en la misma medida que su tiempo."""
    directory = config.data_directory / MEMORY_RUN_DIRECTORY
    directory.mkdir()
    try:
        peak = peak_memory_kib(
            lambda: InvertedIndex.build(
                directory / INDEX_FILE, documents, Analyzer(config, language), config
            ).close()
        )
    finally:
        shutil.rmtree(directory)
    report.add(INVERTED, BUILD_MEMORY, size, 0.0, kib=peak)


def _record_index_space(report: Report, size: int, index: InvertedIndex, path: Path) -> None:
    shape = index.describe(top_terms=0)
    report.add(
        INVERTED,
        INDEX_SPACE,
        size,
        0.0,
        kib=round(directory_size(InvertedIndex.files_of(path)), 1),
        **{
            f"{name}_kib": round(file_bytes / BYTES_PER_KIB, 1)
            for name, file_bytes in shape["file_bytes"].items()
        },
        vocabulario=shape["vocabulary"],
        postings=shape["postings"],
        bloques=shape["blocks"],
        pasadas_de_mezcla=shape["merge_passes"],
    )


def _measure_searcher(
    report: Report, searcher: Searcher, workload: Workload, settings: Settings
) -> list[Inspection]:
    """Por cubeta: una pasada que inspecciona cada consulta y calienta cachés, y luego
    `repetitions` pasadas cronometradas del top-k. El tiempo de una consulta es la pasada
    más rápida entre el número de consultas: otro proceso que ocupa la CPU solo puede
    sumar tiempo a una pasada, nunca quitárselo. Todas las pasadas quedan en el JSON.
    Devuelve las inspecciones en el orden de las cubetas."""
    inspected: list[Inspection] = []
    total_milliseconds = 0.0
    all_precisions: list[float] = []
    all_recalls: list[float] = []
    for bucket, queries in workload.queries.items():
        if not queries:
            continue
        inspections = [searcher.inspect(query.text) for query in queries]
        passes = [
            time_it(partial(_answer_all, searcher, queries)) for _ in range(settings.repetitions)
        ]
        elapsed = min(passes)
        precisions, recalls = _relevance(workload, queries, inspections, settings.k)
        report.add(
            searcher.name,
            BUCKET_OPERATIONS[bucket],
            workload.size,
            elapsed / len(queries),
            pasadas_ms=passes,
            **_query_summary(queries, inspections, precisions, recalls),
        )
        inspected += inspections
        total_milliseconds += elapsed
        all_precisions += precisions
        all_recalls += recalls
    report.add(
        searcher.name,
        ALL_QUERIES,
        workload.size,
        total_milliseconds / len(inspected),
        consultas=len(inspected),
        precision_en_k=statistics.fmean(all_precisions),
        recall_en_k=statistics.fmean(all_recalls),
        relevantes_promedio=statistics.fmean(
            len(workload.relevant[query.identifier])
            for queries in workload.queries.values()
            for query in queries
        ),
    )
    return inspected


def _answer_all(searcher: Searcher, queries: Sequence[BenchmarkQuery]) -> None:
    for query in queries:
        searcher.top_k(query.text)


def _relevance(
    workload: Workload,
    queries: Sequence[BenchmarkQuery],
    inspections: Sequence[Inspection],
    k: int,
) -> tuple[list[float], list[float]]:
    precisions, recalls = [], []
    for query, inspection in zip(queries, inspections, strict=True):
        top = [workload.product_ids[position] for position in inspection.top]
        relevant = workload.relevant[query.identifier]
        precisions.append(precision_at_k(top, relevant, k))
        recalls.append(recall_at_k(top, relevant, k))
    return precisions, recalls


def _query_summary(
    queries: Sequence[BenchmarkQuery],
    inspections: Sequence[Inspection],
    precisions: Sequence[float],
    recalls: Sequence[float],
) -> dict[str, Any]:
    """Las medias de la cubeta y, para el análisis, el detalle de cada consulta."""
    summary: dict[str, Any] = {
        "consultas": len(queries),
        "precision_en_k": statistics.fmean(precisions),
        "recall_en_k": statistics.fmean(recalls),
        "candidatos_promedio": statistics.fmean(len(item.candidates) for item in inspections),
    }
    postings = [item.postings_read for item in inspections if item.postings_read is not None]
    if postings:
        summary["postings_leidos_promedio"] = statistics.fmean(postings)
    for phase in inspections[0].phases:
        summary[f"{phase}_ms_promedio"] = statistics.fmean(
            item.phases[phase] for item in inspections
        )
    summary["por_consulta"] = [
        {
            "query_id": query.identifier,
            "precision_en_k": precision,
            "recall_en_k": recall,
            "candidatos": len(inspection.candidates),
            **(
                {}
                if inspection.postings_read is None
                else {"postings_leidos": inspection.postings_read}
            ),
        }
        for query, inspection, precision, recall in zip(
            queries, inspections, precisions, recalls, strict=True
        )
    ]
    return summary


def _measure_query_memory(report: Report, searcher: Searcher, workload: Workload) -> None:
    """Pico de memoria de Python al responder una vez todas las consultas."""

    def answer_all() -> None:
        for queries in workload.queries.values():
            _answer_all(searcher, queries)

    report.add(searcher.name, QUERY_MEMORY, workload.size, 0.0, kib=peak_memory_kib(answer_all))


def _measure_postgres(
    report: Report,
    workload: Workload,
    settings: Settings,
    dsn: str,
    reference: list[Inspection],
) -> None:
    from psycopg import sql

    size = workload.size
    configuration = sql.Literal(settings.postgres_configuration)
    with connect_postgres(dsn) as connection, connection.cursor() as cursor:
        report.parameters.update(_postgres_settings(cursor))
        cursor.execute(f"CREATE TEMP TABLE {TABLE} (posicion integer, texto text)")
        with cursor.copy(f"COPY {TABLE} (posicion, texto) FROM STDIN") as copy:
            for position, text in enumerate(workload.texts):
                copy.write_row((position, text))
        add_vectors = sql.SQL(
            "ALTER TABLE {} ADD COLUMN vector tsvector "
            "GENERATED ALWAYS AS (to_tsvector({}::regconfig, texto)) STORED"
        ).format(sql.Identifier(TABLE), configuration)
        builds = [_time_gin_build(cursor, add_vectors) for _ in range(settings.build_repetitions)]
        vectors, index = min(builds, key=sum)
        report.add(
            GIN,
            BUILD,
            size,
            vectors + index,
            vectores_ms=vectors,
            indice_ms=index,
            construcciones_ms=[sum(build) for build in builds],
        )
        cursor.execute(f"ANALYZE {TABLE}")
        report.add(GIN, INDEX_SPACE, size, 0.0, **_postgres_space(cursor))
        # Con pocas filas, o con términos muy comunes, PostgreSQL prefiere recorrer la
        # tabla; aquí se mide el índice.
        cursor.execute("SET enable_seqscan = off")
        # psycopg prepara una sentencia que se repite y PostgreSQL acaba usando un plan
        # genérico, en el que la consulta ya no es una constante y `ts_rank_cd` la vuelve
        # a analizar por cada fila. Cada consulta se planifica con su propio texto, como
        # una consulta suelta.
        cursor.execute("SET plan_cache_mode = force_custom_plan")
        searcher = GinSearcher(
            cursor, settings.postgres_configuration, settings.ts_rank_normalization, settings.k
        )
        inspected = _measure_searcher(report, searcher, workload, settings)
    _compare_candidates(report, workload, reference, inspected)


def _time_gin_build(cursor: Any, add_vectors: Any) -> tuple[float, float]:
    """Milisegundos de calcular la columna `tsvector` y de crear el GIN sobre ella; quita
    antes la columna de la construcción anterior, y con ella su índice."""
    cursor.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS vector")
    vectors = time_it(lambda: cursor.execute(add_vectors))
    index = time_it(lambda: cursor.execute(f"CREATE INDEX {INDEX} ON {TABLE} USING GIN (vector)"))
    return vectors, index


def _postgres_settings(cursor: Any) -> dict[str, str]:
    """La versión de PostgreSQL y la memoria con la que construye el índice y consulta."""
    settings = {}
    for name in POSTGRES_SETTINGS:
        cursor.execute(f"SHOW {name}")
        (settings[f"postgres_{name}"],) = cursor.fetchone()
    return settings


def _postgres_space(cursor: Any) -> dict[str, float]:
    cursor.execute(f"SELECT sum(pg_column_size(vector)) FROM {TABLE}")
    (vector_bytes,) = cursor.fetchone()
    return {
        "kib": relation_kib(cursor, INDEX),
        "vectores_kib": round(int(vector_bytes) / BYTES_PER_KIB, 1),
        "tabla_kib": relation_kib(cursor, TABLE),
    }


def _compare_candidates(
    report: Report,
    workload: Workload,
    engine: Sequence[Inspection],
    postgres: Sequence[Inspection],
) -> None:
    """Cuánto se parecen los candidatos y los términos de GIN a los del motor."""
    queries = [query for bucket in workload.queries.values() for query in bucket]
    differences: list[dict[str, Any]] = []
    similarities: list[float] = []
    for query, ours, theirs in zip(queries, engine, postgres, strict=True):
        similarities.append(jaccard(ours.candidates, theirs.candidates))
        if ours.candidates != theirs.candidates or ours.terms != theirs.terms:
            differences.append(
                {
                    "consulta": query.text,
                    "terminos_motor": sorted(ours.terms),
                    "terminos_postgres": sorted(theirs.terms),
                    "solo_motor": len(ours.candidates - theirs.candidates),
                    "solo_postgres": len(theirs.candidates - ours.candidates),
                }
            )
    differences.sort(key=lambda item: -(item["solo_motor"] + item["solo_postgres"]))
    pairs = list(zip(engine, postgres, strict=True))
    report.add(
        GIN,
        CANDIDATES,
        workload.size,
        0.0,
        consultas=len(pairs),
        candidatos_identicos=sum(
            1 for ours, theirs in pairs if ours.candidates == theirs.candidates
        ),
        terminos_identicos=sum(1 for ours, theirs in pairs if ours.terms == theirs.terms),
        jaccard_promedio=statistics.fmean(similarities),
        jaccard_minimo=min(similarities),
        candidatos_motor=sum(len(ours.candidates) for ours, _ in pairs),
        candidatos_postgres=sum(len(theirs.candidates) for _, theirs in pairs),
        en_los_dos=sum(len(ours.candidates & theirs.candidates) for ours, theirs in pairs),
        usa_el_indice=f"{sum(1 for _, theirs in pairs if theirs.uses_index)}/{len(pairs)}",
        mayores_diferencias=differences[:DIFFERENCE_EXAMPLES],
    )


def main() -> None:
    defaults = EngineConfig()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus", type=Path, required=True, help="carpeta del corpus ESCI con sus subconjuntos"
    )
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument(
        "--queries",
        type=int,
        help="consultas por cubeta; por omisión, todas las del subconjunto",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=DEFAULT_REPETITIONS,
        help="pasadas cronometradas de las consultas; cuenta la más rápida",
    )
    parser.add_argument(
        "--build-repetitions",
        type=int,
        default=DEFAULT_BUILD_REPETITIONS,
        help="construcciones de cada índice; cuenta la más rápida",
    )
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="el k de top-k, P@k y R@k")
    parser.add_argument("--k1", type=float, default=defaults.bm25_k1, help="k1 de BM25")
    parser.add_argument("--b", type=float, default=defaults.bm25_b, help="b de BM25")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="idioma del analizador")
    add_postgres_argument(parser)
    parser.add_argument(
        "--postgres-configuration",
        default=DEFAULT_POSTGRES_CONFIGURATION,
        help="configuración de búsqueda de texto de PostgreSQL",
    )
    parser.add_argument(
        "--ts-rank-normalization",
        type=int,
        default=DEFAULT_TS_RANK_NORMALIZATION,
        help="opción de normalización de ts_rank_cd",
    )
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    if arguments.postgres_dsn is None:
        print(f"Sin --postgres-dsn ni ${POSTGRES_DSN_VARIABLE}: no se mide GIN.")
    settings = Settings(
        k=arguments.k,
        repetitions=arguments.repetitions,
        build_repetitions=arguments.build_repetitions,
        queries_per_bucket=arguments.queries,
        k1=arguments.k1,
        b=arguments.b,
        language=arguments.language,
        postgres_dsn=arguments.postgres_dsn,
        postgres_configuration=arguments.postgres_configuration,
        ts_rank_normalization=arguments.ts_rank_normalization,
    )
    workspace = Path(tempfile.mkdtemp(prefix="minigestor-bench-"))
    try:
        report = run(arguments.corpus, arguments.sizes, settings, workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    path = report.save(arguments.output)
    print(report.render())
    print(f"\nResultados crudos en {path}")


if __name__ == "__main__":
    main()
