"""`ExtractFeatures` (§2.4.1): de un archivo multimedia a su matriz de descriptores locales."""

from __future__ import annotations

from pathlib import Path

from config import EngineConfig

from .audio import extract_mfcc, mfcc_dimension
from .descriptors import Descriptors
from .fragments import TemporalFragment
from .image import SIFT_DESCRIPTOR_DIMENSION, extract_sift
from .media import MediaKind, fragment_on_image, media_kind_of


def extract_features(
    path: Path, config: EngineConfig, fragment: TemporalFragment | None = None
) -> Descriptors:
    """Los descriptores locales del archivo de `path`: SIFT si es una imagen y MFCC si es
    un audio, según su extensión; de un audio, solo del tramo `fragment` si se da. Es
    determinista: el mismo archivo con la misma configuración da la misma matriz, bit a bit.

    Returns:
        Una matriz `(n, descriptor_dimension(kind, config))` en `float32`, con `n = 0`
        si del archivo no sale ningún descriptor.

    Raises:
        UnsupportedMediaTypeError: si la extensión no es de imagen ni de audio.
        FragmentOnImageError: si se pide un tramo de una imagen.
        FragmentOutOfRangeError: si el tramo empieza después del final del audio.
        UnreadableMediaError: si el archivo no se puede leer o decodificar.
    """
    if media_kind_of(path) is MediaKind.AUDIO:
        return extract_mfcc(path, config, fragment)
    if fragment is not None:
        raise fragment_on_image(path, fragment)
    return extract_sift(path, config)


def descriptor_dimension(kind: MediaKind, config: EngineConfig) -> int:
    """Columnas de la matriz que `extract_features` da para un archivo de tipo `kind`."""
    if kind is MediaKind.IMAGE:
        return SIFT_DESCRIPTOR_DIMENSION
    return mfcc_dimension(config)

