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

Una ruta de audio puede llevar un tramo, `pista.mp3#t=10,20` (ver
`multimedia/fragments.py`): la fila es ese tramo. Al construir, las filas con tramos de un
mismo archivo se extraen juntas, de una sola decodificación del archivo.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from config import EngineConfig
from index.vector import VectorMetric, VectorSearchStats, record_id_of, vector_id_of
from multimedia import (
    Descriptors,
    FailedExtraction,
    FeatureStore,
    MediaKind,
    MediaReference,
    TemporalFragment,
    check_media,
    decode_segments,
    describe_segments,
    descriptor_dimension,
    extract_each,
    extract_features,
    extract_in_parallel,
    media_kind_of,
    media_reference,
    training_positions,
    training_sample,
)
from storage.record import FieldReader
from storage.record_id import RecordId
from storage.types import Key

from .base import QueryVector, SimilarityIndex, SimilarityQuery, SimilarRow
from .errors import MixedMediaError, SimilarityIndexError
from .spill import DescriptorSpill
from .vectors import IndexedVectors, VectorBatches, VectorIndexSettings

FEATURES_SUFFIX = ".features"
SECONDS_TO_MILLISECONDS = 1000

MediaResolver = Callable[[str], Path]
IndexedFile = tuple[RecordId, MediaReference]


@dataclass(frozen=True, slots=True)
class SegmentMatches:
    """Una ventana del audio consultado y las filas más parecidas a ella."""

    window: TemporalFragment
    rows: tuple[SimilarRow, ...]


@dataclass(frozen=True, slots=True)
class SegmentSearchResult:
    """Lo que devolvió una consulta por segmentos y lo que costó cada etapa.

    Attributes:
        duration: segundos del audio consultado.
        segments: cada ventana, en orden, con sus vecinos de mayor a menor puntaje.
        metric: la métrica del índice, con la que se puntuó.
        stats: el trabajo de la búsqueda de todas las ventanas juntas.
        decode_ms: lo que tardó decodificar el audio, una vez.
        extraction_ms: lo que tardó describir y cuantizar todas las ventanas.
        search_ms: lo que tardó la búsqueda de todas las ventanas en el índice.
    """

    duration: float
    segments: tuple[SegmentMatches, ...]
    metric: VectorMetric
    stats: VectorSearchStats
    decode_ms: float
    extraction_ms: float
    search_ms: float


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

        Las filas con tramos de un mismo archivo se guardan juntas, en el orden en que
        aparece el archivo por primera vez, y salen de una sola decodificación; las demás
        siguen el orden de la tabla.

        Raises:
            NotEnoughDescriptorsError: si las filas no dan descriptores para el vocabulario,
                también si la tabla está vacía.
            MixedMediaError: si la columna mezcla imágenes y audios.
            MediaError: si un archivo no se puede leer, no es una imagen ni un audio o su
                tramo no se puede describir.
            LoaderError: si una ruta sale de los directorios permitidos.
        """
        groups = _grouped_by_file(
            [
                (record_id, media_reference(value, self._resolve))
                for record, record_id in rows
                if isinstance(value := self._value_of(record), str)
            ]
        )
        files = [item for group in groups for item in group]
        _require_one_kind(files)
        with DescriptorSpill(self._path) as spill:
            training = training_sample(
                (
                    spill.keep(record_id, descriptors)
                    for record_id, descriptors in self._extracted(self._training_files(groups))
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

    def similar_segments(
        self, path: Path, window: float, hop: float, k: int
    ) -> SegmentSearchResult:
        """Las `k` filas más parecidas a cada ventana de `window` segundos, cada `hop`, del
        audio de `path`, con la métrica del índice.

        El audio se decodifica una vez y todas las ventanas se buscan en un solo lote: cada
        una recibe las mismas filas y puntajes que `similar` con su tramo
        (`ruta#t=inicio,fin`).

        Raises:
            SegmentLimitError: si el audio o las ventanas pasan de un límite de la
                configuración.
            SimilarityIndexError: si el índice no está construido.
            MixedMediaError: si la columna guarda imágenes.
            MediaError: si el archivo no es un audio o no se puede leer.
        """
        self._require_built()
        self._require_kind(media_kind_of(path))
        started = time.perf_counter()
        segmented = decode_segments(path, window, hop, self._config)
        decoded = time.perf_counter()
        queries = np.stack(
            [self._store.vectorize(found) for found in describe_segments(segmented, self._config)]
        )
        described = time.perf_counter()
        found = self._vectors.search_many(queries, k)
        searched = time.perf_counter()
        segments = tuple(
            SegmentMatches(
                window,
                tuple(SimilarRow(record_id_of(hit.vector_id), hit.score) for hit in hits),
            )
            for window, hits in zip(segmented.windows, found.hits, strict=True)
        )
        return SegmentSearchResult(
            duration=segmented.audio.duration,
            segments=segments,
            metric=self.settings.metric,
            stats=found.stats,
            decode_ms=_milliseconds(started, decoded),
            extraction_ms=_milliseconds(decoded, described),
            search_ms=_milliseconds(described, searched),
        )

    def _check(self, value: Key) -> None:
        reference = self._reference_to(value)
        check_media(reference.path, self._config, reference.fragment)

    def _add(self, value: Key, record_id: RecordId) -> None:
        reference = self._reference_to(value)
        descriptors = extract_features(reference.path, self._config, reference.fragment)
        self._store.add(record_id, descriptors)
        try:
            self._vectors.add(vector_id_of(record_id), self._store.vectorize(descriptors))
        except Exception:
            self._store.remove(record_id)
            raise

    def _reference_to(self, value: Key) -> MediaReference:
        """El archivo y el tramo que nombra el valor de la columna, si este índice los
        admite."""
        reference = media_reference(self._path_text(value), self._resolve)
        self._require_built()
        self._require_kind(media_kind_of(reference.path))
        return reference

    def _remove(self, value: Key, record_id: RecordId) -> bool:
        if not self._store.remove(record_id):
            return False
        self._vectors.remove(vector_id_of(record_id))
        return True

    def _query_vector(self, query: SimilarityQuery) -> QueryVector:
        if not isinstance(query, MediaReference):
            raise SimilarityIndexError(
                f"la columna '{self.column}' guarda la ruta de un archivo: SIMILAR_TO se "
                "consulta con otro archivo, como 'foto.jpg', no con un vector"
            )
        self._require_built()
        self._require_kind(media_kind_of(query.path))
        started = time.perf_counter()
        descriptors = extract_features(query.path, self._config, query.fragment)
        vector = self._store.vectorize(descriptors)
        return QueryVector(vector, _milliseconds(started, time.perf_counter()))

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

    def _training_files(self, groups: list[list[IndexedFile]]) -> list[IndexedFile]:
        """Las filas cuyos descriptores entrenan el vocabulario: una muestra al azar, fija
        por la semilla, de unas `vocabulary_training_objects` filas.

        Se eligen archivos enteros, con todos sus tramos, para que ninguno se decodifique
        en la muestra y otra vez después; sin tramos, cada archivo es una fila y la muestra
        es la de siempre.
        """
        rows = sum(len(group) for group in groups)
        objects = self._config.vocabulary_training_objects
        wanted = max(1, round(objects * len(groups) / rows)) if rows else 0
        positions = training_positions(len(groups), wanted, self._config.kmeans_seed)
        return [item for position in positions for item in groups[position]]

    def _reusing(
        self, spill: DescriptorSpill, files: list[IndexedFile]
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

    def _extracted(self, files: list[IndexedFile]) -> Iterator[tuple[RecordId, Descriptors]]:
        """Los descriptores de cada archivo o tramo, en orden; en paralelo si hay al menos
        un lote por proceso.

        Raises:
            MediaError: el del primer archivo que no se pudo describir.
        """
        references = [reference for _, reference in files]
        config = self._config
        parallel = len(references) > config.feature_chunk_files * config.feature_workers
        outcomes = (
            extract_in_parallel(references, config, config.feature_workers)
            if parallel
            else extract_each(references, config)
        )
        for (record_id, _), outcome in zip(files, outcomes, strict=True):
            if isinstance(outcome, FailedExtraction):
                raise outcome.error
            yield record_id, outcome.descriptors


_KIND_NAMES = {MediaKind.IMAGE: "imágenes", MediaKind.AUDIO: "audios"}


def features_path(path: Path) -> Path:
    """La base de los archivos del almacén de características de un índice."""
    return path.with_name(path.name + FEATURES_SUFFIX)


def _grouped_by_file(files: list[IndexedFile]) -> list[list[IndexedFile]]:
    """Las filas en grupos que se extraen juntos: todas las que piden un tramo del mismo
    archivo, en el orden en que aparece ese archivo; cada fila sin tramo, sola."""
    groups: dict[Path | int, list[IndexedFile]] = {}
    for position, (record_id, reference) in enumerate(files):
        key = position if reference.fragment is None else reference.path
        groups.setdefault(key, []).append((record_id, reference))
    return list(groups.values())


def _milliseconds(started: float, finished: float) -> float:
    return (finished - started) * SECONDS_TO_MILLISECONDS


def _require_one_kind(files: list[IndexedFile]) -> None:
    kinds = {media_kind_of(reference.path) for _, reference in files}
    if len(kinds) > 1:
        raise MixedMediaError(
            "una columna con índice de similitud guarda solo imágenes o solo audios, y esta "
            "tiene de los dos"
        )
