"""Los cuerpos de las peticiones y respuestas del API de rostros."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from faces.consent import ConsentText
from faces.detection import BoundingBox
from faces.pose import Pose
from faces.quality import RejectionReason, ShotMeasurements

# Cabe de sobra un nombre de `DISPLAY_NAME_MAX_LENGTH` con espacios de más, que se recortan;
# lo que pase de aquí ni se lee.
RAW_DISPLAY_NAME_MAX_LENGTH = 200
CONSENT_VERSION_MAX_LENGTH = 64


def timestamp(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


class Consent(BaseModel):
    version: str
    title: str
    purpose: str
    stored_data: list[str]
    not_stored: str
    retention_days: int
    retention: str
    rights: str
    text: str = Field(description="El texto completo que se acepta, con todo lo anterior.")

    @classmethod
    def of(cls, consent: ConsentText) -> Consent:
        return cls(
            version=consent.version,
            title=consent.title,
            purpose=consent.purpose,
            stored_data=list(consent.stored_data),
            not_stored=consent.not_stored,
            retention_days=consent.retention_days,
            retention=consent.retention,
            rights=consent.rights,
            text=consent.text,
        )


class ConsentAcceptance(BaseModel):
    version: str = Field(max_length=CONSENT_VERSION_MAX_LENGTH)
    accepted: bool


class StartEnrollment(BaseModel):
    display_name: str = Field(max_length=RAW_DISPLAY_NAME_MAX_LENGTH)
    consent: ConsentAcceptance | None = Field(
        default=None, description="La versión de GET /consent y `accepted: true`."
    )


class Enrollment(BaseModel):
    """Una sesión de registro: qué poses pide, cuáles tiene y qué hacer ahora."""

    enrollment_id: str
    display_name: str
    poses: list[Pose]
    covered: list[Pose]
    remaining: list[Pose]
    attempts_left: int
    expires_at: datetime
    message: str


class ShotQuality(BaseModel):
    """Lo que se midió de la toma (ver `faces.quality.ShotMeasurements`)."""

    face_width: float
    outside_fraction: float
    brightness: float
    overexposed_fraction: float
    sharpness: float
    yaw: float
    pitch: float
    roll: float

    @classmethod
    def of(cls, measured: ShotMeasurements) -> ShotQuality:
        return cls(
            face_width=measured.face_width,
            outside_fraction=measured.outside_fraction,
            brightness=measured.brightness,
            overexposed_fraction=measured.overexposed_fraction,
            sharpness=measured.sharpness,
            yaw=measured.head.yaw,
            pitch=measured.head.pitch,
            roll=measured.head.roll,
        )


class ShotResult(BaseModel):
    """Una toma aceptada (con la pose que cubrió) o rechazada (con qué corregir)."""

    accepted: bool
    reason: RejectionReason | None
    message: str
    pose: Pose | None
    quality: ShotQuality | None
    enrollment: Enrollment


class Person(BaseModel):
    """Lo que el servicio guarda de una persona, salvo sus embeddings."""

    person_id: str = Field(description="El código para ver o borrar el registro: guárdalo.")
    display_name: str
    consent_version: str
    consented_at: datetime
    created_at: datetime
    expires_at: datetime
    shots: int


class Enrolled(BaseModel):
    person: Person
    message: str


class MatchStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    TOO_SMALL = "too_small"


class FaceBox(BaseModel):
    x: float
    y: float
    width: float
    height: float

    @classmethod
    def of(cls, box: BoundingBox) -> FaceBox:
        return cls(x=box.x, y=box.y, width=box.width, height=box.height)


class IdentifiedFace(BaseModel):
    box: FaceBox
    status: MatchStatus
    display_name: str | None = None
    score: float | None = Field(
        default=None, description="El coseno con la persona más parecida, la reconozca o no."
    )


class Identification(BaseModel):
    faces: list[IdentifiedFace] = Field(description="Del rostro más grande al más chico.")
    message: str
