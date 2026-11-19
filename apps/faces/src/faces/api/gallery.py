"""La galería de personas registradas: a quién se parece un rostro, para reconocerlo o para
no registrarlo dos veces."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from appkit import timed
from faces.api.store import FaceStore
from faces.enrollment import (
    Candidate,
    IdentityThresholds,
    Resemblance,
    best_per_person,
    resemblance_of,
    resemblance_per_person,
)
from faces.recognition import Embedding

GALLERY_STAGE = "galeria"


@dataclass(frozen=True, slots=True)
class Match:
    """La persona reconocida y su coseno."""

    person_id: str
    display_name: str
    score: float


@dataclass(frozen=True, slots=True)
class Lookup:
    """El resultado de buscar un rostro: la persona reconocida, si alguna pasa el umbral, y
    el mejor coseno visto, la reconozca o no."""

    match: Match | None
    best_score: float | None


@dataclass(frozen=True, slots=True)
class Duplicate:
    """Alguien registrado a quien se parece un registro nuevo."""

    kind: Resemblance
    display_name: str
    score: float


class Gallery:
    """Busca en el HNSW de `face_embeddings` y resuelve las personas vigentes."""

    def __init__(self, store: FaceStore, thresholds: IdentityThresholds, neighbours: int) -> None:
        self._store = store
        self._thresholds = thresholds
        self._neighbours = neighbours

    async def identify(self, embedding: Embedding, now: int) -> Lookup:
        """La persona vigente más parecida con coseno de al menos el umbral de
        reconocimiento. Una persona vencida que el borrado periódico aún no alcanzó no se
        reconoce: se pasa a la siguiente."""
        with timed(GALLERY_STAGE):
            return await self._identify(embedding, now)

    async def duplicate_of(self, embeddings: Sequence[Embedding], now: int) -> Duplicate | None:
        """La persona vigente a quien más se parecen las tomas de un registro nuevo, si el
        parecido pasa el umbral de duplicado o el de «se parece demasiado»."""
        with timed(GALLERY_STAGE):
            return await self._duplicate_of(embeddings, now)

    async def _identify(self, embedding: Embedding, now: int) -> Lookup:
        candidates = best_per_person(await self._store.nearest(embedding, self._neighbours))
        best_score = candidates[0].score if candidates else None
        for candidate in candidates:
            if candidate.score < self._thresholds.match:
                break
            name = await self._store.person_name(candidate.person_id, now)
            if name is not None:
                return Lookup(Match(candidate.person_id, name, candidate.score), best_score)
        return Lookup(None, best_score)

    async def _duplicate_of(self, embeddings: Sequence[Embedding], now: int) -> Duplicate | None:
        shots = [await self._store.nearest(embedding, self._neighbours) for embedding in embeddings]
        for candidate in resemblance_per_person(shots):
            duplicate = await self._duplicate(candidate, now)
            if duplicate is not None or candidate.score < self._thresholds.lookalike:
                return duplicate
        return None

    async def _duplicate(self, candidate: Candidate, now: int) -> Duplicate | None:
        kind = resemblance_of(candidate.score, self._thresholds)
        if kind is None:
            return None
        name = await self._store.person_name(candidate.person_id, now)
        return None if name is None else Duplicate(kind, name, candidate.score)
