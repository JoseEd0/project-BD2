"""Seguir rostros entre los cuadros de un video por el solapamiento (IoU) de sus cajas.

Un rastro es un rostro que se vio en cuadros seguidos: su caja más reciente y, si ya se
buscó en la galería, quién es. Mientras esa identidad no caduque, el rostro del rastro no se
vuelve a describir ni a buscar: es lo que ahorra el seguimiento. Las funciones son puras;
dónde se guardan los rastros lo decide quien las usa.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from .detection import BoundingBox

FIRST_TRACK_ID = 1


@dataclass(frozen=True, slots=True)
class TrackingConfig:
    """Cómo se siguen los rostros.

    Attributes:
        min_iou: solapamiento mínimo entre la caja de un rastro y la de un rostro nuevo para
            que sean el mismo. A 10 cuadros por segundo un rostro se mueve poco entre dos
            cuadros; 0,3 tolera un giro rápido de la cámara sin juntar a dos personas
            vecinas, cuyas cajas casi no se tocan.
        verify_seconds: cuánto vale la identidad de un rastro antes de volver a buscarla.
        lost_seconds: cuánto se conserva un rastro que dejó de verse, por si el detector lo
            pierde un cuadro.
    """

    min_iou: float = 0.3
    verify_seconds: float = 2.0
    lost_seconds: float = 1.0


@dataclass(frozen=True, slots=True)
class Identity:
    """Lo que dijo la galería de un rastro: su nombre, o `None` si es un desconocido, y el
    mejor coseno visto."""

    display_name: str | None
    score: float | None
    verified_at: float


@dataclass(frozen=True, slots=True)
class Track:
    track_id: int
    box: BoundingBox
    last_seen: float
    identity: Identity | None = None


@dataclass(frozen=True, slots=True)
class Assignment:
    """El rastro de un rostro del cuadro y la identidad que todavía vale, si la tiene."""

    track_id: int
    box: BoundingBox
    identity: Identity | None


@dataclass(frozen=True, slots=True)
class TrackBook:
    """Los rastros de una sesión y el número que tendrá el próximo."""

    tracks: tuple[Track, ...] = ()
    next_id: int = FIRST_TRACK_ID

    def assign(
        self, boxes: Sequence[BoundingBox], now: float, config: TrackingConfig
    ) -> list[Assignment]:
        """Un rastro para cada caja, en su orden: el que más se le solapa, o uno nuevo. Dos
        cajas nunca comparten rastro."""
        matched = match_boxes([track.box for track in self.tracks], boxes, config.min_iou)
        next_id = self.next_id
        assignments = []
        for box, track_index in zip(boxes, matched, strict=True):
            if track_index is None:
                assignments.append(Assignment(next_id, box, None))
                next_id += 1
                continue
            track = self.tracks[track_index]
            assignments.append(Assignment(track.track_id, box, _fresh(track, now, config)))
        return assignments

    def updated(
        self, assignments: Sequence[Assignment], now: float, config: TrackingConfig
    ) -> TrackBook:
        """Los rastros después del cuadro: los de `assignments` con su caja y su identidad,
        y los que no se vieron pero se perdieron hace menos de `lost_seconds`."""
        seen = {assignment.track_id for assignment in assignments}
        kept = [
            track
            for track in self.tracks
            if track.track_id not in seen and now - track.last_seen < config.lost_seconds
        ]
        current = [
            Track(assignment.track_id, assignment.box, now, assignment.identity)
            for assignment in assignments
        ]
        highest = max((assignment.track_id for assignment in assignments), default=0)
        return TrackBook(tuple(current + kept), max(self.next_id, highest + 1))


def identified(assignment: Assignment, identity: Identity) -> Assignment:
    return replace(assignment, identity=identity)


def match_boxes(
    previous: Sequence[BoundingBox], current: Sequence[BoundingBox], min_iou: float
) -> list[int | None]:
    """Para cada caja de `current`, el índice de la de `previous` que es el mismo rostro, o
    `None`. Voraz por IoU de mayor a menor, uno a uno: con pocos rostros por cuadro da lo
    mismo que el emparejamiento óptimo y cuesta nada."""
    pairs = sorted(
        (
            (overlap, current_index, previous_index)
            for current_index, box in enumerate(current)
            for previous_index, earlier in enumerate(previous)
            if (overlap := iou(box, earlier)) >= min_iou
        ),
        reverse=True,
    )
    matched: list[int | None] = [None] * len(current)
    taken: set[int] = set()
    for _, current_index, previous_index in pairs:
        if matched[current_index] is None and previous_index not in taken:
            matched[current_index] = previous_index
            taken.add(previous_index)
    return matched


def iou(first: BoundingBox, second: BoundingBox) -> float:
    """Área de la intersección sobre área de la unión: 1 si son la misma caja, 0 si no se
    tocan."""
    width = min(first.x + first.width, second.x + second.width) - max(first.x, second.x)
    height = min(first.y + first.height, second.y + second.height) - max(first.y, second.y)
    if width <= 0 or height <= 0:
        return 0.0
    intersection = width * height
    union = first.area + second.area - intersection
    return intersection / union if union > 0 else 0.0


def _fresh(track: Track, now: float, config: TrackingConfig) -> Identity | None:
    identity = track.identity
    if identity is None or now - identity.verified_at >= config.verify_seconds:
        return None
    return identity
