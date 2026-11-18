"""Detección de rostros con YuNet: la caja, los cinco puntos y la confianza de cada uno."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from .config import PipelineConfig
from .errors import MultipleFacesError, NoFaceError
from .image import BgrImage, resized_by

# Una fila de YuNet: x, y, ancho y alto de la caja; x e y del ojo derecho, el izquierdo, la
# punta de la nariz y las comisuras derecha e izquierda; y la confianza. «Derecho» es el de
# la persona, que en la foto queda a la izquierda.
BOX_COLUMNS = slice(0, 4)
LANDMARK_COLUMNS = slice(4, 14)
SCORE_COLUMN = 14
LANDMARK_COUNT = 5
POINT_COORDINATES = 2
# El tamaño con que se crea la red no importa: `detect` lo fija antes de cada imagen.
INITIAL_INPUT_SIZE = (320, 320)


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """La caja del rostro en píxeles de la imagen original; puede salirse de sus bordes
    cuando el rostro está cortado."""

    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class FaceLandmarks:
    """Los cinco puntos con que SFace alinea el rostro."""

    right_eye: Point
    left_eye: Point
    nose_tip: Point
    right_mouth_corner: Point
    left_mouth_corner: Point

    def points(self) -> tuple[Point, ...]:
        return (
            self.right_eye,
            self.left_eye,
            self.nose_tip,
            self.right_mouth_corner,
            self.left_mouth_corner,
        )


@dataclass(frozen=True, slots=True)
class DetectedFace:
    """Un rostro: dónde está, sus cinco puntos y la confianza del detector (0 a 1)."""

    box: BoundingBox
    landmarks: FaceLandmarks
    score: float

    def as_row(self) -> npt.NDArray[np.float32]:
        """La fila de 15 números de YuNet, que es lo que recibe `FaceRecognizerSF`."""
        box = (self.box.x, self.box.y, self.box.width, self.box.height)
        points = [(point.x, point.y) for point in self.landmarks.points()]
        coordinates = [coordinate for point in points for coordinate in point]
        return np.array([*box, *coordinates, self.score], dtype=np.float32)


class FaceDetector:
    """YuNet sobre `cv2.FaceDetectorYN`.

    Una instancia no se comparte entre hilos: la red guarda el tamaño de entrada de la
    última imagen. Cada hilo o proceso crea la suya.
    """

    def __init__(self, model: Path, config: PipelineConfig) -> None:
        self._config = config
        self._network = cv2.FaceDetectorYN.create(
            str(model),
            "",
            INITIAL_INPUT_SIZE,
            config.score_threshold,
            config.nms_threshold,
            config.top_k,
        )

    def detect(self, image: BgrImage) -> list[DetectedFace]:
        """Los rostros de `image`, del de mayor área al de menor; una lista vacía si no
        hay ninguno. Las coordenadas son las de `image`, aunque la red vea otra escala."""
        height, width = image.shape[:2]
        scale = detection_scale(width, height, self._config)
        scaled = resized_by(image, scale)
        self._network.setInputSize((scaled.shape[1], scaled.shape[0]))
        _, rows = self._network.detect(scaled)
        if rows is None:
            return []
        faces = [face_from_row(row, scale) for row in rows]
        return sorted(faces, key=lambda face: (face.box.area, face.score), reverse=True)


def detection_scale(width: int, height: int, config: PipelineConfig) -> float:
    """Por cuánto se multiplica la imagen para que su lado mayor quede entre
    `detection_min_side` y `detection_max_side`; 1 si ya lo está."""
    longer_side = max(width, height)
    if longer_side > config.detection_max_side:
        return config.detection_max_side / longer_side
    if longer_side < config.detection_min_side:
        return config.detection_min_side / longer_side
    return 1.0


def face_from_row(row: npt.NDArray[np.float32], scale: float) -> DetectedFace:
    """El rostro de una fila de YuNet calculada sobre la imagen multiplicada por `scale`,
    con sus coordenadas devueltas a la imagen original."""
    x, y, width, height = (float(value) / scale for value in row[BOX_COLUMNS])
    pairs = np.asarray(row[LANDMARK_COLUMNS], dtype=np.float64).reshape(
        LANDMARK_COUNT, POINT_COORDINATES
    )
    points = [Point(float(x_value) / scale, float(y_value) / scale) for x_value, y_value in pairs]
    return DetectedFace(
        box=BoundingBox(x, y, width, height),
        landmarks=FaceLandmarks(*points),
        score=float(row[SCORE_COLUMN]),
    )


def only_face(faces: Sequence[DetectedFace]) -> DetectedFace:
    """El único rostro de `faces`, para cuando la foto debe ser de una sola persona, como
    al registrarse.

    Raises:
        NoFaceError: si no hay ninguno.
        MultipleFacesError: si hay más de uno.
    """
    if not faces:
        raise NoFaceError("no se encontró ningún rostro en la imagen")
    if len(faces) > 1:
        raise MultipleFacesError(f"la imagen tiene {len(faces)} rostros y debe tener uno")
    return faces[0]
