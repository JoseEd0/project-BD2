"""De los bytes que sube alguien a una imagen BGR de 8 bits, derecha y sin transparencia.

Decodifica Pillow y no `cv2.imdecode` por dos motivos. Pillow lee primero la cabecera, así
que el tamaño declarado se rechaza antes de reservar memoria para los píxeles; y aplica la
orientación EXIF de la foto de un celular sin perder el canal alfa ni la profundidad, que
`cv2.IMREAD_UNCHANGED` conserva pero a costa de ignorar esa orientación.
"""

from __future__ import annotations

import io
import math
from typing import TypeAlias

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import ImageTooLargeError, InvalidImageError

BgrImage: TypeAlias = npt.NDArray[np.uint8]

# Lo que sube un navegador o un celular; el lector de JPEG abre también las MPO de varias
# cámaras. Fuera quedan formatos que Pillow abre con programas externos (EPS) o que nadie
# usa para una foto.
ACCEPTED_FORMATS = ("JPEG", "PNG", "WEBP", "BMP", "GIF", "TIFF")
# Los modos de 16 o 32 bits por canal en gris; se llevan a 8 bits con su byte alto.
HIGH_DEPTH_MODES = frozenset({"I", "I;16", "I;16B", "I;16L", "I;16N"})
HIGH_DEPTH_SHIFT = 8
MAX_8_BIT = 255
# La transparencia se aplana sobre blanco: un fondo negro con el rostro recortado haría de
# borde fuerte para el detector, el blanco es lo que muestra el navegador.
TRANSPARENT_BACKGROUND = (255, 255, 255, 255)
MIN_WHOLE_FACTOR = 2


def decode_image(data: bytes, max_pixels: int) -> BgrImage:
    """La imagen de `data` en BGR de 8 bits, con la orientación EXIF ya aplicada.

    El gris pasa a tres canales iguales, la transparencia se aplana sobre blanco y de una
    imagen animada se toma el primer cuadro.

    Raises:
        ImageTooLargeError: si la cabecera declara más de `max_pixels` píxeles.
        InvalidImageError: si no es una imagen de `ACCEPTED_FORMATS` o está dañada.
    """
    try:
        with Image.open(io.BytesIO(data), formats=ACCEPTED_FORMATS) as image:
            _reject_oversized(image, max_pixels)
            upright = ImageOps.exif_transpose(image)
            return _to_bgr(upright)
    except UnidentifiedImageError as error:
        accepted = ", ".join(ACCEPTED_FORMATS)
        raise InvalidImageError(f"no es una imagen en un formato aceptado ({accepted})") from error
    except Image.DecompressionBombError as error:
        raise ImageTooLargeError(str(error)) from error
    except (OSError, ValueError, SyntaxError) as error:
        raise InvalidImageError(f"la imagen está dañada: {error}") from error


def resized_by(image: BgrImage, scale: float) -> BgrImage:
    """`image` con sus dos lados multiplicados por `scale`, sin deformarla. Reduce
    promediando áreas, que no deja dientes de sierra, y amplía con interpolación lineal."""
    if scale == 1:
        return image
    height, width = image.shape[:2]
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    if scale > 1:
        return np.asarray(cv2.resize(image, size, interpolation=cv2.INTER_LINEAR), dtype=np.uint8)
    return np.asarray(
        cv2.resize(_shrunk_by_whole_factor(image, scale), size, interpolation=cv2.INTER_AREA),
        dtype=np.uint8,
    )


def _shrunk_by_whole_factor(image: BgrImage, scale: float) -> BgrImage:
    """`image` reducida por el mayor entero que no pasa de `1 / scale`.

    `INTER_AREA` promedia bloques enteros de píxeles por un camino rápido, y con un factor
    fraccionario cae a uno general unas diez veces más lento: llevar una foto de 4 000 px a
    640 tarda 64 ms de una vez y 7 ms en dos pasos, primero por 6 y luego el resto (medido
    con un hilo). El resultado promedia las mismas áreas.
    """
    factor = math.floor(1 / scale)
    if factor < MIN_WHOLE_FACTOR:
        return image
    shrink = 1 / factor
    return np.asarray(
        cv2.resize(image, None, fx=shrink, fy=shrink, interpolation=cv2.INTER_AREA),
        dtype=np.uint8,
    )


def _reject_oversized(image: Image.Image, max_pixels: int) -> None:
    width, height = image.size
    if width * height > max_pixels:
        raise ImageTooLargeError(
            f"la imagen declara {width} x {height} píxeles y el máximo es {max_pixels}"
        )


def _to_bgr(image: Image.Image) -> BgrImage:
    rgb = _to_rgb(image)
    return np.ascontiguousarray(np.asarray(rgb, dtype=np.uint8)[:, :, ::-1])


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in HIGH_DEPTH_MODES:
        return _to_eight_bits(image).convert("RGB")
    if image.has_transparency_data:
        background = Image.new("RGBA", image.size, TRANSPARENT_BACKGROUND)
        background.alpha_composite(image.convert("RGBA"))
        return background.convert("RGB")
    return image.convert("RGB")


def _to_eight_bits(image: Image.Image) -> Image.Image:
    levels = np.asarray(image, dtype=np.int64) >> HIGH_DEPTH_SHIFT
    return Image.fromarray(np.clip(levels, 0, MAX_8_BIT).astype(np.uint8))
