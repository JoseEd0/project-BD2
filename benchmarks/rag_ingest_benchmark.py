"""La ingesta del RAG (issue #85): embeddings con e5 en ONNX y carga en el gestor por HTTP.

    uv run --no-sync python -m benchmarks.rag_ingest_benchmark --data-dir data/datasets \\
        --models data/models/rag --engine-dir data/engine-rag --threads 1 2 4 8 12

Cuatro medidas, en este orden:

1. **Embeddings, fp32 contra int8.** Una muestra de `--sample` fragmentos del corpus,
   repartida a lo largo de `fragmentos.jsonl`, pasa por las dos variantes con cada número
   de hilos de `--threads`; cuenta la mediana de `--repetitions`. Después, cuánto se parecen:
   el coseno entre el vector int8 y el fp32 de cada fragmento, y cuántos de los 10 vecinos
   de cada pregunta de `benchmarks/rag/questions.py` dentro de la muestra coinciden.
2. **La siembra de verdad**, `rag.seed.seed`, sobre el corpus entero, contra un uvicorn en
   otro proceso con sus datos en `--engine-dir` (que queda sembrado), con claves `write` y
   `admin` como en producción, la variante `--seed-variant` y `--seed-threads` hilos. Los
   embeddings van a `--cache-dir`; para medirlos, esa carpeta tiene que empezar vacía. Se
   corre dos veces: la segunda no debe hacer nada. Después, lo que ocupa cada tabla e
   índice en el disco del gestor.
3. **Las preguntas a mano**, en los tres modos, contra ese gestor: los primeros fragmentos
   de cada una y la mediana de `--search-repetitions` búsquedas.
4. **Las etapas de la carga, por separado**, en otro gestor desechable: con los primeros N
   fragmentos (`--sizes` y el corpus entero), la carga NDJSON sin índices, cada `CREATE
   INDEX` después, y la carga con los índices creados antes (diferidos), que es la de la
   siembra. Los vectores salen de la caché del paso 2.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import secrets
import statistics
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime
import tokenizers

from api.auth import Role, digest_of
from api.settings import SERVICE_KEYS_VARIABLE, STATEMENT_TIMEOUT_VARIABLE
from benchmarks.common import (
    BYTES_PER_KIB,
    Report,
    load_average,
    machine_description,
    results_directory,
)
from benchmarks.engine_process import engine_server
from benchmarks.rag.names import (
    DEFERRED_LOAD,
    EMBEDDING_OPERATION,
    HNSW_BUILD,
    INVERTED_BUILD,
    LOAD_ONLY,
    REPORT,
    STAGES_OPERATION,
    THROUGHPUT_FIELD,
)
from benchmarks.rag.questions import QUESTIONS
from minigestor_client import AsyncClient, Client, Timeouts
from query.plan import MILLISECONDS_PER_SECOND
from rag.config import RagSettings, load_embedder
from rag.embedding_cache import EmbeddingCache
from rag.embeddings import DEFAULT_BATCH_SIZE, E5Embedder, EmbedderSettings, Embeddings
from rag.files import read_jsonl
from rag.ingest import chunk_rows, count_lines
from rag.models import DEFAULT_MODEL_VARIANT, ModelFiles, ModelVariant, locate_model
from rag.pipeline import CorpusPaths
from rag.schema import CHUNKS_TABLE, PAPERS_TABLE, SCHEMA
from rag.search import Retriever, SearchMode
from rag.seed import CACHE_DIRECTORY, seed
from rag.settings import CORPUS_DIRECTORY

DEFAULT_THREADS = (1, 2, 4, 8)
DEFAULT_SAMPLE = 2_000
DEFAULT_REPETITIONS = 3
DEFAULT_SIZES = (1_000, 10_000)
DEFAULT_SEARCH_REPETITIONS = 5
NEIGHBOURS = 10
TOP_HITS = 3
SERVICE_NAME = "rag"
MIGRATION_SERVICE_NAME = "rag-migraciones"
KEY_BYTES = 24
# La carga del corpus entero termina con la construcción de sus dos índices: el plazo de una
# sentencia y el de la respuesta de la carga se suben para que ninguno la corte.
STATEMENT_TIMEOUT_SECONDS = 3_600
LOAD_TIMEOUT_SECONDS = 7_200.0
SCRATCH_PREFIX = "minigestor-rag-"
DATA_FILES = ("heap", "text")
TABLE_DATA = "tabla (heap y textos)"
PERCENTILE_LOW = 5


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    models_dir: Path
    engine_dir: Path
    cache_dir: Path
    threads: tuple[int, ...]
    sample: int
    repetitions: int
    sizes: tuple[int, ...]
    seed_variant: ModelVariant
    seed_threads: int
    batch_size: int
    search_repetitions: int

    @property
    def corpus(self) -> CorpusPaths:
        return CorpusPaths(self.data_dir / CORPUS_DIRECTORY)


@dataclass(frozen=True, slots=True)
class EngineKeys:
    write: str
    admin: str

    @classmethod
    def generated(cls) -> EngineKeys:
        return cls(secrets.token_hex(KEY_BYTES), secrets.token_hex(KEY_BYTES))

    def environment(self) -> dict[str, str]:
        entries = (
            f"{SERVICE_NAME}:{Role.WRITE.value}:{digest_of(self.write)}",
            f"{MIGRATION_SERVICE_NAME}:{Role.ADMIN.value}:{digest_of(self.admin)}",
        )
        return {
            SERVICE_KEYS_VARIABLE: ",".join(entries),
            STATEMENT_TIMEOUT_VARIABLE: str(STATEMENT_TIMEOUT_SECONDS),
        }


def run(settings: Settings) -> Report:
    report = Report(REPORT, _parameters(settings))
    texts = _sample_texts(settings.corpus.chunks_file, settings.sample)
    report.parameters["acuerdo_int8_fp32"] = _measure_embeddings(report, settings, texts)
    keys = EngineKeys.generated()
    with engine_server(settings.engine_dir, keys.environment()) as base_url:
        rag_settings = _rag_settings(settings, base_url, keys)
        report.parameters["siembra"] = _seeded(rag_settings, settings)
        report.parameters["segunda_siembra"] = _seeded(rag_settings, settings)
        report.parameters["disco_kib"] = _disk_usage(settings.engine_dir)
        report.parameters["preguntas"] = asyncio.run(_ask(rag_settings, settings))
    report.parameters["etapas"] = _measure_stages(report, settings, keys)
    report.parameters["carga_media_al_terminar"] = load_average()
    return report


def _measure_embeddings(report: Report, settings: Settings, texts: list[str]) -> dict[str, Any]:
    vectors: dict[ModelVariant, Embeddings] = {}
    for variant in ModelVariant:
        files = locate_model(settings.models_dir, variant)
        for threads in settings.threads:
            embedder = E5Embedder(files, EmbedderSettings(threads, settings.batch_size))
            embedder.embed_passages(texts[: settings.batch_size])
            seconds, vectors[variant] = _timed_embeddings(embedder, texts, settings.repetitions)
            report.add(
                variant.value,
                EMBEDDING_OPERATION,
                threads,
                seconds * MILLISECONDS_PER_SECOND,
                **{THROUGHPUT_FIELD: round(len(texts) / seconds, 2)},
                fragmentos=len(texts),
            )
            print(f"{variant} con {threads} hilos: {len(texts) / seconds:.1f} fragmentos/s")
    return _agreement(settings, vectors)


def _timed_embeddings(
    embedder: E5Embedder, texts: list[str], repetitions: int
) -> tuple[float, Embeddings]:
    durations = []
    vectors = np.empty(0, dtype=np.float32)
    for _ in range(repetitions):
        started = time.perf_counter()
        vectors = embedder.embed_passages(texts).vectors
        durations.append(time.perf_counter() - started)
    return statistics.median(durations), vectors


def _agreement(settings: Settings, vectors: dict[ModelVariant, Embeddings]) -> dict[str, Any]:
    cosines = (vectors[ModelVariant.FP32] * vectors[ModelVariant.INT8]).sum(axis=1)
    threads = EmbedderSettings(max(settings.threads))
    embedders = [
        E5Embedder(locate_model(settings.models_dir, variant), threads) for variant in ModelVariant
    ]
    overlaps = []
    for _, question in QUESTIONS:
        neighbours = [
            set(np.argsort(-(vectors[variant] @ embedder.embed_query(question)))[:NEIGHBOURS])
            for variant, embedder in zip(ModelVariant, embedders, strict=True)
        ]
        overlaps.append(len(neighbours[0] & neighbours[1]) / NEIGHBOURS)
    return {
        "coseno_medio": round(float(cosines.mean()), 4),
        "coseno_minimo": round(float(cosines.min()), 4),
        f"coseno_p{PERCENTILE_LOW}": round(float(np.percentile(cosines, PERCENTILE_LOW)), 4),
        f"vecinos_comunes_de_{NEIGHBOURS}": round(statistics.mean(overlaps), 3),
        "vecinos_comunes_por_pregunta": overlaps,
    }


def _seeded(rag_settings: RagSettings, settings: Settings) -> dict[str, Any]:
    started = time.perf_counter()
    result = asyncio.run(
        seed(rag_settings, settings.corpus, settings.cache_dir, lambda: load_embedder(rag_settings))
    )
    print(f"siembra en {time.perf_counter() - started:.1f} s")
    return result.to_json()


async def _ask(rag_settings: RagSettings, settings: Settings) -> list[dict[str, Any]]:
    embedder = load_embedder(rag_settings)
    answers = []
    key = rag_settings.engine_key.get_secret_value()
    async with AsyncClient(rag_settings.engine_base_url, key) as engine:
        retriever = Retriever(engine, embedder)
        for language, question in QUESTIONS:
            for mode in SearchMode:
                durations: list[float] = []
                hits = []
                for _ in range(settings.search_repetitions):
                    started = time.perf_counter()
                    hits = await retriever.search(question, mode, TOP_HITS)
                    durations.append(time.perf_counter() - started)
                answers.append(
                    {
                        "idioma": language,
                        "pregunta": question,
                        "modo": mode.value,
                        "mediana_ms": round(_milliseconds(statistics.median(durations)), 1),
                        "fragmentos": [
                            {
                                "chunk_id": hit.chunk_id,
                                "titulo": hit.paper.title,
                                "pagina": hit.page_start,
                                "puntaje": round(hit.score, 4),
                            }
                            for hit in hits
                        ],
                    }
                )
    return answers


def _measure_stages(report: Report, settings: Settings, keys: EngineKeys) -> list[dict[str, Any]]:
    total = count_lines(settings.corpus.chunks_file)
    model_id = ModelFiles.inside(settings.models_dir, settings.seed_variant).model_id
    cache = EmbeddingCache(settings.cache_dir, model_id)
    stages = []
    timeouts = Timeouts(load_seconds=LOAD_TIMEOUT_SECONDS)
    with (
        tempfile.TemporaryDirectory(prefix=SCRATCH_PREFIX) as scratch,
        engine_server(Path(scratch), keys.environment()) as base_url,
        Client(base_url, keys.admin, timeouts=timeouts) as engine,
    ):
        for size in sorted({*settings.sizes, total}):
            stages.append(_stages_of(report, engine, settings, cache, size))
    return stages


def _stages_of(
    report: Report, engine: Client, settings: Settings, cache: EmbeddingCache, size: int
) -> dict[str, Any]:
    print(f"etapas con {size} fragmentos")
    separate = f"{CHUNKS_TABLE}_{size}_separado"
    table, inverted, hnsw = _chunk_schema(separate)
    engine.execute(table)
    loaded = _timed_load(engine, separate, settings, cache, size)
    inverted_ms = _timed(lambda: engine.execute(inverted))
    hnsw_ms = _timed(lambda: engine.execute(hnsw))
    deferred_table = f"{CHUNKS_TABLE}_{size}_diferido"
    for statement in _chunk_schema(deferred_table):
        engine.execute(statement)
    deferred = _timed_load(engine, deferred_table, settings, cache, size)
    for technique, milliseconds in (
        (LOAD_ONLY, loaded),
        (INVERTED_BUILD, inverted_ms),
        (HNSW_BUILD, hnsw_ms),
        (DEFERRED_LOAD, deferred),
    ):
        report.add(technique, STAGES_OPERATION, size, milliseconds)
    for name in (separate, deferred_table):
        engine.execute(f"DROP TABLE {name}")
    return {
        "fragmentos": size,
        "carga_sin_indices_s": round(loaded / MILLISECONDS_PER_SECOND, 2),
        "indice_invertido_s": round(inverted_ms / MILLISECONDS_PER_SECOND, 2),
        "hnsw_s": round(hnsw_ms / MILLISECONDS_PER_SECOND, 2),
        "carga_con_indices_diferidos_s": round(deferred / MILLISECONDS_PER_SECOND, 2),
    }


def _chunk_schema(table: str) -> tuple[str, str, str]:
    """El `CREATE TABLE` y los dos `CREATE INDEX` de `rag_chunks` con otro nombre de tabla:
    los nombres de los índices empiezan por el de la tabla, así que cambian con él."""
    _, chunks, inverted, hnsw = (statement.replace(CHUNKS_TABLE, table) for statement in SCHEMA)
    return chunks, inverted, hnsw


def _timed_load(
    engine: Client, table: str, settings: Settings, cache: EmbeddingCache, size: int
) -> float:
    rows = itertools.islice(chunk_rows(settings.corpus.chunks_file, cache), size)
    return _timed(lambda: engine.bulk_insert(table, rows))


def _timed(action: Callable[[], object]) -> float:
    started = time.perf_counter()
    action()
    return _milliseconds(time.perf_counter() - started)


def _milliseconds(seconds: float) -> float:
    return seconds * MILLISECONDS_PER_SECOND


def _disk_usage(engine_dir: Path) -> dict[str, dict[str, float]]:
    """KiB de cada tabla del RAG en el disco del gestor, separados en sus datos y cada índice."""
    usage: dict[str, dict[str, float]] = {}
    for path in sorted(engine_dir.iterdir()):
        table, _, rest = path.name.partition(".")
        if table not in (PAPERS_TABLE, CHUNKS_TABLE) or not path.is_file():
            continue
        part = rest.split(".")[0]
        group = TABLE_DATA if part in DATA_FILES else part
        sizes = usage.setdefault(table, {})
        sizes[group] = round(sizes.get(group, 0.0) + path.stat().st_size / BYTES_PER_KIB, 1)
    return usage


def _sample_texts(chunks_file: Path, sample: int) -> list[str]:
    """`sample` fragmentos repartidos a lo largo del archivo: de todos los papers, no de los
    primeros."""
    step = max(1, count_lines(chunks_file) // sample)
    every = itertools.islice(read_jsonl(chunks_file), 0, None, step)
    return [str(chunk["text"]) for chunk in itertools.islice(every, sample)]


def _rag_settings(settings: Settings, base_url: str, keys: EngineKeys) -> RagSettings:
    return RagSettings.model_validate(
        {
            "engine_url": base_url,
            "engine_key": keys.write,
            "migration_key": keys.admin,
            "models_dir": settings.models_dir,
            "embedding_model": settings.seed_variant,
            "embedding_threads": settings.seed_threads,
            "embedding_batch_size": settings.batch_size,
            "engine_load_timeout_seconds": LOAD_TIMEOUT_SECONDS,
        }
    )


def _parameters(settings: Settings) -> dict[str, Any]:
    return {
        "fragmentos_del_corpus": count_lines(settings.corpus.chunks_file),
        "muestra_de_embeddings": settings.sample,
        "hilos": list(settings.threads),
        "repeticiones": settings.repetitions,
        "lote": settings.batch_size,
        "variante_de_la_siembra": settings.seed_variant.value,
        "hilos_de_la_siembra": settings.seed_threads,
        "tamanos_de_las_etapas": list(settings.sizes),
        "busquedas_por_pregunta": settings.search_repetitions,
        "servidor": "uvicorn en otro proceso; claves write y admin; cliente minigestor_client",
        "versiones": {
            "onnxruntime": onnxruntime.__version__,
            "tokenizers": tokenizers.__version__,
            "numpy": np.__version__,
        },
        "maquina": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--engine-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--threads", type=int, nargs="+", default=list(DEFAULT_THREADS))
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument(
        "--seed-variant",
        type=ModelVariant,
        choices=list(ModelVariant),
        default=DEFAULT_MODEL_VARIANT,
    )
    parser.add_argument("--seed-threads", type=int, default=EmbedderSettings().threads)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--search-repetitions", type=int, default=DEFAULT_SEARCH_REPETITIONS)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    corpus_root = arguments.data_dir / CORPUS_DIRECTORY
    settings = Settings(
        data_dir=arguments.data_dir,
        models_dir=arguments.models,
        engine_dir=arguments.engine_dir,
        cache_dir=arguments.cache_dir or corpus_root / CACHE_DIRECTORY,
        threads=tuple(arguments.threads),
        sample=arguments.sample,
        repetitions=arguments.repetitions,
        sizes=tuple(arguments.sizes),
        seed_variant=arguments.seed_variant,
        seed_threads=arguments.seed_threads,
        batch_size=arguments.batch_size,
        search_repetitions=arguments.search_repetitions,
    )
    started = time.perf_counter()
    report = run(settings)
    report.parameters["duracion_s"] = round(time.perf_counter() - started, 1)
    print(report.render())
    print(f"resultados en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
