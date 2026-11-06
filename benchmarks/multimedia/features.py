"""De una colección de archivos a sus vectores TF-IDF, con lo que cuesta cada etapa.

Recorre el mismo camino que `CREATE INDEX ... USING HNSW | IVF`: los objetos de la muestra
salen de `training_positions`, el vocabulario de `training_sample` y `Vocabulary.train`, y
los conteos y el idf del `FeatureStore`. Así los vectores son los que indexa el gestor, y
todas las técnicas del experimento comparan sobre los mismos.

Las etapas se miden por separado:

- **muestra:** extraer los objetos con que se entrena el vocabulario;
- **vocabulario:** k-means sobre la muestra;
- **extracción:** una pasada que solo extrae todos los objetos, en paralelo;
- **cuantización y TF-IDF:** una segunda pasada que vuelve a extraer y guarda los conteos
  en el almacén. Se descuenta el tiempo que el proceso principal pasa esperando a los
  procesos que extraen, así que queda el trabajo propio de cuantizar y escribir;
- **lectura:** leer del almacén todos los vectores TF-IDF.

Los descriptores no se guardan: con 100 000 fotos serían unos 8 GB. Solo quedan los
conteos, en el almacén, y los vectores, en memoria.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from benchmarks.common import directory_size, synthetic_record_id
from benchmarks.multimedia.collection import MediaFile
from config import EngineConfig
from index.vector import vector_id_of
from index.vector.metrics import Vectors
from multimedia import (
    Descriptors,
    FailedExtraction,
    FeatureStore,
    extract_features,
    extract_in_parallel,
    training_positions,
    training_sample,
)
from query.plan import MILLISECONDS_PER_SECOND

STORE_NAME = "caracteristicas"
MEASURES_FILE = "medidas.json"
# Los parámetros de la configuración que cambian los vectores: si uno no coincide con el de
# un almacén guardado, sus vectores son otros y no se pueden reutilizar.
FEATURE_SETTINGS = (
    "vocabulary_size",
    "kmeans_iterations",
    "kmeans_seed",
    "kmeans_samples_per_word",
    "vocabulary_training_objects",
    "image_max_side",
    "image_max_descriptors",
    "audio_sample_rate",
    "audio_offset_seconds",
    "audio_duration_seconds",
    "mfcc_coefficients",
    "mfcc_fft_length",
    "mfcc_window_length",
    "mfcc_hop_length",
    "mfcc_delta_width",
)


class StaleFeaturesError(ValueError):
    """La carpeta de características guardadas es de otra colección o de otra configuración."""


@dataclass(frozen=True, slots=True)
class FeatureBuild:
    """Milisegundos de cada etapa y la forma de lo que salió."""

    sample_ms: float
    vocabulary_ms: float
    extraction_ms: float
    quantization_ms: float
    objects: int
    descriptors: int
    empty_objects: int
    training_objects: int
    training_descriptors: int
    store_kib: float


@dataclass(frozen=True, slots=True)
class CollectionFeatures:
    """Los vectores TF-IDF de la colección, en su orden, y el almacén abierto, que da el
    vector de una consulta con el mismo vocabulario e idf."""

    vectors: Vectors
    build: FeatureBuild
    reading_ms: float
    reused: bool
    store: FeatureStore


@dataclass(frozen=True, slots=True)
class QueryDescriptors:
    """Los descriptores de cada consulta y lo que tardó extraerlos, uno a uno."""

    descriptors: tuple[Descriptors, ...]
    milliseconds: tuple[float, ...]


def collection_features(
    files: Sequence[MediaFile], config: EngineConfig, workers: int, directory: Path
) -> CollectionFeatures:
    """Los vectores de `files`, construidos en `directory` o reutilizados si ya están.

    Raises:
        StaleFeaturesError: si `directory` guarda los de otra colección u otra configuración.
        MediaError: si un archivo no se puede describir.
    """
    measures = directory / MEASURES_FILE
    fingerprint = _fingerprint(files, config)
    reused = measures.is_file()
    if reused:
        build = _saved_build(measures, fingerprint)
        store = FeatureStore(directory / STORE_NAME, config)
    else:
        directory.mkdir(parents=True, exist_ok=True)
        for leftover in FeatureStore.files_of(directory / STORE_NAME):
            leftover.unlink(missing_ok=True)
        store = FeatureStore(directory / STORE_NAME, config)
        build = _build(store, [Path(item.path) for item in files], config, workers)
        store.flush()
        build = _with_store_size(build, directory)
        payload = {"fingerprint": fingerprint, "build": asdict(build)}
        measures.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    started = time.perf_counter()
    vectors = _vectors_in_order(store, len(files))
    reading_ms = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    return CollectionFeatures(vectors, build, reading_ms, reused, store)


def query_descriptors(files: Sequence[MediaFile], config: EngineConfig) -> QueryDescriptors:
    """Extrae cada consulta en este proceso, como lo hace `SIMILAR_TO`, y cronometra cada una.

    Raises:
        MediaError: si una consulta no se puede describir.
    """
    descriptors: list[Descriptors] = []
    milliseconds: list[float] = []
    for item in files:
        started = time.perf_counter()
        descriptors.append(extract_features(Path(item.path), config))
        milliseconds.append((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
    return QueryDescriptors(tuple(descriptors), tuple(milliseconds))


def query_vectors(
    store: FeatureStore, queries: QueryDescriptors
) -> tuple[Vectors, tuple[float, ...]]:
    """El vector TF-IDF de cada consulta con el vocabulario y el idf del almacén, y lo que
    tardó cada uno."""
    vectors: list[Vectors] = []
    milliseconds: list[float] = []
    for descriptors in queries.descriptors:
        started = time.perf_counter()
        vectors.append(store.vectorize(descriptors))
        milliseconds.append((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
    stacked: Vectors = np.stack(vectors)
    return stacked, tuple(milliseconds)


class _WaitingClock:
    """Pasa los elementos de otro iterador y suma lo que se espera por cada uno."""

    def __init__(self, items: Iterable[Any]) -> None:
        self._items = iter(items)
        self.waited_seconds = 0.0

    def __iter__(self) -> Iterator[Any]:
        while True:
            started = time.perf_counter()
            try:
                item = next(self._items)
            except StopIteration:
                return
            finally:
                self.waited_seconds += time.perf_counter() - started
            yield item


def _build(
    store: FeatureStore, paths: list[Path], config: EngineConfig, workers: int
) -> FeatureBuild:
    chosen = [
        paths[position]
        for position in training_positions(
            len(paths), config.vocabulary_training_objects, config.kmeans_seed
        )
    ]
    started = time.perf_counter()
    training = training_sample(
        _descriptors_of(chosen, config, workers),
        config.vocabulary_size * config.kmeans_samples_per_word,
        config.kmeans_seed,
    )
    sample_ms = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    extraction_ms = _time_extraction(paths, config, workers)
    extracted = _WaitingClock(_descriptors_of(paths, config, workers))
    objects = (
        (synthetic_record_id(position), descriptors)
        for position, descriptors in enumerate(extracted)
    )
    built = store.build(objects, training)
    quantizing = built.quantizing_seconds - extracted.waited_seconds
    return FeatureBuild(
        sample_ms=sample_ms,
        vocabulary_ms=built.training_seconds * MILLISECONDS_PER_SECOND,
        extraction_ms=extraction_ms,
        quantization_ms=quantizing * MILLISECONDS_PER_SECOND,
        objects=built.objects,
        descriptors=built.descriptors,
        empty_objects=built.empty_objects,
        training_objects=len(chosen),
        training_descriptors=built.training_descriptors,
        store_kib=0.0,
    )


def _time_extraction(paths: list[Path], config: EngineConfig, workers: int) -> float:
    started = time.perf_counter()
    for _ in _descriptors_of(paths, config, workers):
        pass
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _descriptors_of(paths: list[Path], config: EngineConfig, workers: int) -> Iterator[Descriptors]:
    for outcome in extract_in_parallel(paths, config, workers):
        if isinstance(outcome, FailedExtraction):
            raise outcome.error
        yield outcome.descriptors


def _vectors_in_order(store: FeatureStore, count: int) -> Vectors:
    """Los vectores del almacén en el orden de la colección: la fila `i` es el archivo `i`."""
    position_of = {
        vector_id_of(synthetic_record_id(position)): position for position in range(count)
    }
    vectors = np.zeros((count, store.vocabulary_size), dtype=np.float32)
    seen = 0
    for ids, batch in store.vectors():
        vectors[[position_of[int(vector_id)] for vector_id in ids]] = batch
        seen += len(ids)
    if seen != count:
        raise StaleFeaturesError(f"el almacén tiene {seen} vectores y la colección {count}")
    return vectors


def _fingerprint(files: Sequence[MediaFile], config: EngineConfig) -> dict[str, Any]:
    settings = {name: getattr(config, name) for name in FEATURE_SETTINGS}
    return {"files": len(files), "first": files[0].path, "last": files[-1].path, **settings}


def _saved_build(measures: Path, fingerprint: dict[str, Any]) -> FeatureBuild:
    saved = json.loads(measures.read_text(encoding="utf-8"))
    if saved["fingerprint"] != fingerprint:
        raise StaleFeaturesError(
            f"{measures.parent} guarda las características de otra colección o con otros "
            "parámetros: bórrala o usa otra carpeta"
        )
    return FeatureBuild(**saved["build"])


def _with_store_size(build: FeatureBuild, directory: Path) -> FeatureBuild:
    files = FeatureStore.files_of(directory / STORE_NAME)
    values = asdict(build) | {"store_kib": round(directory_size(files), 1)}
    return FeatureBuild(**values)
