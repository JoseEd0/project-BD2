"""Descriptores SIFT de una imagen, con OpenCV.

Cada llamada a OpenCV pasa por una función de este módulo con tipos de NumPy, para que el
resto del paquete no dependa de la API de `cv2`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

import cv2
import numpy as np
import numpy.typing as npt

from config import EngineConfig

from .descriptors import DESCRIPTOR_DTYPE, Descriptors, no_descriptors
from .errors import UnreadableMediaError

# Una rejilla de 4 x 4 celdas alrededor del punto, con un histograma de 8 orientaciones en
# cada una (Lowe, «Distinctive Image Features from Scale-Invariant Keypoints», 2004).
SIFT_DESCRIPTOR_DIMENSION = 128
OPENCV_WORKER_THREADS = 1

GrayImage: TypeAlias = npt.NDArray[np.uint8]


def extract_sift(path: Path, config: EngineConfig) -> Descriptors:
    """Los descriptores SIFT de la imagen de `path`, `(n, 128)` en `float32`.

    La imagen se lee en escala de grises y, si su lado mayor pasa de
    `config.image_max_side`, se reduce sin deformarla. Se conservan los
    `config.image_max_descriptors` puntos de mayor respuesta, en orden de respuesta
    decreciente; una imagen sin puntos clave da `(0, 128)`.

    Raises:
        UnreadableMediaError: si el archivo no se puede leer o no es una imagen.
    """
    image = _downscaled(_read_grayscale(path), config.image_max_side)
    return _strongest_descriptors(image, config.image_max_descriptors)


def confine_opencv_to_one_thread() -> None:
    """Deja a OpenCV con un solo hilo en este proceso, para que varios procesos de la
    extracción en paralelo no se repartan los núcleos por segunda vez."""
    cv2.setNumThreads(OPENCV_WORKER_THREADS)


def _read_grayscale(path: Path) -> GrayImage:
    """Se leen los bytes y se decodifican en memoria, en vez de `cv2.imread`, porque así
    un archivo que falta da el error del sistema en lugar de un `None` sin motivo."""
    try:
        encoded = np.fromfile(path, dtype=np.uint8)
    except OSError as error:
        raise UnreadableMediaError(f"no se puede leer '{path.name}': {error}") from error
    decoded = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE) if encoded.size else None
    if decoded is None:
        raise UnreadableMediaError(f"'{path.name}' no es una imagen que se pueda decodificar")
    return np.asarray(decoded, dtype=np.uint8)


def _downscaled(image: GrayImage, max_side: int) -> GrayImage:
    height, width = image.shape
    longer_side = max(height, width)
    if longer_side <= max_side:
        return image
    scale = max_side / longer_side
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return np.asarray(cv2.resize(image, size, interpolation=cv2.INTER_AREA), dtype=np.uint8)


def _strongest_descriptors(image: GrayImage, limit: int) -> Descriptors:
    """OpenCV puede devolver los puntos en otro orden según cuántos hilos use; ordenarlos
    por respuesta, y por posición, escala y ángulo cuando la respuesta empata, hace que la
    imagen dé la misma matriz en cualquier proceso."""
    keypoints, descriptors = cv2.SIFT.create().detectAndCompute(image, None)
    if not keypoints:
        return no_descriptors(SIFT_DESCRIPTOR_DIMENSION)
    order = np.lexsort(
        (
            [keypoint.angle for keypoint in keypoints],
            [keypoint.size for keypoint in keypoints],
            [keypoint.pt[0] for keypoint in keypoints],
            [keypoint.pt[1] for keypoint in keypoints],
            [-keypoint.response for keypoint in keypoints],
        )
    )
    return np.asarray(descriptors, dtype=DESCRIPTOR_DTYPE)[order[:limit]]
