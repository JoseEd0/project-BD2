"""Las características de una columna multimedia: vocabulario, idf y un histograma por fila.

Es lo que el primer índice vectorial de una columna construye y los demás reutilizan: IVF y
HNSW sobre la misma columna se comparan sobre los mismos vectores, y un índice dañado se
reconstruye desde aquí sin volver a extraer nada. Vive en tres archivos junto a la tabla:

| Archivo | Contenido |
|---|---|
| `<base>.words` | heap file: un registro `(fila, descriptores, conteos)` por fila |
| `<base>.words.map` | hash extendible: dirección de la fila → dirección de su registro |
| `<base>.model` | vocabulario (centroides), idf y objetos al construir, reemplazado de una vez |

Se guardan los **conteos**, no el TF-IDF: el idf solo se conoce al terminar de recorrer la
colección, y con los conteos guardados el vector de cada fila se calcula al leerlo
(`weighting.tf_idf`), sin una segunda pasada que reescriba el archivo.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import numpy as np
import numpy.typing as npt

from config import EngineConfig
from index.hash import ExtendibleHashIndex
from index.keys import RecordIdKeyCodec
from index.vector.ids import vector_id_of
from storage.atomic import write_atomically
from storage.heap import HeapFile
from storage.record import RecordSerializer
from storage.record_id import RECORD_ID_SIZE, RecordId
from storage.schema import Field, Schema
from storage.types import FieldType, StorageError

from .descriptors import Descriptors
from .vocabulary import Vocabulary
from .weighting import InverseFrequencies, Weights, inverse_document_frequencies, tf_idf

WORDS_SUFFIX = ".words"
MAP_SUFFIX = ".words.map"
MODEL_SUFFIX = ".model"
FORMAT_VERSION = 1
METADATA_ARRAY = "metadata"
CENTROIDS_ARRAY = "centroids"
IDF_ARRAY = "idf"
ROW_FIELD = "fila"
DESCRIPTORS_FIELD = "descriptores"
COUNTS_FIELD = "conteos"
# Filas por lote al leer todos los vectores: 4 096 vectores de 256 palabras son 4 MiB, lo
# bastante para que numpy trabaje en bloque sin llevar la colección entera a memoria.
VECTOR_BATCH_ROWS = 4_096


class FeatureStoreError(StorageError):
    """El almacén no existe, está dañado, ya está construido o la fila ya tiene vector."""


@dataclass(frozen=True, slots=True)
class FeatureStoreBuild:
    """Lo que costó construir el almacén, para el resultado de `CREATE INDEX`."""

    objects: int
    descriptors: int
    empty_objects: int
    training_descriptors: int
    vocabulary_size: int
    training_seconds: float
    quantizing_seconds: float


class FeatureStore:
    """Vocabulario, idf e histogramas de las filas de una columna multimedia.

    Complejidad: añadir, quitar y leer el vector de una fila cuestan `O(1)` páginas (el
    heap file y el hash extendible); recorrer todos los vectores, `O(páginas)`.
    """

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._path = path
        self._config = config
        self._vocabulary: Vocabulary | None = None
        self._idf: InverseFrequencies | None = None
        self._documents = 0
        self._words: HeapFile | None = None
        self._rows: ExtendibleHashIndex | None = None
        self._serializer: RecordSerializer | None = None
        if _model_path(path).exists():
            self._read_model()
            self._open_files()

    @staticmethod
    def files_of(path: Path) -> list[Path]:
        """Todos los archivos que ocupa el almacén con base `path`."""
        return [
            _model_path(path),
            path.with_name(path.name + WORDS_SUFFIX),
            *ExtendibleHashIndex.files_of(_map_path(path)),
        ]

    @property
    def built(self) -> bool:
        return self._idf is not None

    @property
    def count(self) -> int:
        return 0 if self._words is None else self._words.record_count

    @property
    def vocabulary_size(self) -> int:
        return self._require_vocabulary().size

    @property
    def descriptor_dimension(self) -> int:
        """Dimensión de los descriptores con que se entrenó: 128 con SIFT, la de MFCC con
        audio."""
        return self._require_vocabulary().descriptor_dimension

    def build(
        self, objects: Iterable[tuple[RecordId, Descriptors]], training: Descriptors
    ) -> FeatureStoreBuild:
        """Entrena el vocabulario con `training`, cuantiza cada objeto y fija el idf.

        Raises:
            NotEnoughDescriptorsError: si `training` tiene menos descriptores que palabras.
            FeatureStoreError: si el almacén ya estaba construido.
        """
        if self._vocabulary is not None:
            raise FeatureStoreError(f"el almacén {self._path} ya está construido")
        started = time.perf_counter()
        self._vocabulary = Vocabulary.train(
            training,
            self._config.vocabulary_size,
            self._config.kmeans_iterations,
            self._config.kmeans_seed,
        )
        trained = time.perf_counter()
        self._open_files()
        frequencies = np.zeros(self._vocabulary.size, dtype=np.int64)
        objects_seen = descriptors_seen = empty = 0
        for record_id, descriptors in objects:
            counts = self._vocabulary.word_counts(descriptors)
            self._store(record_id, counts)
            frequencies += counts > 0
            objects_seen += 1
            descriptors_seen += int(counts.sum())
            empty += not counts.any()
        self._idf = inverse_document_frequencies(frequencies, objects_seen)
        self._documents = objects_seen
        self._write_model()
        return FeatureStoreBuild(
            objects=objects_seen,
            descriptors=descriptors_seen,
            empty_objects=empty,
            training_descriptors=len(training),
            vocabulary_size=self._vocabulary.size,
            training_seconds=trained - started,
            quantizing_seconds=time.perf_counter() - trained,
        )

    def add(self, record_id: RecordId, descriptors: Descriptors) -> None:
        """Cuantiza y guarda una fila nueva con el vocabulario y el idf ya fijados.

        Raises:
            FeatureStoreError: si el almacén no está construido o la fila ya está.
        """
        self._store(record_id, self._require_vocabulary().word_counts(descriptors))

    def remove(self, record_id: RecordId) -> bool:
        """Quita la fila del almacén. Devuelve si estaba."""
        rows, words = self._require_files()
        found = rows.search(record_id)
        if not found:
            return False
        words.delete(RecordId.unpack(found[0]))
        rows.delete_entry(record_id, found[0])
        return True

    def contains(self, record_id: RecordId) -> bool:
        return bool(self._require_files()[0].search(record_id))

    def vector_of(self, record_id: RecordId) -> Weights | None:
        """El vector TF-IDF de la fila, o `None` si no está en el almacén."""
        rows, words = self._require_files()
        found = rows.search(record_id)
        if not found:
            return None
        _, counts = self._decode(words.read(RecordId.unpack(found[0])))
        return tf_idf(counts, self._require_idf())

    def vectors(self) -> Iterator[tuple[npt.NDArray[np.int64], Weights]]:
        """Todos los vectores TF-IDF en lotes `(identificadores de vector, matriz)`, en el
        orden físico del almacén."""
        _, words = self._require_files()
        idf = self._require_idf()
        ids: list[int] = []
        counts: list[npt.NDArray[np.float32]] = []
        for _, record in words.scan():
            row, row_counts = self._decode(record)
            ids.append(row)
            counts.append(row_counts)
            if len(ids) == VECTOR_BATCH_ROWS:
                yield np.array(ids, dtype=np.int64), tf_idf(np.stack(counts), idf)
                ids, counts = [], []
        if ids:
            yield np.array(ids, dtype=np.int64), tf_idf(np.stack(counts), idf)

    def vectorize(self, descriptors: Descriptors) -> Weights:
        """El vector TF-IDF de un objeto que no está en la tabla: la consulta."""
        return tf_idf(self._require_vocabulary().word_counts(descriptors), self._require_idf())

    def describe(self) -> dict[str, Any]:
        """Forma física del almacén, para inspeccionarla."""
        if self._vocabulary is None or self._idf is None or self._words is None:
            return {"built": False}
        return {
            "built": True,
            "vocabulary_size": self._vocabulary.size,
            "descriptor_dimension": self._vocabulary.descriptor_dimension,
            "objects": self.count,
            "objects_at_build": self._documents,
            "words_in_use": int((self._idf > 0).sum()),
            "pages": self._words.page_count,
        }

    def flush(self) -> None:
        if self._words is not None and self._rows is not None:
            self._words.flush()
            self._rows.flush()

    def close(self) -> None:
        if self._words is not None and self._rows is not None:
            self._words.close()
            self._rows.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _store(self, record_id: RecordId, counts: npt.NDArray[np.float32]) -> None:
        rows, words = self._require_files()
        if rows.search(record_id):
            raise FeatureStoreError(f"la fila {record_id} ya tiene vector en el almacén")
        record = self._require_serializer().pack(
            (vector_id_of(record_id), int(counts.sum()), tuple(counts.tolist()))
        )
        rows.insert(record_id, words.insert(record).pack())

    def _decode(self, record: bytes) -> tuple[int, npt.NDArray[np.float32]]:
        row, _, counts = self._require_serializer().unpack(record)
        if not isinstance(row, int) or not isinstance(counts, tuple):
            raise FeatureStoreError(f"un registro del almacén {self._path} está dañado")
        return row, np.asarray(counts, dtype=np.float32)

    def _open_files(self) -> None:
        vocabulary = self._require_vocabulary()
        schema = Schema(
            [
                Field(ROW_FIELD, FieldType.INT, nullable=False),
                Field(DESCRIPTORS_FIELD, FieldType.INT, nullable=False),
                Field(COUNTS_FIELD, FieldType.VECTOR, vocabulary.size, nullable=False),
            ]
        )
        self._serializer = RecordSerializer(schema)
        words_path = self._path.with_name(self._path.name + WORDS_SUFFIX)
        self._words = HeapFile(words_path, self._serializer.size, self._config)
        self._rows = ExtendibleHashIndex(
            _map_path(self._path), RecordIdKeyCodec(), RECORD_ID_SIZE, self._config
        )

    def _write_model(self) -> None:
        vocabulary = self._require_vocabulary()
        metadata = {"format": FORMAT_VERSION, "documents": self._documents}
        arrays = {
            METADATA_ARRAY: np.frombuffer(json.dumps(metadata).encode(), dtype=np.uint8),
            CENTROIDS_ARRAY: vocabulary.centroids,
            IDF_ARRAY: self._require_idf(),
        }
        write_atomically(_model_path(self._path), lambda file: np.savez(file, **arrays))

    def _read_model(self) -> None:
        path = _model_path(self._path)
        try:
            with np.load(path, allow_pickle=False) as saved:
                metadata = json.loads(saved[METADATA_ARRAY].tobytes())
                centroids = saved[CENTROIDS_ARRAY]
                idf = saved[IDF_ARRAY].astype(np.float64)
        except (OSError, ValueError, KeyError) as error:
            raise FeatureStoreError(f"no se puede leer el modelo {path}: {error}") from error
        if metadata.get("format") != FORMAT_VERSION or len(idf) != len(centroids):
            raise FeatureStoreError(f"{path} no es un modelo de características de este motor")
        self._vocabulary = Vocabulary(centroids)
        self._idf = idf
        self._documents = int(metadata["documents"])

    def _require_vocabulary(self) -> Vocabulary:
        if self._vocabulary is None:
            raise FeatureStoreError(f"el almacén {self._path} todavía no está construido")
        return self._vocabulary

    def _require_idf(self) -> InverseFrequencies:
        if self._idf is None:
            raise FeatureStoreError(f"el almacén {self._path} todavía no está construido")
        return self._idf

    def _require_files(self) -> tuple[ExtendibleHashIndex, HeapFile]:
        if self._rows is None or self._words is None:
            raise FeatureStoreError(f"el almacén {self._path} todavía no está construido")
        return self._rows, self._words

    def _require_serializer(self) -> RecordSerializer:
        if self._serializer is None:
            raise FeatureStoreError(f"el almacén {self._path} todavía no está construido")
        return self._serializer


def _model_path(path: Path) -> Path:
    return path.with_name(path.name + MODEL_SUFFIX)


def _map_path(path: Path) -> Path:
    return path.with_name(path.name + MAP_SUFFIX)
