"""El embedding de un rostro con SFace: alinear con los cinco puntos y describir en 128
números de norma 1."""

from __future__ import annotations

from pathlib import Path
from typing import TypeAlias

import cv2
import numpy as np
import numpy.typing as npt

from .detection import DetectedFace
from .errors import FaceError
from .image import BgrImage

EMBEDDING_DIMENSION = 128
Embedding: TypeAlias = npt.NDArray[np.float32]


class FaceEmbedder:
    """SFace sobre `cv2.FaceRecognizerSF`. Como el detector, una instancia por hilo."""

    def __init__(self, model: Path) -> None:
        self._network = cv2.FaceRecognizerSF.create(str(model), "")

    def align(self, image: BgrImage, face: DetectedFace) -> BgrImage:
        """El rostro recortado a 112 x 112 con los ojos, la nariz y la boca en el sitio en
        que SFace los vio al entrenar: una semejanza (giro, escala y traslado) que lleva los
        cinco puntos a los de referencia."""
        return np.asarray(self._network.alignCrop(image, face.as_row()), dtype=np.uint8)

    def describe(self, aligned: BgrImage) -> Embedding:
        """El embedding de un rostro ya alineado, de norma 1."""
        feature = np.asarray(self._network.feature(aligned), dtype=np.float32).reshape(-1)
        return l2_normalized(feature)

    def embed(self, image: BgrImage, face: DetectedFace) -> Embedding:
        """El embedding de `face`, un rostro detectado en `image`."""
        return self.describe(self.align(image, face))


def l2_normalized(vector: npt.NDArray[np.float32]) -> Embedding:
    """`vector` dividido por su norma, en `float32`: así el producto punto de dos
    embeddings es su coseno, que es la similitud con que se entrenó SFace.

    Raises:
        FaceError: si el vector es nulo y no tiene dirección.
    """
    norm = float(np.linalg.norm(vector))
    if norm == 0:
        raise FaceError("el embedding es el vector nulo")
    return np.asarray(vector / norm, dtype=np.float32)


def cosine_similarity(first: Embedding, second: Embedding) -> float:
    """El coseno entre dos embeddings de norma 1: 1 si son el mismo, cerca de 0 si no
    tienen nada que ver."""
    return float(np.dot(first, second))
