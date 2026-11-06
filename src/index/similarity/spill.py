"""Los descriptores de la muestra del vocabulario, guardados mientras se construye un índice.

Construir un índice de similitud recorre los archivos dos veces: la primera, sobre una
muestra, para entrenar el vocabulario; la segunda, sobre todos, para cuantizarlos con él.
Decodificar es lo caro —un MP3 de 30 s cuesta unos 28 ms—, y con una tabla de hasta
`vocabulary_training_objects` filas la muestra es la tabla entera: cada archivo se
decodificaba dos veces. Aquí se guardan los descriptores de la muestra en un archivo
temporal la primera vez, y la segunda se leen de él.

El archivo crece con la muestra, no con la tabla: 2 000 pistas de 30 s ocupan unos 900 MB
y 2 000 fotos de catálogo unos 160 MB. Se borra al terminar, salga bien o no.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Self

import numpy as np

from multimedia import Descriptors
from storage.record_id import RecordId

SPILL_SUFFIX = ".muestra.tmp"
DESCRIPTOR_DTYPE = np.float32


class DescriptorSpill:
    """Descriptores por fila en un archivo temporal: se guardan una vez y se leen una vez."""

    def __init__(self, path: Path) -> None:
        self._path = path.with_name(path.name + SPILL_SUFFIX)
        self._file = self._path.open("w+b")
        self._entries: dict[RecordId, tuple[int, tuple[int, ...]]] = {}

    def keep(self, record_id: RecordId, descriptors: Descriptors) -> Descriptors:
        """Guarda los descriptores de la fila y los devuelve sin cambios."""
        matrix = np.ascontiguousarray(descriptors, dtype=DESCRIPTOR_DTYPE)
        self._entries[record_id] = (self._file.tell(), matrix.shape)
        self._file.write(matrix.tobytes())
        return matrix

    def __contains__(self, record_id: RecordId) -> bool:
        return record_id in self._entries

    def read(self, record_id: RecordId) -> Descriptors:
        """Los descriptores que se guardaron para la fila."""
        offset, shape = self._entries[record_id]
        self._file.flush()
        self._file.seek(offset)
        size = int(np.prod(shape)) * DESCRIPTOR_DTYPE().itemsize
        return np.frombuffer(self._file.read(size), dtype=DESCRIPTOR_DTYPE).reshape(shape)

    def close(self) -> None:
        self._file.close()
        self._path.unlink(missing_ok=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
