"""Un cuadro de video en dos pasos que corren en un hilo con su `FacePipeline`: decodificar y
detectar, y después describir solo los rostros que el seguimiento no identificó ya.

Cada paso mide sus etapas, porque el reconocimiento en vivo las devuelve con cada cuadro.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from .detection import DetectedFace
from .image import BgrImage, decode_image
from .pipeline import FacePipeline
from .recognition import Embedding

MILLISECONDS_PER_SECOND = 1000.0


@dataclass(frozen=True, slots=True)
class DetectedFrame:
    """La imagen del cuadro, sus rostros del más grande al más chico y lo que costó."""

    image: BgrImage
    faces: tuple[DetectedFace, ...]
    decode_ms: float
    detect_ms: float


@dataclass(frozen=True, slots=True)
class DescribedFaces:
    embeddings: tuple[Embedding, ...]
    embed_ms: float


def detect_frame(
    pipeline: FacePipeline, data: bytes, max_pixels: int, max_faces: int
) -> DetectedFrame:
    """Los `max_faces` rostros más grandes del cuadro `data`.

    Raises:
        ImageTooLargeError: si declara más de `max_pixels` píxeles.
        InvalidImageError: si no es una imagen aceptada.
    """
    started = time.perf_counter()
    image = decode_image(data, max_pixels)
    decoded = time.perf_counter()
    faces = tuple(pipeline.detect(image)[:max_faces])
    return DetectedFrame(image, faces, _ms(decoded - started), _ms(time.perf_counter() - decoded))


def describe_faces(
    pipeline: FacePipeline, image: BgrImage, faces: Sequence[DetectedFace]
) -> DescribedFaces:
    """El embedding de cada rostro de `faces`, en su orden."""
    started = time.perf_counter()
    embeddings = tuple(pipeline.embed(image, face) for face in faces)
    return DescribedFaces(embeddings, _ms(time.perf_counter() - started))


def _ms(seconds: float) -> float:
    return seconds * MILLISECONDS_PER_SECOND
