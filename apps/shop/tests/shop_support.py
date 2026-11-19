"""Apoyo de los tests de la tienda: una red falsa que cuenta pedidos, imágenes diminutas y
las imágenes sintéticas con que se prueba CLIP.

Ningún test sale a la red: las fotos y los archivos publicados se sirven desde memoria.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from shop.dataset.errors import HttpStatusError

DISC_CANVAS = (300, 200)
DISC_BOX = (60, 20, 240, 180)


class FakeTransport:
    """Responde a cada URL con lo que se le programó: bytes, o un estado de error. Una
    lista se consume en orden, para simular un fallo que luego se arregla."""

    def __init__(self) -> None:
        self.responses: dict[str, list[bytes | int]] = {}
        self.requests: list[str] = []

    def serve(self, url: str, *responses: bytes | int) -> None:
        self.responses[url] = list(responses)

    @contextmanager
    def open(self, url: str) -> Iterator[io.BytesIO]:
        self.requests.append(url)
        queue = self.responses.get(url, [404])
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, int):
            raise HttpStatusError(url, response)
        yield io.BytesIO(response)


def image_bytes(
    size: tuple[int, int], color: tuple[int, int, int], image_format: str = "PNG"
) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, image_format)
    return output.getvalue()


def write_jsonl(path: Path, records: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def pattern_image(width: int, height: int) -> Image.Image:
    """Una imagen determinista con gradientes y una trama: cada píxel distinto, para que un
    corte o una interpolación equivocados cambien los valores."""
    y, x = np.mgrid[0:height, 0:width]
    channels = (
        x * 255 // max(width - 1, 1),
        y * 255 // max(height - 1, 1),
        (x * 7 + y * 13) % 256,
    )
    return Image.fromarray(np.stack(channels, axis=-1).astype(np.uint8), "RGB")


def disc(color: str) -> Image.Image:
    """Un disco de `color` sobre blanco, que CLIP reconoce por su color."""
    image = Image.new("RGB", DISC_CANVAS, "white")
    ImageDraw.Draw(image).ellipse(DISC_BOX, fill=color)
    return image

