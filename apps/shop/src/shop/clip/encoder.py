"""Las dos torres de CLIP en onnxruntime: imágenes y textos al mismo espacio de 512
dimensiones, con norma 1.

Con norma 1 el producto punto es el coseno, la métrica del HNSW de `shop_clip`, y una
consulta de texto se compara directamente con los embeddings de las fotos: eso es buscar
imágenes con palabras.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TypeAlias

import numpy as np
import numpy.typing as npt
import onnxruntime
from PIL import Image, UnidentifiedImageError

from .errors import UnreadableImageError
from .preprocessing import RGB, PixelValues, pixel_values
from .tokenizer import ClipTokenizer

Embeddings: TypeAlias = npt.NDArray[np.float32]

EMBEDDING_DIMENSION = 512
CPU_PROVIDER = "CPUExecutionProvider"
PIXEL_INPUT = "pixel_values"
TOKEN_INPUT = "input_ids"
# Con 0 onnxruntime usa un hilo por núcleo físico.
DEFAULT_THREADS = 0


def load_image(path: Path) -> Image.Image:
    """La imagen de `path`, ya decodificada.

    Raises:
        UnreadableImageError: si no existe o no es una imagen que Pillow entienda.
    """
    try:
        with Image.open(path) as image:
            return image.convert(RGB)
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise UnreadableImageError(f"{path} no es una imagen legible: {error}") from error


def normalized_rows(vectors: npt.NDArray[np.float32]) -> Embeddings:
    """Cada fila dividida por su norma."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.asarray(vectors / norms, dtype=np.float32)


class ClipImageEncoder:
    """La torre de imagen. Una instancia por proceso: la sesión de onnxruntime ya reparte
    cada lote entre `threads` hilos."""

    def __init__(self, model: Path, threads: int = DEFAULT_THREADS) -> None:
        self._session = _session(model, threads)

    def embed(self, images: Sequence[Image.Image]) -> Embeddings:
        """Los embeddings de `images`, `(len(images), 512)` con norma 1."""
        return self.embed_pixels(np.stack([pixel_values(image) for image in images]))

    def embed_pixels(self, batch: PixelValues) -> Embeddings:
        """Los embeddings de un lote ya preprocesado, `(n, 3, 224, 224)`."""
        (embeddings,) = self._session.run(None, {PIXEL_INPUT: batch})
        return normalized_rows(embeddings)


class ClipTextEncoder:
    """La torre de texto con su tokenizador."""

    def __init__(
        self, model: Path, tokenizer: ClipTokenizer, threads: int = DEFAULT_THREADS
    ) -> None:
        self._session = _session(model, threads)
        self._tokenizer = tokenizer

    def embed(self, texts: Sequence[str]) -> Embeddings:
        """Los embeddings de `texts`, `(len(texts), 512)` con norma 1."""
        (embeddings,) = self._session.run(None, {TOKEN_INPUT: self._tokenizer.encode(texts)})
        return normalized_rows(embeddings)


def _session(model: Path, threads: int) -> onnxruntime.InferenceSession:
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = threads
    return onnxruntime.InferenceSession(str(model), options, providers=[CPU_PROVIDER])
