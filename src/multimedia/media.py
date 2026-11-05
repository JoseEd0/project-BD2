"""El tipo de un archivo multimedia, decidido por su extensión."""

from __future__ import annotations

from enum import Enum, unique
from pathlib import Path

from .errors import UnsupportedMediaTypeError

# Los formatos que decodifican, sin códecs aparte, OpenCV (imágenes) y libsndfile (audio,
# MP3 incluido desde su versión 1.1).
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg"})


@unique
class MediaKind(Enum):
    IMAGE = "image"
    AUDIO = "audio"


def media_kind_of(path: Path) -> MediaKind:
    """El tipo de `path` según su extensión, sin mirar mayúsculas ni leer el archivo.

    Raises:
        UnsupportedMediaTypeError: si la extensión no es de imagen ni de audio.
    """
    extension = path.suffix.lower()
    if extension in IMAGE_EXTENSIONS:
        return MediaKind.IMAGE
    if extension in AUDIO_EXTENSIONS:
        return MediaKind.AUDIO
    supported = ", ".join(sorted(IMAGE_EXTENSIONS | AUDIO_EXTENSIONS))
    raise UnsupportedMediaTypeError(
        f"'{path.name}' no es una imagen ni un audio conocidos (extensiones: {supported})"
    )
