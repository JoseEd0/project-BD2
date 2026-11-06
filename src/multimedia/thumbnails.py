"""Miniaturas de imágenes para mostrar en la galería del resultado de un `SIMILAR_TO`."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .errors import UnreadableMediaError

THUMBNAIL_EXTENSION = ".jpg"
# Calidad de JPEG de las miniaturas: a 256 px no se distingue de la original y pesa la mitad
# que con 95.
THUMBNAIL_QUALITY = 85


def thumbnail(path: Path, side: int) -> bytes:
    """La imagen de `path` reducida a `side` píxeles de lado mayor, en JPEG; una imagen
    menor no se agranda.

    Raises:
        UnreadableMediaError: si el archivo no se puede leer o no es una imagen.
    """
    try:
        encoded = np.fromfile(path, dtype=np.uint8)
    except OSError as error:
        raise UnreadableMediaError(f"no se puede leer '{path.name}': {error}") from error
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR) if encoded.size else None
    if image is None:
        raise UnreadableMediaError(f"'{path.name}' no es una imagen que se pueda decodificar")
    height, width = image.shape[:2]
    scale = min(1.0, side / max(height, width))
    if scale < 1.0:
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    quality = [cv2.IMWRITE_JPEG_QUALITY, THUMBNAIL_QUALITY]
    ok, jpeg = cv2.imencode(THUMBNAIL_EXTENSION, image, quality)
    if not ok:
        raise UnreadableMediaError(f"no se pudo reducir '{path.name}'")
    return bytes(jpeg.tobytes())
