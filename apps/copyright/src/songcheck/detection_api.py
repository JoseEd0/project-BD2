"""`POST /detections`: qué canciones del catálogo tiene dentro un audio subido.

1. el audio se reconoce por su contenido y se comprueban tamaño y duración (`upload`);
2. se decodifica por bloques para saber qué ventanas están en silencio (`loudness`);
3. el gestor lo corta en las ventanas del catálogo (`windows.WINDOWING`) y busca los vecinos
   de todas en un lote (`POST /media/segments`);
4. la votación por desfase arma las coincidencias (`detection`) y se puntúan con las ventanas
   de cada canción, que salen de su duración en `cr_canciones`.

Cada etapa sale en `Server-Timing`, también las tres del gestor (`decode`, `extraction` y
`search`), que el gestor mide sobre todas las ventanas a la vez.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from pydantic import BaseModel

from appkit import Engine, record_stage, timed
from minigestor_client import AsyncClient, MediaFile, Record, SegmentSearch
from songcheck.detection import (
    Match,
    Neighbour,
    QueryWindow,
    VotingParameters,
    aligned_runs,
    score_runs,
)
from songcheck.loudness import Loudness
from songcheck.schema import (
    SEGMENT_END_COLUMN,
    SEGMENT_SONG_COLUMN,
    SEGMENT_START_COLUMN,
    SEGMENTS_TABLE,
    SONGS_TABLE,
)
from songcheck.settings import CopyrightSettings
from songcheck.upload import ProbedAudio, UploadLimits, probe_audio
from songcheck.windows import MIN_SEGMENT_SECONDS, WINDOWING, segment_windows

DETECTIONS_PATH = "/detections"
UPLOAD_NAME = "subida"
PROBE_STAGE = "probe"
LOUDNESS_STAGE = "loudness"
SEGMENTS_STAGE = "segments"
ENGINE_DECODE_STAGE = "decode"
ENGINE_EXTRACTION_STAGE = "extraction"
ENGINE_SEARCH_STAGE = "search"
VOTING_STAGE = "voting"
SONGS_STAGE = "songs"
SONGS_BY_ID = "SELECT id, titulo, artista, duracion FROM {table} WHERE id IN ({placeholders})"


@dataclass(frozen=True, slots=True)
class DetectionSettings:
    """Lo que la ruta necesita de la configuración, armado al crear la aplicación."""

    limits: UploadLimits
    voting: VotingParameters
    neighbours_per_window: int
    threshold: float
    max_matches: int
    silence_threshold_dbfs: float

    @classmethod
    def of(cls, settings: CopyrightSettings) -> DetectionSettings:
        return cls(
            limits=UploadLimits(
                max_bytes=settings.max_upload_bytes,
                min_seconds=MIN_SEGMENT_SECONDS,
                max_seconds=settings.max_upload_seconds,
            ),
            voting=VotingParameters(WINDOWING.hop_seconds, settings.min_run_windows),
            neighbours_per_window=settings.neighbours_per_window,
            threshold=settings.match_threshold,
            max_matches=settings.max_matches,
            silence_threshold_dbfs=settings.silence_threshold_dbfs,
        )


class SongReference(BaseModel):
    id: int
    title: str
    artist: str


class TimeSpan(BaseModel):
    start_seconds: float
    end_seconds: float


class DetectedMatch(BaseModel):
    """Una racha de ventanas de la subida alineada con una canción del catálogo.

    `coverage` es la fracción de las ventanas que la subida y el original podrían compartir
    (las audibles de la subida o las del original, las que sean menos) que cubre la racha, y
    no entra en el puntaje; `score` es la evidencia de la racha, de 0 a 1: sus ventanas hasta
    las de una copia de 20 s por su similitud media sobre 0,85 (`detection.run_score`).
    """

    song: SongReference
    upload: TimeSpan
    original: TimeSpan
    offset_seconds: float
    windows: int
    mean_similarity: float
    coverage: float
    score: float
    above_threshold: bool


class EngineTimings(BaseModel):
    """Lo que tardó el gestor, sobre todas las ventanas a la vez."""

    decode_ms: float
    extraction_ms: float
    search_ms: float


class Detection(BaseModel):
    """Lo que se encontró en la subida. `detected` dice si alguna coincidencia pasa el umbral."""

    format: str
    duration_seconds: float
    sample_rate: int
    channels: int
    window_seconds: float
    hop_seconds: float
    windows: int
    silent_windows: int
    threshold: float
    detected: bool
    matches: list[DetectedMatch]
    engine_timings: EngineTimings | None


AudioUpload = Annotated[UploadFile, File(description="El audio: MP3, WAV, FLAC u OGG")]

router = APIRouter()


@router.post(DETECTIONS_PATH)
async def detect(
    request: Request,
    engine: Engine,
    file: AudioUpload,
) -> Detection:
    """Las canciones del catálogo que suenan dentro del audio, por votación por desfase.

    Responde `413` si pesa o dura más de lo que admite la configuración, `415` si su
    contenido no es MP3, WAV, FLAC ni OGG (se mira el contenido, no el nombre) y `400` si
    está vacío, dañado o dura menos de un segundo. Un audio en silencio no es un error: no
    tiene coincidencias.
    """
    settings = settings_of(request)
    content = await file.read(settings.limits.max_bytes + 1)
    with timed(PROBE_STAGE):
        audio = probe_audio(content, settings.limits)
    with timed(LOUDNESS_STAGE):
        loudness = Loudness.measure(content)
    return await detect_in(engine, audio, loudness, settings)


async def detect_in(
    engine: AsyncClient, audio: ProbedAudio, loudness: Loudness, settings: DetectionSettings
) -> Detection:
    """La detección de un audio ya reconocido y medido. Uno en silencio no llega al gestor."""
    bounds = [(window.start, window.end) for window in segment_windows(audio.duration_seconds)]
    if not any(_audible(loudness, bounds, settings)):
        return _detection(audio, settings, windows=len(bounds), silent=len(bounds), matches=[])
    with timed(SEGMENTS_STAGE):
        search = await _search_segments(engine, audio, settings.neighbours_per_window)
    _record_engine_stages(search)
    windows = query_windows(search, loudness, settings.silence_threshold_dbfs)
    audible = sum(1 for window in windows if window.neighbours)
    with timed(VOTING_STAGE):
        runs = aligned_runs(windows, settings.voting)
    with timed(SONGS_STAGE):
        songs = await songs_by_id(engine, {run.song_id for run in runs})
    original_windows = {
        song_id: len(segment_windows(row["duracion"])) for song_id, row in songs.items()
    }
    catalogued = [run for run in runs if run.song_id in songs]
    matches = score_runs(catalogued, audible, original_windows, settings.threshold)
    return _detection(
        audio,
        settings,
        windows=len(windows),
        silent=len(windows) - audible,
        matches=[_detected(match, songs) for match in matches[: settings.max_matches]],
        engine_timings=EngineTimings.model_validate(search.timings.model_dump()),
    )


def query_windows(
    search: SegmentSearch, loudness: Loudness, silence_threshold_dbfs: float
) -> list[QueryWindow]:
    """Las ventanas que respondió el gestor con sus vecinos; las que están en silencio, sin
    ninguno, para que no voten y corten la racha."""
    silent = loudness.silent_windows(
        [(segment.start, segment.end) for segment in search.segments], silence_threshold_dbfs
    )
    return [
        QueryWindow(
            segment.start,
            segment.end,
            () if quiet else tuple(_neighbour(item.row, item.score) for item in segment.neighbours),
        )
        for segment, quiet in zip(search.segments, silent, strict=True)
    ]


async def songs_by_id(engine: AsyncClient, song_ids: Iterable[int]) -> dict[int, Record]:
    """Las filas de `cr_canciones` de `song_ids`, por id, en una sola consulta."""
    ordered = sorted(song_ids)
    if not ordered:
        return {}
    placeholders = ", ".join(f"${position}" for position in range(1, len(ordered) + 1))
    rows = await engine.fetch_all(
        SONGS_BY_ID.format(table=SONGS_TABLE, placeholders=placeholders), ordered
    )
    return {row["id"]: row for row in rows}


def settings_of(request: Request) -> DetectionSettings:
    settings = request.app.state.detection
    assert isinstance(settings, DetectionSettings)
    return settings


async def _search_segments(
    engine: AsyncClient, audio: ProbedAudio, neighbours: int
) -> SegmentSearch:
    upload = MediaFile(
        f"{UPLOAD_NAME}{audio.format.extension}", audio.content, audio.format.content_type
    )
    return await engine.segments(
        upload,
        SEGMENTS_TABLE,
        k=neighbours,
        window=WINDOWING.window_seconds,
        hop=WINDOWING.hop_seconds,
    )


def _record_engine_stages(search: SegmentSearch) -> None:
    record_stage(ENGINE_DECODE_STAGE, search.timings.decode_ms)
    record_stage(ENGINE_EXTRACTION_STAGE, search.timings.extraction_ms)
    record_stage(ENGINE_SEARCH_STAGE, search.timings.search_ms)


def _audible(
    loudness: Loudness, bounds: Sequence[tuple[float, float]], settings: DetectionSettings
) -> list[bool]:
    return [not quiet for quiet in loudness.silent_windows(bounds, settings.silence_threshold_dbfs)]


def _neighbour(row: Record, similarity: float) -> Neighbour:
    return Neighbour(
        song_id=row[SEGMENT_SONG_COLUMN],
        start=row[SEGMENT_START_COLUMN],
        end=row[SEGMENT_END_COLUMN],
        similarity=similarity,
    )


def _detected(match: Match, songs: dict[int, Record]) -> DetectedMatch:
    run = match.run
    song = songs[run.song_id]
    return DetectedMatch(
        song=SongReference(id=run.song_id, title=song["titulo"], artist=song["artista"]),
        upload=TimeSpan(start_seconds=run.upload.start, end_seconds=run.upload.end),
        original=TimeSpan(start_seconds=run.original.start, end_seconds=run.original.end),
        offset_seconds=run.offset_seconds,
        windows=run.windows,
        mean_similarity=run.mean_similarity,
        coverage=match.coverage,
        score=match.score,
        above_threshold=match.above_threshold,
    )


def _detection(
    audio: ProbedAudio,
    settings: DetectionSettings,
    *,
    windows: int,
    silent: int,
    matches: list[DetectedMatch],
    engine_timings: EngineTimings | None = None,
) -> Detection:
    return Detection(
        format=audio.format.name,
        duration_seconds=audio.duration_seconds,
        sample_rate=audio.sample_rate,
        channels=audio.channels,
        window_seconds=WINDOWING.window_seconds,
        hop_seconds=WINDOWING.hop_seconds,
        windows=windows,
        silent_windows=silent,
        threshold=settings.threshold,
        detected=any(match.above_threshold for match in matches),
        matches=matches,
        engine_timings=engine_timings,
    )
