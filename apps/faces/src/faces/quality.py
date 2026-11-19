"""Controles de calidad de una toma del registro: medidas de la foto y del rostro, y el primer
control que no pasa, con el mensaje que dice qué corregir.

Medir (`measure_shot`) y decidir (`first_rejection`) están separados: las medidas son números
que se guardan junto al embedding y se devuelven a la interfaz, y cada control es una función
pura de esos números y de un umbral de `QualityConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import cv2
import numpy as np

from .detection import BoundingBox, DetectedFace
from .image import BgrImage
from .pose import HeadPose, PoseLimits, estimate_head_pose

# La parte del recorte alineado de 112 x 112 que es cara y no fondo: en la plantilla de SFace
# los ojos quedan a 0,46 del alto y la boca a 0,82, entre 0,22 y 0,80 del ancho.
FACE_REGION_ROWS = (0.25, 0.95)
FACE_REGION_COLUMNS = (0.2, 0.8)
# Un píxel en gris desde este nivel ya perdió el detalle de la piel: está quemado.
OVEREXPOSED_LEVEL = 250


class RejectionReason(StrEnum):
    """Por qué no se acepta una toma. El valor es el código que recibe la interfaz."""

    NO_FACE = "no_face"
    MULTIPLE_FACES = "multiple_faces"
    FACE_CUT_OFF = "face_cut_off"
    FACE_TOO_SMALL = "face_too_small"
    TOO_DARK = "too_dark"
    OVEREXPOSED = "overexposed"
    BLURRY = "blurry"
    HEAD_TILTED = "head_tilted"
    TURNED_TOO_FAR = "turned_too_far"
    LOOKING_UP = "looking_up"
    LOOKING_DOWN = "looking_down"
    POSE_UNCLEAR = "pose_unclear"
    POSE_ALREADY_TAKEN = "pose_already_taken"
    POSE_NOT_IN_PLAN = "pose_not_in_plan"
    DIFFERENT_PERSON = "different_person"


REJECTION_MESSAGES: dict[RejectionReason, str] = {
    RejectionReason.NO_FACE: (
        "No encontramos ningún rostro. Mira a la cámara con la cara completa a la vista y "
        "con buena luz."
    ),
    RejectionReason.MULTIPLE_FACES: (
        "Aparece más de un rostro. Para registrarte, que en la foto salga solo tu cara."
    ),
    RejectionReason.FACE_CUT_OFF: (
        "Tu cara se sale del cuadro. Aléjate un poco y céntrala en la pantalla."
    ),
    RejectionReason.FACE_TOO_SMALL: (
        "Tu cara se ve muy pequeña. Acércate a la cámara hasta que ocupe buena parte de la "
        "pantalla."
    ),
    RejectionReason.TOO_DARK: (
        "Hay poca luz sobre tu cara. Busca un lugar más iluminado o ponte de frente a una "
        "ventana o a una lámpara."
    ),
    RejectionReason.OVEREXPOSED: (
        "Hay demasiada luz sobre tu cara. Evita el sol directo, el flash o una lámpara muy cerca."
    ),
    RejectionReason.BLURRY: (
        "La foto salió borrosa. Quédate quieto al tomarla y limpia el lente de la cámara."
    ),
    RejectionReason.HEAD_TILTED: (
        "Tienes la cabeza inclinada hacia un hombro. Endereza la cabeza."
    ),
    RejectionReason.TURNED_TOO_FAR: (
        "Giraste demasiado la cabeza. Basta con un giro leve: gira menos."
    ),
    RejectionReason.LOOKING_UP: "Estás mirando hacia arriba. Baja un poco la barbilla.",
    RejectionReason.LOOKING_DOWN: "Estás mirando hacia abajo. Levanta un poco la barbilla.",
    RejectionReason.POSE_UNCLEAR: "No queda claro si miras de frente o de lado.",
    RejectionReason.POSE_ALREADY_TAKEN: "Esa toma ya la tenemos.",
    RejectionReason.POSE_NOT_IN_PLAN: "Esa pose no es una de las que pedimos.",
    RejectionReason.DIFFERENT_PERSON: (
        "Esta toma parece de otra persona. Todas las tomas tienen que ser de quien se "
        "registra; si eres tú, cancela y empieza de nuevo."
    ),
}


@dataclass(frozen=True, slots=True)
class Rejection:
    """Una toma rechazada: el código y el mensaje para la persona."""

    reason: RejectionReason
    message: str

    @classmethod
    def of(cls, reason: RejectionReason, guidance: str = "") -> Rejection:
        """El rechazo con el mensaje de `reason`, seguido de `guidance` si lo hay."""
        message = REJECTION_MESSAGES[reason]
        return cls(reason, f"{message} {guidance}" if guidance else message)


@dataclass(frozen=True, slots=True)
class QualityConfig:
    """Los umbrales de cada control. Cómo se eligieron está en el README del servicio.

    Attributes:
        min_face_width: ancho mínimo de la caja del rostro, en píxeles de la foto. SFace ve
            el rostro a 112 x 112: uno más chico se amplía y pierde el detalle que lo
            distingue.
        max_outside_fraction: fracción de la caja del rostro que puede quedar fuera de la
            foto. YuNet ajusta la caja de una cara cortada al borde y rara vez la deja
            salir, así que cualquier salida que no sea redondeo es una cara cortada (medido:
            cortar un 20 % de la cara por abajo deja un 8 % de la caja afuera).
        background_face_ratio: un rostro más angosto que esta fracción del más grande es
            alguien al fondo y no cuenta como «otra persona en la foto».
        min_brightness, max_brightness: el gris medio de la cara, de 0 a 255.
        max_overexposed_fraction: fracción de la cara que puede estar quemada.
        min_sharpness: varianza del laplaciano de la cara alineada; baja si está movida o
            desenfocada.
        pose: los límites de la orientación de la cabeza.
    """

    min_face_width: float = 80.0
    max_outside_fraction: float = 0.02
    background_face_ratio: float = 0.5
    min_brightness: float = 50.0
    max_brightness: float = 200.0
    max_overexposed_fraction: float = 0.15
    min_sharpness: float = 40.0
    pose: PoseLimits = field(default_factory=PoseLimits)


@dataclass(frozen=True, slots=True)
class ShotMeasurements:
    """Lo que se midió de una toma.

    Attributes:
        face_width: ancho de la caja del rostro, en píxeles de la foto.
        outside_fraction: fracción de la caja que queda fuera de la foto.
        brightness: gris medio de la cara alineada (0 a 255).
        overexposed_fraction: fracción de la cara alineada quemada.
        sharpness: varianza del laplaciano de la cara alineada.
        head: la orientación estimada de la cabeza.
    """

    face_width: float
    outside_fraction: float
    brightness: float
    overexposed_fraction: float
    sharpness: float
    head: HeadPose


def subject_faces(faces: list[DetectedFace], config: QualityConfig) -> list[DetectedFace]:
    """Los rostros de `faces` (del más grande al más chico) que no son alguien al fondo: los
    de ancho de al menos `background_face_ratio` del más grande."""
    if not faces:
        return []
    least_width = faces[0].box.width * config.background_face_ratio
    return [face for face in faces if face.box.width >= least_width]


def measure_shot(image: BgrImage, face: DetectedFace, aligned: BgrImage) -> ShotMeasurements:
    """Las medidas de `face`, detectado en `image`, con su recorte alineado `aligned`."""
    height, width = image.shape[:2]
    region = face_region(aligned)
    return ShotMeasurements(
        face_width=face.box.width,
        outside_fraction=outside_fraction(face.box, width, height),
        brightness=float(region.mean()),
        overexposed_fraction=float(np.mean(region >= OVEREXPOSED_LEVEL)),
        sharpness=laplacian_variance(region),
        head=estimate_head_pose(face.landmarks),
    )


def face_region(aligned: BgrImage) -> np.ndarray:
    """La cara del recorte alineado, en gris, sin el fondo de las esquinas."""
    gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    rows, columns = gray.shape
    top, bottom = (round(rows * fraction) for fraction in FACE_REGION_ROWS)
    left, right = (round(columns * fraction) for fraction in FACE_REGION_COLUMNS)
    return np.asarray(gray[top:bottom, left:right])


def laplacian_variance(gray: np.ndarray) -> float:
    """La varianza del laplaciano: cuánta energía de bordes finos tiene la imagen. Una foto
    movida o desenfocada pierde esos bordes y su varianza cae."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def outside_fraction(box: BoundingBox, width: int, height: int) -> float:
    """La fracción del área de `box` que queda fuera de una imagen de `width` x `height`."""
    if box.area <= 0:
        return 1.0
    inside_width = max(0.0, min(box.x + box.width, width) - max(box.x, 0.0))
    inside_height = max(0.0, min(box.y + box.height, height) - max(box.y, 0.0))
    return 1.0 - inside_width * inside_height / box.area


def first_rejection(measured: ShotMeasurements, config: QualityConfig) -> Rejection | None:
    """El primer control que no pasa, o `None` si la toma es buena.

    El orden importa: primero lo que se corrige con el entorno (encuadre, distancia, luz,
    nitidez) y después la pose. Una foto oscura también pierde bordes, así que la luz va
    antes que la nitidez; y en una oscura o borrosa los puntos de la pose no son fiables.
    """
    for check in (
        framing_rejection,
        size_rejection,
        lighting_rejection,
        sharpness_rejection,
        pose_rejection,
    ):
        rejection = check(measured, config)
        if rejection is not None:
            return rejection
    return None


def framing_rejection(measured: ShotMeasurements, config: QualityConfig) -> Rejection | None:
    if measured.outside_fraction > config.max_outside_fraction:
        return Rejection.of(RejectionReason.FACE_CUT_OFF)
    return None


def size_rejection(measured: ShotMeasurements, config: QualityConfig) -> Rejection | None:
    if measured.face_width < config.min_face_width:
        return Rejection.of(RejectionReason.FACE_TOO_SMALL)
    return None


def lighting_rejection(measured: ShotMeasurements, config: QualityConfig) -> Rejection | None:
    if measured.brightness < config.min_brightness:
        return Rejection.of(RejectionReason.TOO_DARK)
    too_bright = measured.brightness > config.max_brightness
    if too_bright or measured.overexposed_fraction > config.max_overexposed_fraction:
        return Rejection.of(RejectionReason.OVEREXPOSED)
    return None


def sharpness_rejection(measured: ShotMeasurements, config: QualityConfig) -> Rejection | None:
    if measured.sharpness < config.min_sharpness:
        return Rejection.of(RejectionReason.BLURRY)
    return None


def pose_rejection(measured: ShotMeasurements, config: QualityConfig) -> Rejection | None:
    head, limits = measured.head, config.pose
    if abs(head.roll) > limits.max_roll:
        return Rejection.of(RejectionReason.HEAD_TILTED)
    if abs(head.yaw) > limits.max_yaw:
        return Rejection.of(RejectionReason.TURNED_TOO_FAR)
    if head.pitch > limits.max_pitch:
        return Rejection.of(RejectionReason.LOOKING_DOWN)
    if head.pitch < -limits.max_pitch:
        return Rejection.of(RejectionReason.LOOKING_UP)
    return None
