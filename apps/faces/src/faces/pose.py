"""La orientación de la cabeza estimada con los cinco puntos de YuNet, y la pose que cubre una
toma del registro (de frente, girada a la izquierda o a la derecha).

Con cinco puntos no hay una pose exacta: se supone una cara promedio y se proyecta sin
perspectiva. El giro de lado (yaw) desplaza la punta de la nariz, que sobresale del plano de
los ojos, respecto del punto medio entre ellos; el cabeceo (pitch) cambia la altura de la
nariz entre la línea de los ojos y la de la boca; la inclinación (roll) es el ángulo de la
línea de los ojos. Los grados que salen sirven para decidir «de frente» o «girado unos 20°»,
no para medir: lo que se midió de estos estimadores está en el README del servicio.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from .detection import FaceLandmarks, Point

# Cuánto sobresale la punta de la nariz del plano de los ojos, en distancias entre los ojos:
# unos 35 mm frente a unos 63 mm en un adulto promedio.
NOSE_DEPTH_PER_EYE_DISTANCE = 0.55
# Con la cara de frente, la nariz queda a esta fracción del camino de los ojos a la boca: la
# mediana de 3 000 fotos de LFW, casi todas de frente.
FRONTAL_NOSE_DROP = 0.574
# Profundidad de la nariz y de la boca respecto de los ojos, en distancias de los ojos a la
# boca (unos 35 y 20 mm frente a unos 70 mm).
NOSE_DEPTH_PER_EYE_TO_MOUTH = 0.5
MOUTH_DEPTH_PER_EYE_TO_MOUTH = 0.29


class Pose(StrEnum):
    """Hacia dónde mira la persona en una toma, desde su punto de vista: «izquierda» es su
    izquierda, que en una foto sin espejar queda a la derecha de la imagen."""

    FRONT = "front"
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class HeadPose:
    """La orientación estimada, en grados.

    Attributes:
        yaw: giro de lado; positivo si la persona gira hacia su izquierda.
        pitch: cabeceo; positivo si baja la cabeza (mira al suelo).
        roll: inclinación; positivo si inclina la cabeza hacia su hombro izquierdo.
    """

    yaw: float
    pitch: float
    roll: float


@dataclass(frozen=True, slots=True)
class PoseLimits:
    """Qué cuenta como de frente, qué como girado y qué ya es demasiado.

    Attributes:
        frontal_max_yaw: hasta cuántos grados de giro una toma es «de frente».
        turned_min_yaw: desde cuántos grados una toma es «girada». Entre los dos no es ni lo
            uno ni lo otro, y se pide girar más o mirar de frente: así las tomas giradas se
            distinguen de verdad de la de frente.
        max_yaw: el giro máximo; más allá, los puntos de YuNet y el embedding se degradan.
        max_pitch: el cabeceo máximo, hacia arriba o hacia abajo.
        max_roll: la inclinación máxima. La alineación la corrige, pero YuNet subestima la
            de una cabeza muy inclinada (medido: girar la foto 30° se lee como unos 20°), y
            más allá deja de encontrar el rostro.
    """

    frontal_max_yaw: float = 10.0
    turned_min_yaw: float = 15.0
    max_yaw: float = 40.0
    max_pitch: float = 25.0
    max_roll: float = 15.0


def estimate_head_pose(landmarks: FaceLandmarks) -> HeadPose:
    """La orientación de la cabeza de `landmarks`, en grados."""
    eye_line = _difference(landmarks.left_eye, landmarks.right_eye)
    roll = math.atan2(eye_line.y, eye_line.x)
    eyes_middle = _middle(landmarks.right_eye, landmarks.left_eye)
    upright = _UprightFace(eyes_middle, -roll)
    nose = upright.place(landmarks.nose_tip)
    mouth = upright.place(_middle(landmarks.right_mouth_corner, landmarks.left_mouth_corner))
    eye_distance = math.hypot(eye_line.x, eye_line.y)
    pitch = _pitch(nose.y, mouth.y)
    return HeadPose(
        yaw=math.degrees(_yaw(nose.x, eye_distance, pitch)),
        pitch=math.degrees(pitch),
        roll=math.degrees(roll),
    )


def pose_of(head: HeadPose, limits: PoseLimits) -> Pose | None:
    """La pose que cubre una cabeza ya dentro de los límites; `None` si el giro queda entre
    «de frente» y «girada»."""
    turn = abs(head.yaw)
    if turn <= limits.frontal_max_yaw:
        return Pose.FRONT
    if turn < limits.turned_min_yaw:
        return None
    return Pose.LEFT if head.yaw > 0 else Pose.RIGHT


def _pitch(nose_height: float, mouth_height: float) -> float:
    """Cabecear φ lleva un punto (y, z) a y·cos φ + z·sen φ: φ se despeja de la fracción del
    camino de los ojos a la boca en que queda la nariz. `atan2` y no una división: unos
    puntos degenerados dan un ángulo enorme, que se rechaza, y no un error."""
    rise = nose_height - FRONTAL_NOSE_DROP * mouth_height
    depth = NOSE_DEPTH_PER_EYE_TO_MOUTH * mouth_height - MOUTH_DEPTH_PER_EYE_TO_MOUTH * nose_height
    return math.atan2(rise, depth)


def _yaw(nose_offset: float, eye_distance: float, pitch: float) -> float:
    """Con la nariz a profundidad D y los ojos a distancia I, girar θ corre la nariz D·sen θ
    y acerca los ojos a I·cos θ: el corrimiento medido es (D / I)·tan θ. Cabecear cambia la
    profundidad de la nariz (D·cos φ - y·sen φ), así que se usa la de la cabeza ya cabeceada."""
    nose_rise_per_depth = FRONTAL_NOSE_DROP / NOSE_DEPTH_PER_EYE_TO_MOUTH
    depth_after_pitch = math.cos(pitch) - nose_rise_per_depth * math.sin(pitch)
    return math.atan2(nose_offset, eye_distance * NOSE_DEPTH_PER_EYE_DISTANCE * depth_after_pitch)


@dataclass(frozen=True, slots=True)
class _UprightFace:
    """Coordenadas con origen entre los ojos y la línea de los ojos horizontal."""

    origin: Point
    angle: float

    def place(self, point: Point) -> Point:
        relative = _difference(point, self.origin)
        cosine, sine = math.cos(self.angle), math.sin(self.angle)
        return Point(
            relative.x * cosine - relative.y * sine,
            relative.x * sine + relative.y * cosine,
        )


def _difference(point: Point, origin: Point) -> Point:
    return Point(point.x - origin.x, point.y - origin.y)


def _middle(first: Point, second: Point) -> Point:
    return Point((first.x + second.x) / 2, (first.y + second.y) / 2)
