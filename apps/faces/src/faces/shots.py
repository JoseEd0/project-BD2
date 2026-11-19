"""De los bytes de una foto a una toma aceptada (medidas y embedding) o a un rechazo con su
mensaje; y de una foto a los rostros que se pueden buscar en la galería.

Corre en un hilo con su propio `FacePipeline`: no toca el gestor ni HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass

from .detection import DetectedFace
from .pipeline import FacePipeline
from .quality import (
    QualityConfig,
    Rejection,
    RejectionReason,
    ShotMeasurements,
    first_rejection,
    measure_shot,
    subject_faces,
)
from .recognition import Embedding


@dataclass(frozen=True, slots=True)
class AcceptedShot:
    """Una toma que pasó los controles de calidad, con lo que se midió y su embedding."""

    measurements: ShotMeasurements
    embedding: Embedding


@dataclass(frozen=True, slots=True)
class RejectedShot:
    """Una toma que no pasó: el motivo, y lo que se midió si había un solo rostro."""

    rejection: Rejection
    measurements: ShotMeasurements | None = None


@dataclass(frozen=True, slots=True)
class QueryFace:
    """Un rostro de una foto de consulta y su embedding, o `None` si es muy chico para que
    el embedding sirva."""

    face: DetectedFace
    embedding: Embedding | None


def analyze_shot(
    pipeline: FacePipeline, data: bytes, config: QualityConfig
) -> AcceptedShot | RejectedShot:
    """La toma de `data`, aceptada o con el motivo del rechazo. Solo se calcula el embedding
    de una toma que pasa los controles.

    Raises:
        InvalidImageError: si `data` no es una imagen aceptada o pasa del tope de píxeles.
    """
    image = pipeline.decode(data)
    faces = subject_faces(pipeline.detect(image), config)
    if not faces:
        return RejectedShot(Rejection.of(RejectionReason.NO_FACE))
    if len(faces) > 1:
        return RejectedShot(Rejection.of(RejectionReason.MULTIPLE_FACES))
    aligned = pipeline.align(image, faces[0])
    measurements = measure_shot(image, faces[0], aligned)
    rejection = first_rejection(measurements, config)
    if rejection is not None:
        return RejectedShot(rejection, measurements)
    return AcceptedShot(measurements, pipeline.describe(aligned))


def analyze_query(
    pipeline: FacePipeline, data: bytes, config: QualityConfig, max_faces: int
) -> list[QueryFace]:
    """Los `max_faces` rostros más grandes de `data`, cada uno con su embedding si tiene al
    menos `config.min_face_width` de ancho.

    Raises:
        InvalidImageError: si `data` no es una imagen aceptada o pasa del tope de píxeles.
    """
    image = pipeline.decode(data)
    return [
        QueryFace(
            face,
            pipeline.embed(image, face) if face.box.width >= config.min_face_width else None,
        )
        for face in pipeline.detect(image)[:max_faces]
    ]
