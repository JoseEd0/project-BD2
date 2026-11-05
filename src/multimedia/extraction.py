"""`ExtractFeatures` (§2.4.1): de un archivo multimedia a su matriz de descriptores locales."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from config import EngineConfig

from .audio import extract_mfcc, mfcc_dimension
from .descriptors import Descriptors
from .image import SIFT_DESCRIPTOR_DIMENSION, extract_sift
from .media import MediaKind, media_kind_of

_EXTRACTORS: dict[MediaKind, Callable[[Path, EngineConfig], Descriptors]] = {
    MediaKind.IMAGE: extract_sift,
    MediaKind.AUDIO: extract_mfcc,
}


def extract_features(path: Path, config: EngineConfig) -> Descriptors:
    """Los descriptores locales del archivo de `path`: SIFT si es una imagen y MFCC si es
    un audio, según su extensión. Es determinista: el mismo archivo con la misma
    configuración da la misma matriz, bit a bit.

    Returns:
        Una matriz `(n, descriptor_dimension(kind, config))` en `float32`, con `n = 0`
        si del archivo no sale ningún descriptor.

    Raises:
        UnsupportedMediaTypeError: si la extensión no es de imagen ni de audio.
        UnreadableMediaError: si el archivo no se puede leer o decodificar.
    """
    return _EXTRACTORS[media_kind_of(path)](path, config)


def descriptor_dimension(kind: MediaKind, config: EngineConfig) -> int:
    """Columnas de la matriz que `extract_features` da para un archivo de tipo `kind`."""
    if kind is MediaKind.IMAGE:
        return SIFT_DESCRIPTOR_DIMENSION
    return mfcc_dimension(config)
