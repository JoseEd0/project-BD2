"""Reconocer los rostros de un cuadro con la ayuda de los rastros de su sesión.

Por cuadro: decodificar y detectar (en un hilo), leer los rastros de Redis y asignar cada
rostro a uno por IoU; describir y buscar en la galería solo los rostros cuyo rastro no tiene
una identidad vigente; guardar los rastros. Por debajo del umbral de reconocimiento
(`FACES_MATCH_THRESHOLD`) el rostro es «desconocido», como en `POST /identify`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

from faces.api.analysis import AnalysisPool, analyzed
from faces.api.gallery import Gallery
from faces.api.models import FaceBox, MatchStatus
from faces.api.realtime.protocol import (
    TOO_SMALL_LABEL,
    UNKNOWN_LABEL,
    Frame,
    FrameResult,
    StageTimings,
    StreamFace,
)
from faces.api.realtime.tracks import TrackStore
from faces.detection import DetectedFace
from faces.frames import DetectedFrame, describe_faces, detect_frame
from faces.recognition import Embedding
from faces.tracking import Assignment, Identity, TrackBook, TrackingConfig, identified

Clock = Callable[[], float]
MILLISECONDS_PER_SECOND = 1000.0


@dataclass(frozen=True, slots=True)
class FrameLimits:
    max_pixels: int
    max_faces: int
    min_face_width: float


@dataclass(frozen=True, slots=True)
class _Face:
    """Un rostro del cuadro mientras se reconoce: su detección, su rastro (si hay caché) y
    la identidad que ya tenía o la que se le encontró."""

    detected: DetectedFace
    assignment: Assignment | None
    identity: Identity | None
    cached: bool


class FrameRecognizer:
    def __init__(
        self,
        pool: AnalysisPool,
        gallery: Gallery,
        tracks: TrackStore,
        tracking: TrackingConfig,
        limits: FrameLimits,
        clock: Clock,
    ) -> None:
        self._pool = pool
        self._gallery = gallery
        self._tracks = tracks
        self._tracking = tracking
        self._limits = limits
        self._clock = clock

    async def recognize(
        self, frame: Frame, session: str, use_tracks: bool, dropped: int
    ) -> FrameResult:
        """Los rostros del cuadro con sus rastros, identidades y tiempos.

        Raises:
            UnreadableImageError, TooManyPixelsError: si el cuadro no se puede analizar.
        """
        started = time.monotonic()
        work = partial(
            detect_frame,
            data=frame.image,
            max_pixels=self._limits.max_pixels,
            max_faces=self._limits.max_faces,
        )
        detected = await analyzed(self._pool, work)
        now = self._clock()
        track_started = time.perf_counter()
        book = await self._tracks.load(session) if use_tracks else None
        track_ms = _elapsed_ms(track_started)
        faces = self._assigned(detected, book, now)
        embed_ms, described = await self._described(detected, faces)
        search_started = time.perf_counter()
        faces = [await self._searched(face, now) for face in described]
        search_ms = _elapsed_ms(search_started)
        if book is not None:
            save_started = time.perf_counter()
            await self._save(session, book, faces, now)
            track_ms += _elapsed_ms(save_started)
        timings = StageTimings(
            wait=(started - frame.received_at) * MILLISECONDS_PER_SECOND,
            decode=detected.decode_ms,
            detect=detected.detect_ms,
            track=track_ms,
            embed=embed_ms,
            search=search_ms,
            total=(time.monotonic() - frame.received_at) * MILLISECONDS_PER_SECOND,
        )
        return FrameResult(
            frame=frame.number,
            faces=[self._view(face) for face in faces],
            dropped=dropped,
            timings=timings,
        )

    def _assigned(self, detected: DetectedFrame, book: TrackBook | None, now: float) -> list[_Face]:
        if book is None:
            return [_Face(face, None, None, False) for face in detected.faces]
        boxes = [face.box for face in detected.faces]
        assignments = book.assign(boxes, now, self._tracking)
        return [
            _Face(face, assignment, assignment.identity, assignment.identity is not None)
            for face, assignment in zip(detected.faces, assignments, strict=True)
        ]

    async def _described(
        self, detected: DetectedFrame, faces: list[_Face]
    ) -> tuple[float, list[tuple[_Face, Embedding | None]]]:
        """Los embeddings de los rostros sin identidad y bastante grandes para buscarlos."""
        pending = [index for index, face in enumerate(faces) if self._needs_search(face)]
        if not pending:
            return 0.0, [(face, None) for face in faces]
        work = partial(
            describe_faces,
            image=detected.image,
            faces=[faces[index].detected for index in pending],
        )
        described = await self._pool.run(work)
        embeddings: dict[int, Embedding] = dict(zip(pending, described.embeddings, strict=True))
        return described.embed_ms, [(face, embeddings.get(i)) for i, face in enumerate(faces)]

    def _needs_search(self, face: _Face) -> bool:
        return face.identity is None and face.detected.box.width >= self._limits.min_face_width

    async def _searched(self, described: tuple[_Face, Embedding | None], now: float) -> _Face:
        face, embedding = described
        if embedding is None:
            return face
        lookup = await self._gallery.identify(embedding, int(now))
        match = lookup.match
        identity = Identity(
            display_name=None if match is None else match.display_name,
            score=lookup.best_score if match is None else match.score,
            verified_at=now,
        )
        return _Face(face.detected, face.assignment, identity, cached=False)

    async def _save(
        self, session: str, book: TrackBook, faces: Sequence[_Face], now: float
    ) -> None:
        assignments = [
            face.assignment if face.identity is None else identified(face.assignment, face.identity)
            for face in faces
            if face.assignment is not None
        ]
        await self._tracks.save(session, book.updated(assignments, now, self._tracking))

    def _view(self, face: _Face) -> StreamFace:
        box = FaceBox.of(face.detected.box)
        track = None if face.assignment is None else face.assignment.track_id
        identity = face.identity
        if identity is None:
            return StreamFace(
                track=track,
                box=box,
                status=MatchStatus.TOO_SMALL,
                label=TOO_SMALL_LABEL,
                cached=False,
            )
        if identity.display_name is None:
            return StreamFace(
                track=track,
                box=box,
                status=MatchStatus.UNKNOWN,
                label=UNKNOWN_LABEL,
                score=identity.score,
                cached=face.cached,
            )
        return StreamFace(
            track=track,
            box=box,
            status=MatchStatus.KNOWN,
            label=identity.display_name,
            display_name=identity.display_name,
            score=identity.score,
            cached=face.cached,
        )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
