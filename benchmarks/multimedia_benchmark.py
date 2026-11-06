"""Comparación experimental de la Parte 4: IVF vs HNSW y euclidiana vs coseno.

Sobre fotos de producto (ABO) y clips de voz (Speech Commands), en subconjuntos anidados de
1 000, 10 000 y 100 000 objetos, con consultas que no están en la base:

    .venv/bin/python -m benchmarks.multimedia_benchmark \\
        --images data/datasets/abo --audio data/datasets/speech_commands \\
        --sizes 1000 10000 100000 --repetitions 3

**Todas las técnicas comparan los mismos vectores.** Cada colección pasa una vez por el
camino de `CREATE INDEX` (`benchmarks.multimedia.features`): SIFT o MFCC, un vocabulario de
k-means entrenado con una muestra de esa colección —uno por tamaño, como lo haría el
gestor—, conteos de palabras y TF-IDF. Sobre esos vectores se construyen:

- la búsqueda exacta, el IVF y el HNSW **del gestor** (`index.vector`, sobre FAISS);
- el IVF y el HNSW de **FAISS directo**, con los mismos parámetros y sin la capa del gestor;
- el HNSW de **USearch** (si está instalado, `.[usearch]`);
- `ivfflat` y `hnsw` de **pgvector** (con `--postgres-dsn` o `BENCH_POSTGRES_DSN`).

Además, el gestor se mide **por SQL**: `CREATE INDEX` de punta a punta y `SIMILAR_TO` con
el archivo de la consulta, en los tamaños de `--sql-sizes`.

**Qué se mide.** Construcción (cada etapa de las características y cada índice), espacio en
memoria y en disco, y por cada consulta su tiempo, su Recall@k contra la búsqueda exacta y
cuántos de sus vecinos son de su clase (`benchmarks.multimedia.quality`). El esfuerzo de
búsqueda se barre (`--ivf-probes`, `--ef-search`) para las curvas de recall contra
latencia; la medida principal es la del esfuerzo por omisión del gestor.

**Cómo se cronometra.** Cada consulta se responde `--repetitions` veces y cuenta su
mediana, porque el equipo puede estar compartido y la mediana no se mueve por una pasada
interrumpida; de ahí salen la mediana y el percentil 95 entre consultas. Cada índice se
construye `--build-repetitions` veces y cuenta la mediana. Todas las repeticiones, la
máquina, los hilos, las versiones y la carga del sistema quedan en el JSON.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import statistics
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from benchmarks.common import (
    BYTES_PER_KIB,
    POSTGRES_DSN_VARIABLE,
    Report,
    add_postgres_argument,
    load_average,
    machine_description,
    results_directory,
)
from benchmarks.multimedia.collection import (
    DatasetSpec,
    MediaFile,
    audio_dataset,
    images_dataset,
)
from benchmarks.multimedia.features import (
    CollectionFeatures,
    QueryDescriptors,
    collection_features,
    query_descriptors,
    query_vectors,
)
from benchmarks.multimedia.names import (
    BUILD,
    CREATE_INDEX,
    CURVE,
    ENGINE_HNSW,
    EXTRACTION_STAGE,
    FEATURE_STORE,
    FEATURES,
    GALLERY,
    MEMORY,
    METRIC_LABELS,
    PGVECTOR_TABLE,
    QUANTIZATION_STAGE,
    QUERY,
    QUERY_EXTRACTION_STAGE,
    QUERY_VECTOR_STAGE,
    RANDOM_NEIGHBOURS,
    RAW_VECTORS,
    READING_STAGE,
    SAMPLE_STAGE,
    SQL_HNSW,
    SQL_IVF,
    VOCABULARY_STAGE,
    dataset_operation,
    metric_operation,
)
from benchmarks.multimedia.pgvector import (
    PgVectorIndex,
    Postgres,
    median_server_milliseconds,
    pgvector_techniques,
    postgres,
)
from benchmarks.multimedia.quality import (
    GalleryCase,
    GroundTruth,
    class_precision_at_k,
    gallery_cases,
    random_class_precision,
)
from benchmarks.multimedia.sql import (
    SQL_HNSW_INDEX,
    SQL_IVF_INDEX,
    SqlCollection,
    SqlIndex,
    sql_config,
)
from benchmarks.multimedia.subsets import DEFAULT_SIZES
from benchmarks.multimedia.techniques import (
    BuiltIndex,
    IndexParameters,
    SearchEffort,
    TechniqueSpec,
    engine_techniques,
    faiss_techniques,
    retained_python_kib,
    usearch_technique,
)
from config import EngineConfig
from index.vector import VectorMetric, vector_metric_named
from index.vector.metrics import Vectors
from query.plan import MILLISECONDS_PER_SECOND

BENCHMARK_NAME = "multimedia"
DEFAULT_K = 10
DEFAULT_REPETITIONS = 3
DEFAULT_BUILD_REPETITIONS = 3
# Con más de 1 hilo, FAISS y USearch reparten la construcción y pgvector añade procesos de
# `CREATE INDEX`; con 1, las tres construyen igual y la comparación es de algoritmos.
DEFAULT_THREADS = 1
DEFAULT_METRICS = ("euclidean", "cosine", "dot")
DEFAULT_IVF_PROBES = (1, 2, 4, 8, 16, 32, 64, 128)
DEFAULT_EF_SEARCH = (10, 20, 40, 80, 160, 320)
DEFAULT_GALLERY_CASES = 3
DEFAULT_GALLERY_NEIGHBOURS = 5
DEFAULT_SEED = 20261106
TAIL_PERCENTILE = 95
USEARCH_MODULE = "usearch"
FEATURES_DIRECTORY = "caracteristicas"
SQL_DIRECTORY = "sql"
SCRATCH_DIRECTORY = "indices"


@dataclass(frozen=True, slots=True)
class Settings:
    """Los parámetros de una ejecución que no dependen del tamaño."""

    sizes: tuple[int, ...]
    metrics: tuple[VectorMetric, ...]
    k: int = DEFAULT_K
    queries: int | None = None
    repetitions: int = DEFAULT_REPETITIONS
    build_repetitions: int = DEFAULT_BUILD_REPETITIONS
    threads: int = DEFAULT_THREADS
    ivf_probes: tuple[int, ...] = DEFAULT_IVF_PROBES
    ef_search: tuple[int, ...] = DEFAULT_EF_SEARCH
    sql_sizes: tuple[int, ...] = ()
    sql_metric: VectorMetric = VectorMetric.COSINE
    gallery_metric: VectorMetric = VectorMetric.COSINE
    gallery_cases: int = DEFAULT_GALLERY_CASES
    gallery_neighbours: int = DEFAULT_GALLERY_NEIGHBOURS
    seed: int = DEFAULT_SEED
    usearch: bool = True
    postgres_dsn: str | None = None
    postgres_maintenance_work_mem: str | None = None
    features_directory: Path | None = None
    engine: EngineConfig = field(default_factory=EngineConfig)


@dataclass(frozen=True, slots=True)
class QueryRun:
    """Lo que encontró una técnica para cada consulta y la mediana de sus tiempos."""

    found: list[list[int]]
    milliseconds: list[float]

    @property
    def median(self) -> float:
        return statistics.median(self.milliseconds)

    @property
    def tail(self) -> float:
        return float(np.percentile(self.milliseconds, TAIL_PERCENTILE))


@dataclass(frozen=True, slots=True)
class Collection:
    """Lo que comparten todas las medidas de un dataset en un tamaño."""

    spec: DatasetSpec
    files: tuple[MediaFile, ...]
    queries: tuple[MediaFile, ...]
    vectors: Vectors
    query_vectors: Vectors

    @property
    def size(self) -> int:
        return len(self.files)

    def labels_of(self, positions: Sequence[int]) -> list[str]:
        return [self.files[position].label for position in positions]


def run(datasets: Sequence[DatasetSpec], settings: Settings, workspace: Path) -> Report:
    report = Report(BENCHMARK_NAME, parameters=_parameters(settings))
    with _faiss_threads(settings.threads), _optional_postgres(settings) as database:
        if database is not None:
            report.parameters.update(database.describe())
        for spec in datasets:
            _measure_dataset(report, spec, settings, workspace, database)
    report.parameters["carga_media_al_terminar"] = load_average()
    return report


def _parameters(settings: Settings) -> dict[str, Any]:
    config = settings.engine
    return {
        "sizes": list(settings.sizes),
        "metrics": [metric.value for metric in settings.metrics],
        "k": settings.k,
        "queries": settings.queries,
        "repetitions": settings.repetitions,
        "build_repetitions": settings.build_repetitions,
        "threads": settings.threads,
        "feature_workers": config.feature_workers,
        "ivf_probes": list(settings.ivf_probes),
        "ef_search": list(settings.ef_search),
        "hnsw_m": config.hnsw_m,
        "hnsw_ef_construction": config.hnsw_ef_construction,
        "hnsw_ef_search": config.hnsw_ef_search,
        "vocabulary_size": config.vocabulary_size,
        "vocabulary_training_objects": config.vocabulary_training_objects,
        "kmeans_iterations": config.kmeans_iterations,
        "image_max_descriptors": config.image_max_descriptors,
        "sql_sizes": list(settings.sql_sizes),
        "sql_metric": settings.sql_metric.value,
        "gallery_metric": settings.gallery_metric.value,
        "seed": settings.seed,
        "postgres": settings.postgres_dsn is not None,
        "postgres_maintenance_work_mem_sesion": settings.postgres_maintenance_work_mem,
        "features_directory": None
        if settings.features_directory is None
        else str(settings.features_directory),
        "maquina": machine_description(),
        "versiones": _versions(settings.usearch),
    }


def _versions(usearch: bool) -> dict[str, str]:
    import cv2
    import librosa

    found = {
        "faiss": faiss.__version__,
        "numpy": np.__version__,
        "opencv": cv2.__version__,
        "librosa": librosa.__version__,
    }
    if usearch:
        import usearch as usearch_module

        found["usearch"] = usearch_module.__version__
    return found


@contextmanager
def _faiss_threads(threads: int) -> Iterator[None]:
    previous = faiss.omp_get_max_threads()
    faiss.omp_set_num_threads(threads)
    try:
        yield
    finally:
        faiss.omp_set_num_threads(previous)


@contextmanager
def _optional_postgres(settings: Settings) -> Iterator[Postgres | None]:
    if settings.postgres_dsn is None:
        yield None
        return
    with postgres(
        settings.postgres_dsn, settings.postgres_maintenance_work_mem, settings.threads
    ) as database:
        yield database


def _measure_dataset(
    report: Report,
    spec: DatasetSpec,
    settings: Settings,
    workspace: Path,
    database: Postgres | None,
) -> None:
    queries = spec.queries(settings.queries)
    descriptors = query_descriptors(queries, settings.engine)
    for size in settings.sizes:
        files = spec.base(size)
        features = collection_features(
            files,
            settings.engine,
            settings.engine.feature_workers,
            _features_directory(settings, workspace, spec, size),
        )
        try:
            _measure_size(
                report, spec, files, queries, descriptors, features, settings, workspace, database
            )
        finally:
            features.store.close()


def _features_directory(settings: Settings, workspace: Path, spec: DatasetSpec, size: int) -> Path:
    root = settings.features_directory or workspace / FEATURES_DIRECTORY
    return root / spec.directory.name / str(size)


def _measure_size(
    report: Report,
    spec: DatasetSpec,
    files: tuple[MediaFile, ...],
    queries: tuple[MediaFile, ...],
    descriptors: QueryDescriptors,
    features: CollectionFeatures,
    settings: Settings,
    workspace: Path,
    database: Postgres | None,
) -> None:
    vectors, vector_milliseconds = query_vectors(features.store, descriptors)
    collection = Collection(spec, files, queries, features.vectors, vectors)
    _record_features(report, collection, features, descriptors, vector_milliseconds)
    if database is not None:
        report.add(
            PGVECTOR_TABLE,
            dataset_operation(spec.name, MEMORY),
            collection.size,
            0.0,
            disco_kib=database.load(collection.vectors),
        )
    scratch = workspace / SCRATCH_DIRECTORY
    scratch.mkdir(parents=True, exist_ok=True)
    truths: dict[VectorMetric, GroundTruth] = {}
    for metric in settings.metrics:
        truths[metric] = GroundTruth(
            collection.vectors, collection.query_vectors, metric, settings.k
        )
        found = _measure_metric(
            report, collection, metric, truths[metric], settings, scratch, database
        )
        if metric is settings.gallery_metric and collection.size == max(settings.sizes):
            _record_gallery(report, collection, found, settings)
    if collection.size in settings.sql_sizes:
        _measure_sql(report, collection, truths[settings.sql_metric], settings, workspace)


def _record_features(
    report: Report,
    collection: Collection,
    features: CollectionFeatures,
    descriptors: QueryDescriptors,
    vector_milliseconds: Sequence[float],
) -> None:
    built = features.build
    operation = dataset_operation(collection.spec.name, FEATURES)
    size = collection.size
    shape = {
        "objetos": built.objects,
        "descriptores": built.descriptors,
        "objetos_sin_descriptores": built.empty_objects,
        "objetos_de_la_muestra": built.training_objects,
        "descriptores_de_la_muestra": built.training_descriptors,
        "reutilizadas": features.reused,
        "carga_media_1_min": load_average(),
    }
    report.add(EXTRACTION_STAGE, operation, size, built.extraction_ms, **shape)
    report.add(SAMPLE_STAGE, operation, size, built.sample_ms)
    report.add(VOCABULARY_STAGE, operation, size, built.vocabulary_ms)
    report.add(QUANTIZATION_STAGE, operation, size, built.quantization_ms)
    report.add(READING_STAGE, operation, size, features.reading_ms)
    report.add(
        FEATURE_STORE,
        dataset_operation(collection.spec.name, MEMORY),
        size,
        0.0,
        disco_kib=built.store_kib,
    )
    per_query = [
        extraction + vector
        for extraction, vector in zip(descriptors.milliseconds, vector_milliseconds, strict=True)
    ]
    query_operation = dataset_operation(collection.spec.name, QUERY)
    report.add(
        QUERY_EXTRACTION_STAGE,
        query_operation,
        size,
        statistics.median(per_query),
        p95_ms=float(np.percentile(per_query, TAIL_PERCENTILE)),
        extraccion_ms_mediana=statistics.median(descriptors.milliseconds),
        vector_ms_mediana=statistics.median(vector_milliseconds),
        consultas=len(per_query),
    )
    report.add(QUERY_VECTOR_STAGE, query_operation, size, statistics.median(vector_milliseconds))


def _measure_metric(
    report: Report,
    collection: Collection,
    metric: VectorMetric,
    truth: GroundTruth,
    settings: Settings,
    scratch: Path,
    database: Postgres | None,
) -> dict[str, list[list[int]]]:
    """Mide cada técnica con una métrica y devuelve lo que encontró cada una con su
    esfuerzo por omisión."""
    name, size = collection.spec.name, collection.size
    query_labels = [item.label for item in collection.queries]
    report.add(
        RANDOM_NEIGHBOURS,
        metric_operation(name, metric, QUERY),
        size,
        0.0,
        precision_categoria=random_class_precision(
            [item.label for item in collection.files], query_labels
        ),
    )
    report.add(
        RAW_VECTORS,
        metric_operation(name, metric, MEMORY),
        size,
        0.0,
        memoria_kib=round(collection.vectors.nbytes / BYTES_PER_KIB, 1),
    )
    found = {}
    for technique in _techniques(collection.size, metric, settings, database):
        found[technique.name] = _measure_technique(
            report, collection, metric, truth, technique, settings, scratch
        )
    return found


def _techniques(
    count: int, metric: VectorMetric, settings: Settings, database: Postgres | None
) -> list[TechniqueSpec]:
    config = settings.engine
    parameters = IndexParameters(
        metric, config.hnsw_m, config.hnsw_ef_construction, config.hnsw_ef_search, settings.threads
    )
    chosen = [*engine_techniques(count, parameters), *faiss_techniques(count, parameters)]
    if settings.usearch:
        chosen.append(usearch_technique(parameters))
    if database is not None:
        chosen += pgvector_techniques(database, count, parameters)
    return chosen


def _measure_technique(
    report: Report,
    collection: Collection,
    metric: VectorMetric,
    truth: GroundTruth,
    technique: TechniqueSpec,
    settings: Settings,
    scratch: Path,
) -> list[list[int]]:
    name, size = collection.spec.name, collection.size
    built, builds = _built(technique, collection.vectors, settings.build_repetitions)
    try:
        report.add(
            technique.name,
            metric_operation(name, metric, BUILD),
            size,
            statistics.median(builds),
            construcciones_ms=builds,
            **_structure(technique),
            carga_media_1_min=load_average(),
        )
        footprint = built.footprint(scratch)
        if technique.traces_python:
            footprint["memoria_python_kib"] = retained_python_kib(
                lambda: technique.build(collection.vectors)
            )
        report.add(technique.name, metric_operation(name, metric, MEMORY), size, 0.0, **footprint)
        return _sweep(report, collection, metric, truth, technique, built, settings)
    finally:
        built.close()


def _structure(technique: TechniqueSpec) -> dict[str, int]:
    """Las listas de un IVF, que salen del tamaño de la colección."""
    if technique.effort is SearchEffort.PROBES and technique.max_effort is not None:
        return {"listas": technique.max_effort}
    return {}


def _built(
    technique: TechniqueSpec, vectors: Vectors, repetitions: int
) -> tuple[BuiltIndex, list[float]]:
    """La última de `repetitions` construcciones desde cero y lo que tardó cada una."""
    builds: list[float] = []
    built: BuiltIndex | None = None
    for _ in range(repetitions):
        if built is not None:
            built.close()
        started = time.perf_counter()
        built = technique.build(vectors)
        builds.append((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
    if built is None:
        raise ValueError("hace falta al menos una construcción")
    return built, builds


def _sweep(
    report: Report,
    collection: Collection,
    metric: VectorMetric,
    truth: GroundTruth,
    technique: TechniqueSpec,
    built: BuiltIndex,
    settings: Settings,
) -> list[list[int]]:
    """Responde las consultas con cada esfuerzo; devuelve lo encontrado con el de omisión."""
    prepared = [built.prepare(query) for query in collection.query_vectors]
    default: list[list[int]] = []
    for effort in efforts_of(technique, settings):

        def search(position: int, effort: int | None = effort) -> list[int]:
            return built.search(prepared[position], settings.k, effort)

        measured = timed_queries(search, len(prepared), settings.repetitions)
        quality = _quality(collection, truth, measured, settings.k)
        is_default = effort == technique.default_effort
        operation = metric_operation(collection.spec.name, metric, CURVE)
        report.add(
            technique.name,
            operation,
            collection.size,
            measured.median,
            p95_ms=measured.tail,
            esfuerzo=effort,
            por_omision=is_default,
            **quality,
        )
        if not is_default:
            continue
        default = measured.found
        extra = _server_side(built, prepared, settings.k, effort)
        report.add(
            technique.name,
            metric_operation(collection.spec.name, metric, QUERY),
            collection.size,
            measured.median,
            p95_ms=measured.tail,
            esfuerzo=effort,
            consultas=len(prepared),
            **quality,
            **extra,
            por_consulta_ms=[round(value, 4) for value in measured.milliseconds],
        )
    return default


def efforts_of(technique: TechniqueSpec, settings: Settings) -> list[int | None]:
    """Los esfuerzos que se barren: los pedidos que la técnica admite, más el de omisión."""
    if technique.effort is None or technique.default_effort is None:
        return [None]
    asked = settings.ivf_probes if technique.effort is SearchEffort.PROBES else settings.ef_search
    ceiling = technique.max_effort
    chosen = {effort for effort in asked if ceiling is None or effort <= ceiling}
    return [*sorted(chosen | {technique.default_effort})]


def timed_queries(search: Callable[[int], list[int]], queries: int, repetitions: int) -> QueryRun:
    """Responde cada consulta `repetitions` veces seguidas de las demás; guarda lo que dio la
    primera pasada y la mediana de los tiempos de cada consulta."""
    found: list[list[int]] = []
    times: list[list[float]] = [[] for _ in range(queries)]
    for repetition in range(repetitions):
        for position in range(queries):
            started = time.perf_counter()
            result = search(position)
            times[position].append((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
            if repetition == 0:
                found.append(result)
    return QueryRun(found, [statistics.median(measured) for measured in times])


def _quality(
    collection: Collection, truth: GroundTruth, measured: QueryRun, k: int
) -> dict[str, float]:
    recalls = [truth.recall(query, found) for query, found in enumerate(measured.found)]
    precisions = _class_precisions(collection, measured.found, k)
    return {
        "recall_en_k": statistics.fmean(recalls),
        "recall_minimo": min(recalls),
        "precision_categoria": statistics.fmean(precisions),
        "resultados_cortos": sum(1 for found in measured.found if len(found) < k),
    }


def _class_precisions(
    collection: Collection, found: Sequence[Sequence[int]], k: int
) -> list[float]:
    return [
        class_precision_at_k(collection.labels_of(neighbours), query.label, k)
        for query, neighbours in zip(collection.queries, found, strict=True)
    ]


def _server_side(
    built: BuiltIndex, prepared: list[Any], k: int, effort: int | None
) -> dict[str, Any]:
    """En pgvector, el tiempo dentro del servidor: separa la búsqueda del viaje al cliente."""
    if not isinstance(built, PgVectorIndex):
        return {}
    server, used = median_server_milliseconds(built, prepared, k, effort)
    return {"servidor_ms_mediana": server, "usa_el_indice": f"{used}/{len(prepared)}"}


def _record_gallery(
    report: Report,
    collection: Collection,
    found: dict[str, list[list[int]]],
    settings: Settings,
) -> None:
    neighbours = found[ENGINE_HNSW]
    precisions = _class_precisions(collection, neighbours, settings.k)
    successes, errors = gallery_cases(
        neighbours,
        precisions,
        [item.label for item in collection.queries],
        settings.gallery_cases,
        settings.gallery_neighbours,
        settings.seed,
    )
    report.add(
        ENGINE_HNSW,
        dataset_operation(collection.spec.name, GALLERY),
        collection.size,
        0.0,
        metrica=METRIC_LABELS[settings.gallery_metric],
        precision_categoria=statistics.fmean(precisions),
        exitos=[_case(collection, case) for case in successes],
        errores=[_case(collection, case) for case in errors],
    )


def _case(collection: Collection, case: GalleryCase) -> dict[str, Any]:
    return {
        "consulta": _described(collection.queries[case.query]),
        "precision_categoria": case.precision,
        "vecinos": [_described(collection.files[position]) for position in case.neighbours],
    }


def _described(item: MediaFile) -> dict[str, str]:
    return {"ruta": item.path, "titulo": item.title, "clase": item.label}


def _measure_sql(
    report: Report,
    collection: Collection,
    truth: GroundTruth,
    settings: Settings,
    workspace: Path,
) -> None:
    directory = workspace / SQL_DIRECTORY / collection.spec.directory.name / str(collection.size)
    config = sql_config(settings.engine, directory, collection.spec.directory)
    table = SqlCollection(collection.files, config)
    try:
        for index, name in ((SQL_HNSW_INDEX, SQL_HNSW), (SQL_IVF_INDEX, SQL_IVF)):
            _measure_sql_index(report, collection, truth, settings, table, index, name)
    finally:
        table.close()
        shutil.rmtree(directory, ignore_errors=True)


def _measure_sql_index(
    report: Report,
    collection: Collection,
    truth: GroundTruth,
    settings: Settings,
    table: SqlCollection,
    index: SqlIndex,
    name: str,
) -> None:
    metric = settings.sql_metric
    report.add(
        name,
        dataset_operation(collection.spec.name, CREATE_INDEX),
        collection.size,
        table.create_index(index, metric),
        metrica=metric.value,
        carga_media_1_min=load_average(),
    )
    paths = [item.path for item in collection.queries]
    measured = timed_queries(
        lambda position: table.similar(index, paths[position], settings.k, None),
        len(paths),
        settings.repetitions,
    )
    report.add(
        name,
        metric_operation(collection.spec.name, metric, QUERY),
        collection.size,
        measured.median,
        p95_ms=measured.tail,
        consultas=len(paths),
        **_quality(collection, truth, measured, settings.k),
        por_consulta_ms=[round(value, 4) for value in measured.milliseconds],
    )
    table.drop_index(index)


def _datasets(arguments: argparse.Namespace) -> list[DatasetSpec]:
    chosen = []
    if arguments.images is not None:
        chosen.append(images_dataset(arguments.images))
    if arguments.audio is not None:
        chosen.append(audio_dataset(arguments.audio))
    return chosen


def _parser() -> argparse.ArgumentParser:
    defaults = EngineConfig()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--images", type=Path, help="carpeta del dataset ABO")
    parser.add_argument("--audio", type=Path, help="carpeta del dataset Speech Commands")
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--queries", type=int, help="consultas; por omisión, todas")
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="el k del top-k y del Recall@k")
    parser.add_argument("--metrics", nargs="+", default=list(DEFAULT_METRICS))
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--build-repetitions", type=int, default=DEFAULT_BUILD_REPETITIONS)
    parser.add_argument(
        "--threads", type=int, default=DEFAULT_THREADS, help="hilos de FAISS, USearch y pgvector"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=defaults.feature_workers,
        help="procesos que extraen las características",
    )
    parser.add_argument("--ivf-probes", type=int, nargs="+", default=list(DEFAULT_IVF_PROBES))
    parser.add_argument("--ef-search", type=int, nargs="+", default=list(DEFAULT_EF_SEARCH))
    parser.add_argument("--hnsw-m", type=int, default=defaults.hnsw_m)
    parser.add_argument("--hnsw-ef-construction", type=int, default=defaults.hnsw_ef_construction)
    parser.add_argument(
        "--hnsw-ef-search",
        type=int,
        default=defaults.hnsw_ef_search,
        help="el ef de la medida principal",
    )
    parser.add_argument("--vocabulary", type=int, default=defaults.vocabulary_size)
    parser.add_argument(
        "--sql-sizes",
        type=int,
        nargs="*",
        help="tamaños en que se mide el gestor por SQL; por omisión, todos",
    )
    parser.add_argument("--sql-metric", default=VectorMetric.COSINE.value)
    parser.add_argument("--gallery-metric", default=VectorMetric.COSINE.value)
    parser.add_argument("--gallery-cases", type=int, default=DEFAULT_GALLERY_CASES)
    parser.add_argument("--gallery-neighbours", type=int, default=DEFAULT_GALLERY_NEIGHBOURS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="desempate de la galería")
    add_postgres_argument(parser)
    parser.add_argument(
        "--postgres-maintenance-work-mem",
        help="maintenance_work_mem de la sesión que construye los índices de pgvector",
    )
    parser.add_argument(
        "--features-dir",
        type=Path,
        help="guarda aquí las características y las reutiliza en la siguiente ejecución",
    )
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    datasets = _datasets(arguments)
    if not datasets:
        raise SystemExit("hace falta --images, --audio o los dos")
    if arguments.postgres_dsn is None:
        print(f"Sin --postgres-dsn ni ${POSTGRES_DSN_VARIABLE}: no se mide pgvector.")
    usearch = importlib.util.find_spec(USEARCH_MODULE) is not None
    if not usearch:
        print("Sin usearch instalado (.[usearch]): no se mide su HNSW.")
    sizes = tuple(arguments.sizes)
    settings = Settings(
        sizes=sizes,
        metrics=tuple(vector_metric_named(name) for name in arguments.metrics),
        k=arguments.k,
        queries=arguments.queries,
        repetitions=arguments.repetitions,
        build_repetitions=arguments.build_repetitions,
        threads=arguments.threads,
        ivf_probes=tuple(arguments.ivf_probes),
        ef_search=tuple(arguments.ef_search),
        sql_sizes=sizes if arguments.sql_sizes is None else tuple(arguments.sql_sizes),
        sql_metric=vector_metric_named(arguments.sql_metric),
        gallery_metric=vector_metric_named(arguments.gallery_metric),
        gallery_cases=arguments.gallery_cases,
        gallery_neighbours=arguments.gallery_neighbours,
        seed=arguments.seed,
        usearch=usearch,
        postgres_dsn=arguments.postgres_dsn,
        postgres_maintenance_work_mem=arguments.postgres_maintenance_work_mem,
        features_directory=arguments.features_dir,
        engine=EngineConfig(
            feature_workers=arguments.workers,
            vocabulary_size=arguments.vocabulary,
            hnsw_m=arguments.hnsw_m,
            hnsw_ef_construction=arguments.hnsw_ef_construction,
            hnsw_ef_search=arguments.hnsw_ef_search,
        ),
    )
    workspace = Path(tempfile.mkdtemp(prefix="minigestor-bench-"))
    try:
        report = run(datasets, settings, workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    path = report.save(arguments.output)
    print(report.render())
    print(f"\nResultados crudos en {path}")


if __name__ == "__main__":
    main()
