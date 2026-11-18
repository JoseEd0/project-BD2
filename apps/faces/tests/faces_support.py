"""Fotos de prueba: las tres fotos de `fixtures/` y las que se generan a partir de ellas."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"
SINGLE_PORTRAIT = "collins-1998.jpg"
OTHER_PORTRAIT = "collins-early.jpg"
GROUP_PHOTO = "sts-114-crew.jpg"
GROUP_SIZE = 7


def encoded(image: Image.Image, image_format: str, **options: object) -> bytes:
    """`image` codificada en `image_format` (JPEG, PNG...), con las opciones de Pillow."""
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **options)
    return buffer.getvalue()


def solid(width: int, height: int, gray: int) -> Image.Image:
    return Image.fromarray(np.full((height, width, 3), gray, dtype=np.uint8))
