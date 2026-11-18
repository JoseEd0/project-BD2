"""El tipo de un archivo multimedia, decidido por su extensión."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum, unique
from pathlib import Path

from .errors import FragmentOnImageError, UnsupportedMediaTypeError
from .fragments import MediaReference, TemporalFragment, split_fragment

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


def media_reference(text: str, locate: Callable[[str], Path]) -> MediaReference:
    """El archivo que nombra `text` y el tramo que pide su `#t=`, si lo lleva.

    `locate` convierte la ruta escrita, ya sin el fragmento, en la del archivo, y rechaza la
    que no se puede leer.

    Raises:
        MediaFragmentError: si el fragmento no se puede leer, está vacío o es de una imagen.
        UnsupportedMediaTypeError: si la extensión no es de imagen ni de audio.
    """
    written, fragment = split_fragment(text)
    path = locate(written)
    if fragment is not None and media_kind_of(path) is MediaKind.IMAGE:
        raise fragment_on_image(path, fragment)
    return MediaReference(path, fragment)


def fragment_on_image(path: Path, fragment: TemporalFragment) -> FragmentOnImageError:
    return FragmentOnImageError(
        f"'{path.name}#{fragment}' es una imagen: los fragmentos temporales (#t=) son de audio"
    )
