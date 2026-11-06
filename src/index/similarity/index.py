"""Un índice de similitud sobre la columna que guarda la ruta de una imagen o un audio.

Es lo que crea `CREATE INDEX ... USING HNSW (ruta)` o `USING IVF (ruta)`: la tabla le avisa
de cada fila que entra o sale, como a cualquier índice secundario, y él mantiene dos cosas:

- **el almacén de características** (`multimedia.FeatureStore`): el vocabulario, el idf y
  los conteos de palabras de cada fila, en páginas del gestor. Es la fuente de verdad;
- **el índice de vectores** (`HNSWIndex` o `IVFIndex`, sobre FAISS), que se deriva de él.

Construirlo extrae los descriptores de todas las filas (en paralelo), entrena el
vocabulario con una muestra, cuantiza cada fila y llena el índice de vectores.

El índice de vectores se guarda en un archivo aparte que se reescribe entero, y con 100 000
vectores pesa más de cien megabytes: escribirlo en cada COMMIT haría que un `INSERT` de una
fila costara una escritura así. En su lugar, el `flush` del COMMIT lleva al disco las
páginas del almacén y borra el archivo del índice si quedó viejo; `close` lo vuelve a
escribir. Si el proceso termina sin cerrar, al abrir no hay archivo y se reconstruye desde el
almacén, que sí está al día.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from config import EngineConfig
from errors import DomainError
from index.hnsw import HNSWIndex
from index.ivf import IVFIndex
from index.secondary import ColumnIndex
from index.vector import (
    FlatIndex,
    VectorIndex,
    VectorIndexFileError,
    VectorMetric,
    VectorSearchStats,
    record_id_of,
    vector_id_of,
)
from multimedia import (
    Descriptors,
    FailedExtraction,
    FeatureStore,
    MediaKind,
    descriptor_dimension,
    extract_each,
    extract_features,
    extract_in_parallel,
    media_kind_of,
    training_sample,
)
from storage.record import FieldReader
from storage.record_id import RecordId
from storage.types import Key

from .spill import DescriptorSpill

FEATURES_SUFFIX = ".features"
HNSW_KIND = "hnsw"
IVF_KIND = "ivf"
SECONDS_TO_MILLISECONDS = 1000

MediaResolver = Callable[[str], Path]


class SimilarityIndexError(DomainError):
    """Lo que el índice de similitud no puede hacer con lo que se le pidió."""


class SimilarityEqualityError(SimilarityIndexError):
    """Se pidió una igualdad a un índice de similitud, que solo responde `SIMILAR_TO`."""


class MixedMediaError(SimilarityIndexError):
    """La columna mezcla imágenes y audios, o la consulta no es del tipo de la columna."""


@dataclass(frozen=True, slots=True)
class VectorIndexSettings:
    """Cómo se construye el índice de vectores: el método, la métrica y sus parámetros.

    `vocabulary_size`, si se da, reemplaza al de la configuración para esta columna.
    """

    kind: str
    metric: VectorMetric
    m: int
    ef_construction: int
    ef_search: int
    lists: int | None = None
    probes: int | None = None
    vocabulary_size: int | None = None

    def create(self, dimension: int, config: EngineConfig) -> VectorIndex:
        if self.kind == HNSW_KIND:
            return HNSWIndex(
                dimension,
                self.metric,
                m=self.m,
                ef_construction=self.ef_construction,
                ef_search=self.ef_search,
                rebuild_fraction=config.vector_rebuild_fraction,
                exact_filter_rows=config.vector_exact_filter_rows,
            )
        return IVFIndex(
            dimension,
            self.metric,
            lists=self.lists,
            probes=self.probes,
            rebuild_fraction=config.vector_rebuild_fraction,
            exact_filter_rows=config.vector_exact_filter_rows,
        )

    def load(self, path: Path, config: EngineConfig) -> VectorIndex:
        kind: type[VectorIndex] = HNSWIndex if self.kind == HNSW_KIND else IVFIndex
        return kind.load(path, config.vector_exact_filter_rows)


@dataclass(frozen=True, slots=True)
class SimilarRow:
    record_id: RecordId
    score: float


@dataclass(frozen=True, slots=True)
class SimilarityResult:
    """Lo que devolvió un `SIMILAR_TO` y lo que costó, para el plan.

    Attributes:
        rows: las filas más parecidas, de mayor a menor puntaje.
        stats: el trabajo de la búsqueda en el índice de vectores.
        metric: la métrica con que se puntuó.
        exact_reason: por qué se comparó contra todas las filas en vez de usar el índice;
            `None` si se usó.
        extraction_ms: lo que tardó extraer y cuantizar la consulta.
    """

    rows: tuple[SimilarRow, ...]
    stats: VectorSearchStats
    metric: VectorMetric
    exact_reason: str | None
    extraction_ms: float


class SimilarityIndex(ColumnIndex):
    """Índice HNSW o IVF sobre una columna con rutas de imágenes o audios.

    `resolve` convierte el texto de la columna en la ruta del archivo, y rechaza la que no
    se puede leer: es el mismo control que `CREATE TABLE ... FROM FILE`.

    Raises:
        VectorIndexFileError: si el índice de vectores guardado no es de este método.
    """

    def __init__(
        self,
        path: Path,
        serializer: FieldReader,
        column: str,
        config: EngineConfig,
        settings: VectorIndexSettings,
        resolve: MediaResolver,
    ) -> None:
        super().__init__(serializer, column)
        self._column = column
        self._path = path
        self._settings = settings
        self._config = (
            config
            if settings.vocabulary_size is None
            else replace(config, vocabulary_size=settings.vocabulary_size)
        )
        self._resolve = resolve
        self._store = FeatureStore(features_path(path), self._config)
        self._vectors = self._open_vectors()
        self._saved = path.exists()
        self._exact: dict[VectorMetric, FlatIndex] = {}

    @staticmethod
    def files_of(path: Path) -> list[Path]:
        """El índice de vectores y los archivos de su almacén de características."""
        return [path, *FeatureStore.files_of(features_path(path))]

    @property
    def settings(self) -> VectorIndexSettings:
        return self._settings

    @property
    def column(self) -> str:
        return self._column

    @property
    def media_kind(self) -> MediaKind | None:
        """Lo que guarda la columna, imágenes o audios; `None` si el índice está vacío."""
        if not self._store.built:
            return None
        dimension = self._store.descriptor_dimension
        image = descriptor_dimension(MediaKind.IMAGE, self._config)
        return MediaKind.IMAGE if dimension == image else MediaKind.AUDIO

    def build(self, rows: Iterable[tuple[bytes, RecordId]]) -> None:
        """Extrae, entrena el vocabulario, cuantiza y llena el índice de vectores.

        Raises:
            NotEnoughDescriptorsError: si las filas no dan descriptores para el vocabulario,
                también si la tabla está vacía.
            MixedMediaError: si la columna mezcla imágenes y audios.
            MediaError: si un archivo no se puede leer o no es una imagen ni un audio.
            LoaderError: si una ruta sale de los directorios permitidos.
        """
        files = [
            (record_id, self._resolve(value))
            for record, record_id in rows
            if isinstance(value := self._value_of(record), str)
        ]
        _require_one_kind(files)
        with DescriptorSpill(self._path) as spill:
            training = training_sample(
                (
                    spill.keep(record_id, descriptors)
                    for record_id, descriptors in self._extracted(self._training_files(files))
                ),
                self._config.vocabulary_size * self._config.kmeans_samples_per_word,
                self._config.kmeans_seed,
            )
            self._store.build(self._reusing(spill, files), training)
        self._vectors = self._from_store()
        self._save()

    def similar(
        self,
        query: Path,
        k: int,
        metric: VectorMetric | None = None,
        allowed: Iterable[RecordId] | None = None,
        effort: int | None = None,
    ) -> SimilarityResult:
        """Las `k` filas más parecidas al archivo `query`.

        Con la métrica del índice busca en él; con otra, compara contra todas las filas, ya
        que el índice se construyó para ordenar con la suya.

        Raises:
            MixedMediaError: si la consulta no es del tipo de archivo de la columna.
            MediaError: si el archivo no se puede leer.
        """
        chosen = metric or self._settings.metric
        started = time.perf_counter()
        vector = self._query_vector(query)
        extraction_ms = (time.perf_counter() - started) * SECONDS_TO_MILLISECONDS
        ids = None if allowed is None else [vector_id_of(record_id) for record_id in allowed]
        exact_reason = None
        if chosen is self._settings.metric:
            found = self._require_vectors().search(vector, k, allowed=ids, effort=effort)
        else:
            exact_reason = (
                f"el índice ordena con {self._settings.metric.value} y la consulta pide "
                f"{chosen.value}"
            )
            found = self._exact_index(chosen).search(vector, k, allowed=ids)
        rows = tuple(SimilarRow(record_id_of(hit.vector_id), hit.score) for hit in found.hits)
        return SimilarityResult(rows, found.stats, chosen, exact_reason, extraction_ms)

    def describe(self) -> dict[str, Any]:
        vectors = self._vectors.describe() if self._vectors is not None else {}
        return {
            "kind": self._settings.kind,
            **vectors,
            "metric": self._settings.metric.value,
            "media": None if self.media_kind is None else self.media_kind.value,
            "features": self._store.describe(),
        }

    def flush(self) -> None:
        self._store.flush()
        if self._vectors is not None and self._vectors.needs_maintenance():
            self._vectors.maintain()
            self._saved = False
        if not self._saved:
            self._path.unlink(missing_ok=True)

    def close(self) -> None:
        self._store.close()
        if not self._saved and self._vectors is not None:
            self._save()

    def _add(self, value: Key, record_id: RecordId) -> None:
        path = self._resolve(self._path_text(value))
        vectors = self._require_vectors()
        self._require_kind(media_kind_of(path))
        descriptors = extract_features(path, self._config)
        self._store.add(record_id, descriptors)
        vectors.add([vector_id_of(record_id)], self._store.vectorize(descriptors)[None, :])
        self._changed()

    def _remove(self, value: Key, record_id: RecordId) -> bool:
        if not self._store.remove(record_id):
            return False
        self._require_vectors().remove([vector_id_of(record_id)])
        self._changed()
        return True

    def _find(self, value: Key) -> list[RecordId]:
        raise SimilarityEqualityError(
            f"un índice {self._settings.kind.upper()} no responde igualdades: se consulta con "
            "SIMILAR_TO"
        )

    def _changed(self) -> None:
        self._saved = False
        self._exact.clear()

    def _save(self) -> None:
        self._require_vectors().save(self._path)
        self._saved = True

    def _open_vectors(self) -> VectorIndex | None:
        """El índice de vectores guardado; si falta o no se puede leer, el que sale del
        almacén; `None` mientras el almacén no esté construido."""
        if not self._store.built:
            return None
        if self._path.exists():
            try:
                return self._settings.load(self._path, self._config)
            except VectorIndexFileError:
                self._path.unlink()
        return self._from_store()

    def _from_store(self) -> VectorIndex:
        """Un índice de vectores nuevo con los vectores del almacén."""
        index = self._settings.create(self._store.vocabulary_size, self._config)
        for ids, vectors in self._store.vectors():
            index.add(ids, vectors)
        return index

    def _exact_index(self, metric: VectorMetric) -> FlatIndex:
        if metric not in self._exact:
            self._exact[metric] = FlatIndex(
                self._store.vocabulary_size, metric, self._config.vector_exact_filter_rows
            )
            for ids, vectors in self._store.vectors():
                self._exact[metric].add(ids, vectors)
        return self._exact[metric]

    def _query_vector(self, query: Path) -> np.ndarray:
        self._require_kind(media_kind_of(query))
        return self._store.vectorize(extract_features(query, self._config))

    def _require_kind(self, kind: MediaKind) -> None:
        indexed = self.media_kind
        if indexed is not None and indexed is not kind:
            raise MixedMediaError(
                f"la columna '{self.column}' guarda {_KIND_NAMES[indexed]} y el archivo es "
                f"{_KIND_NAMES[kind][:-1]}"
            )

    def _require_vectors(self) -> VectorIndex:
        if self._vectors is None:
            raise SimilarityIndexError(
                f"el índice {self._path.name} no está construido: se crea sobre una tabla con "
                "filas, porque el vocabulario se entrena con ellas"
            )
        return self._vectors

    def _path_text(self, value: Key) -> str:
        if not isinstance(value, str):
            raise SimilarityIndexError(
                f"la columna '{self.column}' guarda la ruta de un archivo y recibió "
                f"{type(value).__name__}"
            )
        return value

    def _training_files(self, files: list[tuple[RecordId, Path]]) -> list[tuple[RecordId, Path]]:
        """Los archivos cuyos descriptores entrenan el vocabulario: una muestra al azar,
        fija por la semilla, de `vocabulary_training_objects` filas."""
        count = min(len(files), self._config.vocabulary_training_objects)
        chosen = np.random.default_rng(self._config.kmeans_seed).choice(
            len(files), size=count, replace=False
        )
        return [files[position] for position in sorted(chosen.tolist())]

    def _reusing(
        self, spill: DescriptorSpill, files: list[tuple[RecordId, Path]]
    ) -> Iterator[tuple[RecordId, Descriptors]]:
        """Los descriptores de todas las filas en el orden de la tabla: los de la muestra,
        del archivo temporal; los demás, extraídos. El orden es el mismo que sin la muestra
        guardada, así que el almacén y el índice salen idénticos."""
        pending = self._extracted([item for item in files if item[0] not in spill])
        for record_id, _ in files:
            if record_id in spill:
                yield record_id, spill.read(record_id)
            else:
                yield next(pending)

    def _extracted(
        self, files: list[tuple[RecordId, Path]]
    ) -> Iterator[tuple[RecordId, Descriptors]]:
        """Los descriptores de cada archivo, en orden; en paralelo si hay al menos un lote
        por proceso.

        Raises:
            MediaError: el del primer archivo que no se pudo describir.
        """
        paths = [path for _, path in files]
        config = self._config
        parallel = len(paths) > config.feature_chunk_files * config.feature_workers
        outcomes = (
            extract_in_parallel(paths, config, config.feature_workers)
            if parallel
            else extract_each(paths, config)
        )
        for (record_id, _), outcome in zip(files, outcomes, strict=True):
            if isinstance(outcome, FailedExtraction):
                raise outcome.error
            yield record_id, outcome.descriptors


_KIND_NAMES = {MediaKind.IMAGE: "imágenes", MediaKind.AUDIO: "audios"}


def features_path(path: Path) -> Path:
    """La base de los archivos del almacén de características de un índice."""
    return path.with_name(path.name + FEATURES_SUFFIX)


def _require_one_kind(files: list[tuple[RecordId, Path]]) -> None:
    kinds = {media_kind_of(path) for _, path in files}
    if len(kinds) > 1:
        raise MixedMediaError(
            "una columna con índice de similitud guarda solo imágenes o solo audios, y esta "
            "tiene de los dos"
        )
