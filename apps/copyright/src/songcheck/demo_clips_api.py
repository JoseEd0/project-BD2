"""`GET /demo-clips` y `GET /demo-clips/{clip}/audio`: los audios de prueba (`demo_clips`).

Cada clip se arma la primera vez que se pide, con el audio de sus canciones leído del gestor,
y queda en memoria del proceso: son cuatro WAV de menos de 2 MB.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from appkit import Engine, ResourceNotFoundError, timed
from minigestor_client import AsyncClient, Record
from songcheck.demo_clips import (
    EXCERPT_SECONDS,
    MASHUP_PIECE_SECONDS,
    NOISE_SNR_DB,
    NOISY_SECONDS,
    DemoClipId,
    DemoSongs,
    build_clip,
    songs_of_clip,
)
from songcheck.detection_api import songs_by_id
from songcheck.schema import SONGS_TABLE

WAV_MEDIA_TYPE = "audio/wav"
CLIPS_STAGE = "clips"
SONG_PATH = f"SELECT ruta FROM {SONGS_TABLE} WHERE id = $1"


class DemoClip(BaseModel):
    """Un audio de prueba: qué es y qué debería encontrar la detección."""

    id: DemoClipId
    title: str
    description: str
    expected_songs: list[int]


@dataclass
class ClipCache:
    """Los WAV ya armados, por clip; el candado evita armar el mismo dos veces a la vez."""

    songs: DemoSongs
    built: dict[DemoClipId, bytes] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


router = APIRouter()


def _name(songs: dict[int, Record], song_id: int) -> str:
    row = songs.get(song_id)
    return f"«{row['titulo']}» de {row['artista']}" if row is not None else f"la canción {song_id}"


def describe(songs: DemoSongs, rows: dict[int, Record]) -> list[DemoClip]:
    first, second, excerpt = (_name(rows, song_id) for song_id in songs.ids)
    piece = f"{MASHUP_PIECE_SECONDS:g}"
    return [
        DemoClip(
            id=DemoClipId.MASHUP,
            title="Mezcla de dos canciones",
            description=f"{piece} s de {first} seguidos de {piece} s de {second}.",
            expected_songs=[songs.first, songs.second],
        ),
        DemoClip(
            id=DemoClipId.EXCERPT,
            title="Extracto a mitad de canción",
            description=f"{EXCERPT_SECONDS:g} s de {excerpt}, empezando a mitad de la pista.",
            expected_songs=[songs.excerpt],
        ),
        DemoClip(
            id=DemoClipId.NOISY,
            title="Extracto con ruido de fondo",
            description=(
                f"{NOISY_SECONDS:g} s de {excerpt} con ruido {NOISE_SNR_DB:g} dB por debajo."
            ),
            expected_songs=[songs.excerpt],
        ),
        DemoClip(
            id=DemoClipId.SYNTHETIC,
            title="Melodía sintética",
            description=(
                "Notas generadas al azar que no están en el catálogo: no debería encontrar copias."
            ),
            expected_songs=[],
        ),
    ]


@router.get("/demo-clips")
async def list_demo_clips(request: Request, engine: Engine) -> list[DemoClip]:
    """Los audios de prueba, con las canciones del catálogo que contiene cada uno."""
    songs = cache_of(request).songs
    with timed(CLIPS_STAGE):
        rows = await songs_by_id(engine, songs.ids)
    return describe(songs, rows)


@router.get(
    "/demo-clips/{clip}/audio",
    response_class=Response,
    responses={200: {"content": {WAV_MEDIA_TYPE: {}}, "description": "El clip en WAV."}},
)
async def demo_clip_audio(clip: DemoClipId, request: Request, engine: Engine) -> Response:
    """El WAV del clip, armado la primera vez; `404` si falta una canción del catálogo."""
    cache = cache_of(request)
    async with cache.lock:
        if clip not in cache.built:
            with timed(CLIPS_STAGE):
                audio = {
                    song: await song_audio(engine, song)
                    for song in songs_of_clip(clip, cache.songs)
                }
                cache.built[clip] = build_clip(clip, cache.songs, audio)
    return Response(cache.built[clip], media_type=WAV_MEDIA_TYPE)


async def song_audio(engine: AsyncClient, song_id: int) -> bytes:
    path = await engine.scalar(SONG_PATH, [song_id])
    if path is None:
        raise ResourceNotFoundError(f"no hay una canción {song_id} en el catálogo")
    async with engine.stream_file(path) as stream:
        return b"".join([chunk async for chunk in stream.chunks])


def cache_of(request: Request) -> ClipCache:
    cache = request.app.state.demo_clips
    assert isinstance(cache, ClipCache)
    return cache
