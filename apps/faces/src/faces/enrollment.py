"""Las reglas del registro que no dependen de HTTP ni del gestor: el plan de poses, la guía
para la siguiente toma, y cuándo un rostro ya está registrado o se parece demasiado a alguien.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .pose import Pose
from .quality import Rejection, RejectionReason

DISPLAY_NAME_MAX_LENGTH = 60
# Categorías de Unicode que no se ven o mueven el texto: control, formato (incluidos los
# caracteres que invierten la dirección), sin asignar y de uso privado.
INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cn", "Co", "Cs"})

POSE_GUIDANCE: dict[Pose, str] = {
    Pose.FRONT: "Mira directo a la cámara.",
    Pose.LEFT: "Gira un poco la cabeza hacia tu izquierda.",
    Pose.RIGHT: "Gira un poco la cabeza hacia tu derecha.",
}
POSE_NAMES: dict[Pose, str] = {
    Pose.FRONT: "de frente",
    Pose.LEFT: "girada a tu izquierda",
    Pose.RIGHT: "girada a tu derecha",
}
COMPLETE_GUIDANCE = "Ya tenemos todas las tomas: termina el registro."


class InvalidDisplayNameError(ValueError):
    """El nombre está vacío, es muy largo o tiene caracteres que no se ven."""


@dataclass(frozen=True, slots=True)
class PosePlan:
    """Las poses que necesita un registro, en el orden en que se piden. Una pose puede
    repetirse: `(FRONT, FRONT)` pide dos tomas de frente."""

    poses: tuple[Pose, ...]

    def remaining(self, covered: Iterable[Pose]) -> tuple[Pose, ...]:
        """Las poses del plan que aún faltan, en su orden."""
        pending = list(self.poses)
        for pose in covered:
            if pose in pending:
                pending.remove(pose)
        return tuple(pending)

    def is_complete(self, covered: Iterable[Pose]) -> bool:
        return not self.remaining(covered)

    def guidance(self, covered: Iterable[Pose]) -> str:
        """Qué hacer en la siguiente toma."""
        remaining = self.remaining(covered)
        return POSE_GUIDANCE[remaining[0]] if remaining else COMPLETE_GUIDANCE

    def placement_rejection(self, pose: Pose | None, covered: Sequence[Pose]) -> Rejection | None:
        """Por qué una toma de `pose` no sirve al plan, o `None` si cubre una que falta.
        `pose` es `None` cuando el giro quedó entre «de frente» y «girada»."""
        guidance = self.guidance(covered)
        if pose is None:
            return Rejection.of(RejectionReason.POSE_UNCLEAR, guidance)
        if pose not in self.poses:
            return Rejection.of(RejectionReason.POSE_NOT_IN_PLAN, guidance)
        if pose not in self.remaining(covered):
            return Rejection.of(RejectionReason.POSE_ALREADY_TAKEN, guidance)
        return None

    def accepted_message(self, covered: Sequence[Pose]) -> str:
        """El mensaje tras aceptar la última toma de `covered`."""
        return f"Toma {POSE_NAMES[covered[-1]]} lista. {self.guidance(covered)}"


def normalized_display_name(raw: str) -> str:
    """El nombre en NFC, sin espacios de más.

    Raises:
        InvalidDisplayNameError: si queda vacío, pasa de `DISPLAY_NAME_MAX_LENGTH` o tiene
            caracteres de control, de formato o sin asignar.
    """
    name = " ".join(unicodedata.normalize("NFC", raw).split())
    if not name:
        raise InvalidDisplayNameError("Escribe tu nombre para registrarte.")
    if len(name) > DISPLAY_NAME_MAX_LENGTH:
        raise InvalidDisplayNameError(
            f"El nombre puede tener hasta {DISPLAY_NAME_MAX_LENGTH} caracteres."
        )
    if any(unicodedata.category(character) in INVISIBLE_CATEGORIES for character in name):
        raise InvalidDisplayNameError("El nombre tiene caracteres que no se pueden mostrar.")
    return name


@dataclass(frozen=True, slots=True)
class Neighbour:
    """Un embedding de la galería cercano a la consulta: de quién es y su coseno."""

    person_id: str
    score: float


@dataclass(frozen=True, slots=True)
class Candidate:
    person_id: str
    score: float


def best_per_person(neighbours: Iterable[Neighbour]) -> list[Candidate]:
    """Cada persona con el mejor coseno de sus embeddings, de la más parecida a la menos."""
    best: dict[str, float] = {}
    for neighbour in neighbours:
        best[neighbour.person_id] = max(best.get(neighbour.person_id, -1.0), neighbour.score)
    ranked = sorted(best.items(), key=lambda item: (-item[1], item[0]))
    return [Candidate(person_id, score) for person_id, score in ranked]


def resemblance_per_person(shots: Sequence[Sequence[Neighbour]]) -> list[Candidate]:
    """Cuánto se parece cada persona de la galería a las tomas de un registro: el promedio,
    sobre las tomas, de su mejor coseno con cada una.

    Una persona que no salió entre los vecinos de una toma cuenta, para esa toma, con el
    coseno del último vecino, que es una cota superior del suyo: se sobrestima el parecido,
    nunca se subestima. Promediar exige que todas las tomas se parezcan, y no solo la mejor.
    """
    per_shot = [{c.person_id: c.score for c in best_per_person(shot)} for shot in shots]
    people = {person for shot in per_shot for person in shot}
    floors = [min(shot.values(), default=0.0) for shot in per_shot]
    averaged = [
        Candidate(
            person,
            sum(shot.get(person, floor) for shot, floor in zip(per_shot, floors, strict=True))
            / len(per_shot),
        )
        for person in people
    ]
    return sorted(averaged, key=lambda candidate: (-candidate.score, candidate.person_id))


class Resemblance(StrEnum):
    """Qué tan parecido es un registro nuevo a alguien que ya está."""

    SAME_PERSON = "same_person"
    LOOKALIKE = "lookalike"


@dataclass(frozen=True, slots=True)
class IdentityThresholds:
    """Los umbrales de coseno del servicio. Cómo se eligieron está en su README.

    Attributes:
        session: de una toma con las anteriores del mismo registro (1:1). Es el umbral de
            verificación que publica OpenCV para SFace.
        match: para reconocer a alguien de la galería (1:N); por debajo, «desconocido».
        duplicate: desde aquí un registro nuevo es alguien que ya está, y se dice su nombre.
        lookalike: desde aquí, y por debajo de `duplicate`, se parece demasiado a alguien: no
            se registra, pero no se dice a quién. Igual a `duplicate` desactiva el aviso.
    """

    session: float = 0.363
    match: float = 0.45
    duplicate: float = 0.5
    lookalike: float = 0.45


def resemblance_of(score: float, thresholds: IdentityThresholds) -> Resemblance | None:
    if score >= thresholds.duplicate:
        return Resemblance.SAME_PERSON
    if score >= thresholds.lookalike:
        return Resemblance.LOOKALIKE
    return None
