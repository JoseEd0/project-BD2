"""Reconocer a las personas de una foto contra la galería."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from faces.api.analysis import AnalysisPool, analyzed
from faces.api.gallery import Gallery
from faces.api.models import FaceBox, Identification, IdentifiedFace, MatchStatus
from faces.quality import REJECTION_MESSAGES, QualityConfig, RejectionReason
from faces.shots import QueryFace, analyze_query

Clock = Callable[[], float]
NO_FACE_MESSAGE = REJECTION_MESSAGES[RejectionReason.NO_FACE]
TOO_SMALL_MESSAGE = (
    "Los rostros se ven muy pequeños para reconocerlos. Acércate o acerca la cámara."
)


class IdentificationService:
    """Cada rostro de una foto, del más grande al más chico, con la persona registrada que
    es o «desconocido»."""

    def __init__(
        self,
        gallery: Gallery,
        pool: AnalysisPool,
        quality: QualityConfig,
        max_faces: int,
        clock: Clock,
    ) -> None:
        self._gallery = gallery
        self._pool = pool
        self._quality = quality
        self._max_faces = max_faces
        self._clock = clock

    async def identify(self, photo: bytes) -> Identification:
        """Raises: UnreadableImageError, TooManyPixelsError si la foto no se puede analizar."""
        work = partial(analyze_query, data=photo, config=self._quality, max_faces=self._max_faces)
        faces = [await self._identified(face) for face in await analyzed(self._pool, work)]
        return Identification(faces=faces, message=_summary(faces))

    async def _identified(self, query: QueryFace) -> IdentifiedFace:
        box = FaceBox.of(query.face.box)
        if query.embedding is None:
            return IdentifiedFace(box=box, status=MatchStatus.TOO_SMALL)
        lookup = await self._gallery.identify(query.embedding, int(self._clock()))
        if lookup.match is None:
            return IdentifiedFace(box=box, status=MatchStatus.UNKNOWN, score=lookup.best_score)
        return IdentifiedFace(
            box=box,
            status=MatchStatus.KNOWN,
            display_name=lookup.match.display_name,
            score=lookup.match.score,
        )


def _summary(faces: list[IdentifiedFace]) -> str:
    if not faces:
        return NO_FACE_MESSAGE
    if all(face.status is MatchStatus.TOO_SMALL for face in faces):
        return TOO_SMALL_MESSAGE
    names = [face.display_name for face in faces if face.display_name is not None]
    if not names:
        return "No reconocemos a nadie en la foto."
    return "Reconocemos a " + ", ".join(names) + "."
