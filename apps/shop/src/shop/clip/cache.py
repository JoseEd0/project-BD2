"""Caché en disco de los embeddings de las fotos, por el SHA-256 de cada archivo.

Volver a cargar la tienda no vuelve a pasar por CLIP ninguna foto que ya pasó: el
embedding depende solo de los bytes de la foto y del modelo, así que la clave es el
SHA-256 de los bytes, y cada modelo tiene su propio archivo (su nombre lleva el comienzo
del SHA-256 del modelo).

El archivo es una secuencia de registros de tamaño fijo, `digest (32 bytes) + 512 float32`,
que solo crece al final. Al abrirlo se leen únicamente los digests, para saber en qué
posición está cada uno; los vectores se leen cuando se piden. Un corte a mitad de una
escritura deja un registro incompleto al final, que se descarta al abrir.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

import numpy as np

from .encoder import EMBEDDING_DIMENSION, Embeddings
from .errors import EmbeddingCacheError

DIGEST_BYTES = hashlib.sha256().digest_size
VECTOR_DTYPE = np.dtype("<f4")
CACHE_SUFFIX = ".f32"
MODEL_TAG_CHARACTERS = 12


def image_digest(data: bytes) -> bytes:
    """La clave de una foto: el SHA-256 de sus bytes."""
    return hashlib.sha256(data).digest()


class EmbeddingCache:
    """Los embeddings ya calculados con un modelo, en `directory`.

    Se usa con `with`: abre (o crea) el archivo del modelo y lo cierra al salir.

    Raises:
        EmbeddingCacheError: al abrir, si el archivo repite un digest: no es una caché de
            este formato.
    """

    def __init__(self, directory: Path, model_sha256: str) -> None:
        self.path = directory / f"clip-{model_sha256[:MODEL_TAG_CHARACTERS]}{CACHE_SUFFIX}"
        self._record_bytes = DIGEST_BYTES + EMBEDDING_DIMENSION * VECTOR_DTYPE.itemsize
        self._positions: dict[bytes, int] = {}
        self._file: BinaryIO | None = None

    def __enter__(self) -> EmbeddingCache:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+b")
        self._positions = self._read_digests(self._file)
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self._opened().close()
        self._file = None

    def __len__(self) -> int:
        return len(self._positions)

    def __contains__(self, digest: bytes) -> bool:
        return digest in self._positions

    def get(self, digest: bytes) -> Embeddings:
        """El embedding de la foto con ese digest, `(512,)`.

        Raises:
            KeyError: si no está.
        """
        handle = self._opened()
        handle.seek(self._positions[digest] * self._record_bytes + DIGEST_BYTES)
        vector = handle.read(self._record_bytes - DIGEST_BYTES)
        return np.frombuffer(vector, dtype=VECTOR_DTYPE).astype(np.float32)

    def put_all(self, entries: Iterable[tuple[bytes, Embeddings]]) -> None:
        """Añade al final los embeddings que no estaban y los lleva al disco."""
        handle = self._opened()
        handle.seek(0, 2)
        for digest, vector in entries:
            if digest in self._positions:
                continue
            handle.write(digest + np.asarray(vector, dtype=VECTOR_DTYPE).tobytes())
            self._positions[digest] = len(self._positions)
        handle.flush()

    def _opened(self) -> BinaryIO:
        if self._file is None:
            raise EmbeddingCacheError("la caché de embeddings se usa dentro de un `with`")
        return self._file

    def _read_digests(self, handle: BinaryIO) -> dict[bytes, int]:
        size = handle.seek(0, 2)
        complete = size // self._record_bytes
        if complete * self._record_bytes != size:
            handle.truncate(complete * self._record_bytes)
        positions: dict[bytes, int] = {}
        for position in range(complete):
            handle.seek(position * self._record_bytes)
            digest = handle.read(DIGEST_BYTES)
            if digest in positions:
                raise EmbeddingCacheError(f"{self.path} repite un digest en el registro {position}")
            positions[digest] = position
        return positions
