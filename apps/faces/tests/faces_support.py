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


# Las fotos de `fixtures/` son retratos chicos (rostros de 65 px). Una «selfie» es el rostro
# recortado y ampliado a 570 x 660, con el rostro de unos 190 px: lo que da la cámara frontal
# de un celular a un brazo de distancia.
PORTRAIT_FACE_REGION = (60, 30, 250, 250)
SELFIE_SIZE = (570, 660)
# Los rostros de la foto de grupo (x, y, ancho, alto). El de (510, 200) es Eileen Collins,
# la comandante del STS-114: la misma persona de los retratos.
CREW_FACES = {
    "collins": (510, 200, 42, 49),
    "other": (235, 119, 46, 58),
    "another": (558, 113, 43, 56),
}
# El recorte de un tripulante abarca su rostro y otro tanto alrededor, ampliado cuatro veces.
CREW_CROP_MARGIN = 1
CREW_ZOOM = 4
JPEG_QUALITY = 95


def portrait_image(name: str) -> Image.Image:
    with Image.open(FIXTURES / name) as image:
        return image.convert("RGB")


def selfie(name: str = SINGLE_PORTRAIT) -> Image.Image:
    """El retrato `name` encuadrado como una selfie."""
    face = portrait_image(name).crop(PORTRAIT_FACE_REGION)
    return face.resize(SELFIE_SIZE, Image.Resampling.LANCZOS)


def crew_member(name: str) -> Image.Image:
    """El rostro de un tripulante de la foto de grupo, encuadrado como una selfie."""
    x, y, width, height = CREW_FACES[name]
    margin_x, margin_y = width * CREW_CROP_MARGIN, height * CREW_CROP_MARGIN
    region = (x - margin_x, y - margin_y, x + width + margin_x, y + height + margin_y)
    crop = portrait_image(GROUP_PHOTO).crop(region)
    return crop.resize((crop.width * CREW_ZOOM, crop.height * CREW_ZOOM), Image.Resampling.LANCZOS)


def jpeg(image: Image.Image) -> bytes:
    return encoded(image, "JPEG", quality=JPEG_QUALITY)
