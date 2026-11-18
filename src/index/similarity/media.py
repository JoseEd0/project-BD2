"""Un índice de similitud sobre la columna que guarda la ruta de una imagen o un audio.

Es lo que crea `CREATE INDEX ... USING HNSW (ruta)` o `USING IVF (ruta)` sobre una columna
de texto: la tabla le avisa de cada fila que entra o sale, como a cualquier índice
secundario, y él mantiene dos cosas:

- **el almacén de características** (`multimedia.FeatureStore`): el vocabulario, el idf y
  los conteos de palabras de cada fila, en páginas del gestor. Es la fuente de verdad;
- **el índice de vectores** (`IndexedVectors`), que se deriva de él.

Construirlo extrae los descriptores de todas las filas (en paralelo), entrena el
vocabulario con una muestra, cuantiza cada fila y llena el índice de vectores. Si al abrir
el índice de vectores falta o está dañado, se reconstruye desde el almacén.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from config import EngineConfig
from index.vector import vector_id_of
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
    training_positions,
    training_sample,
)
from storage.record import FieldReader
from storage.record_id import RecordId
from storage.types import Key

from .base import QueryVector, SimilarityIndex, SimilarityQuery
from .errors import MixedMediaError, SimilarityIndexError
from .spill import DescriptorSpill
from .vectors import IndexedVectors, VectorBatches, VectorIndexSettings

FEATURES_SUFFIX = ".features"
SECONDS_TO_MILLISECONDS = 1000

MediaResolver = Callable[[str], Path]


class MediaSimilarityIndex(SimilarityIndex):
    """Índice HNSW o IVF sobre una columna con rutas de imágenes o audios.

    `resolve` convierte el texto de la columna en la ruta del archivo, y rechaza la que no
    se puede leer: es el mismo control que `CREATE TABLE ... FROM FILE`.
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
        super().__init__(path, serializer, column, config, settings)
        self._config = (
            config
            if settings.vocabulary_size is None
            else replace(config, vocabulary_size=settings.vocabulary_size)
        )
        self._resolve = resolve
        self._store = FeatureStore(features_path(path), self._config)
        if self._store.built and not self._vectors.load():
            self._vectors.rebuild(self._store.vocabulary_size, self._store.vectors())

    @staticmethod
    def files_of(path: Path) -> list[Path]:
        """El índice de vectores, la lista de vectores cero y los archivos de su almacén de
        características."""
        return [*IndexedVectors.files_of(path), *FeatureStore.files_of(features_path(path))]

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
        self._vectors.rebuild(self._store.vocabulary_size, self._store.vectors())
        self._vectors.save()

    def describe(self) -> dict[str, Any]:
        return {
            **super().describe(),
            "media": None if self.media_kind is None else self.media_kind.value,
            "features": self._store.describe(),
        }

    def flush(self) -> None:
        self._store.flush()
        super().flush()

    def close(self) -> None:
        self._store.close()
        super().close()

    def _add(self, value: Key, record_id: RecordId) -> None:
        path = self._resolve(self._path_text(value))
        self._require_built()
        self._require_kind(media_kind_of(path))
        descriptors = extract_features(path, self._config)
        self._store.add(record_id, descriptors)
        self._vectors.add(vector_id_of(record_id), self._store.vectorize(descriptors))

    def _remove(self, value: Key, record_id: RecordId) -> bool:
        if not self._store.remove(record_id):
            return False
        self._vectors.remove(vector_id_of(record_id))
        return True

    def _query_vector(self, query: SimilarityQuery) -> QueryVector:
        if not isinstance(query, Path):
            raise SimilarityIndexError(
                f"la columna '{self.column}' guarda la ruta de un archivo: SIMILAR_TO se "
                "consulta con otro archivo, como 'foto.jpg', no con un vector"
            )
        self._require_built()
        self._require_kind(media_kind_of(query))
        started = time.perf_counter()
        vector = self._store.vectorize(extract_features(query, self._config))
        extraction_ms = (time.perf_counter() - started) * SECONDS_TO_MILLISECONDS
        return QueryVector(vector, extraction_ms)

    def _all_vectors(self) -> VectorBatches:
        return self._store.vectors()

    def _require_kind(self, kind: MediaKind) -> None:
        indexed = self.media_kind
        if indexed is not None and indexed is not kind:
            raise MixedMediaError(
                f"la columna '{self.column}' guarda {_KIND_NAMES[indexed]} y el archivo es "
                f"{_KIND_NAMES[kind][:-1]}"
            )

    def _require_built(self) -> None:
        if not self._vectors.built:
            raise SimilarityIndexError(
                f"el índice {self._path.name} no está construido: se crea sobre una tabla con "
                "filas, porque el vocabulario se entrena con ellas"
            )

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
        positions = training_positions(
            len(files), self._config.vocabulary_training_objects, self._config.kmeans_seed
        )
        return [files[position] for position in positions]

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
