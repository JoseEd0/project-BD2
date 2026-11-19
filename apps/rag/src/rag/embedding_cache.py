"""Caché en disco de los embeddings de los fragmentos, por huella del texto y modelo.

Calcular los embeddings del corpus es lo más caro de la ingesta (decenas de minutos en
CPU). La caché los guarda por la huella SHA-256 del texto de cada fragmento, así que volver
a sembrar el gestor —en otra máquina, tras borrar su volumen, con otro esquema— no vuelve a
pasar ningún fragmento por el modelo, y un fragmento que no cambió entre dos corpus
tampoco.

Cada modelo (nombre, revisión y precisión: `ModelFiles.model_id`) tiene su carpeta con dos
archivos que solo crecen:

    <carpeta>/<model_id>/vectors.f32   los vectores, float32, uno detrás de otro
    <carpeta>/<model_id>/keys.txt      la huella de cada vector, una por línea, en el mismo orden

Se escriben primero los vectores y después las huellas, con `fsync` en cada lote: si el
proceso muere entre las dos escrituras, al abrir sobran vectores sin huella, y se recortan.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt

from .embeddings import EMBEDDING_DIMENSIONS, Embeddings
from .errors import EmbeddingCacheError

VECTORS_FILENAME = "vectors.f32"
KEYS_FILENAME = "keys.txt"
ENCODING = "ascii"
VECTOR_BYTES = EMBEDDING_DIMENSIONS * np.dtype(np.float32).itemsize
# Un `model_id` lleva `@` y `-`; el resto de caracteres no válidos en un nombre de carpeta
# se reemplaza por este.
SAFE_REPLACEMENT = "_"


class EmbeddingCache:
    """Los embeddings ya calculados con un modelo, por huella del texto."""

    def __init__(self, directory: Path, model_id: str) -> None:
        self._directory = directory / _safe_name(model_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._vectors_path = self._directory / VECTORS_FILENAME
        self._keys_path = self._directory / KEYS_FILENAME
        self._positions = _recovered_positions(self._vectors_path, self._keys_path)

    def __len__(self) -> int:
        return len(self._positions)

    def __contains__(self, key: object) -> bool:
        return key in self._positions

    def missing(self, keys: Iterable[str]) -> list[str]:
        """Las huellas de `keys` que la caché no tiene, sin repetir y en su orden."""
        return list(dict.fromkeys(key for key in keys if key not in self._positions))

    def add(self, keys: Sequence[str], vectors: Embeddings) -> None:
        """Agrega los vectores de `keys` (una fila por huella) y los deja en el disco.

        Raises:
            EmbeddingCacheError: si las filas no son `len(keys)` vectores del modelo.
        """
        if vectors.shape != (len(keys), EMBEDDING_DIMENSIONS):
            raise EmbeddingCacheError(
                f"se esperaban {len(keys)} vectores de {EMBEDDING_DIMENSIONS} dimensiones y "
                f"llegó una matriz {vectors.shape}"
            )
        first_of_each = {key: position for position, key in reversed(list(enumerate(keys)))}
        fresh = [position for key, position in first_of_each.items() if key not in self._positions]
        if not fresh:
            return
        _append_durably(self._vectors_path, vectors[fresh].astype(np.float32).tobytes())
        lines = "".join(keys[position] + "\n" for position in fresh)
        _append_durably(self._keys_path, lines.encode(ENCODING))
        for position in fresh:
            self._positions[keys[position]] = len(self._positions)

    def vectors(self, keys: Sequence[str]) -> Embeddings:
        """Los vectores de `keys`, una fila por huella, leídos del disco sin cargar el resto.

        Raises:
            KeyError: con la primera huella que la caché no tiene.
        """
        rows = [self._positions[key] for key in keys]
        stored: npt.NDArray[np.float32] = np.memmap(
            self._vectors_path,
            dtype=np.float32,
            mode="r",
            shape=(len(self._positions), EMBEDDING_DIMENSIONS),
        )
        return np.array(stored[rows], dtype=np.float32)


def _safe_name(model_id: str) -> str:
    return "".join(
        character if character.isalnum() or character in "@-." else SAFE_REPLACEMENT
        for character in model_id
    )


def _recovered_positions(vectors_path: Path, keys_path: Path) -> dict[str, int]:
    """Las huellas guardadas con su fila; recorta lo que dejó a medias un proceso muerto."""
    keys = _complete_lines(keys_path)
    stored_vectors = vectors_path.stat().st_size // VECTOR_BYTES if vectors_path.exists() else 0
    complete = min(len(keys), stored_vectors)
    if vectors_path.exists():
        os.truncate(vectors_path, complete * VECTOR_BYTES)
    kept = "".join(key + "\n" for key in keys[:complete]).encode(ENCODING)
    if keys_path.exists() and keys_path.stat().st_size != len(kept):
        keys_path.write_bytes(kept)
    return {key: row for row, key in enumerate(keys[:complete])}


def _complete_lines(path: Path) -> list[str]:
    """Las líneas de `path` que terminan en salto de línea: la última puede estar a medias."""
    if not path.exists():
        return []
    text = path.read_text(encoding=ENCODING)
    return text.splitlines()[: text.count("\n")]


def _append_durably(path: Path, content: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
