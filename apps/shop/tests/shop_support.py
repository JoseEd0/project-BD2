"""Apoyo de los tests del dataset: una red falsa que cuenta pedidos e imágenes diminutas.

Ningún test sale a la red: las fotos y los archivos publicados se sirven desde memoria.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from shop.dataset.errors import HttpStatusError


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
