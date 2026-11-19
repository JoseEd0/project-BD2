"""El API del servicio de copyright: la lectura del catálogo de canciones originales y la
detección de fragmentos copiados (`detection_api`).

    uvicorn --factory songcheck.api:create_app --host 0.0.0.0 --port 8000

Sin estado: todo lo lee del gestor con `appkit`, que pone `/health`, `/ready`, las trazas y
las migraciones al arrancar.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Annotated

import httpx
from fastapi import APIRouter, FastAPI, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from appkit import (
    Engine,
    ResourceNotFoundError,
    configure_logging,
    create_service_app,
    load_settings,
    timed,
)
from minigestor_client import Record
from minigestor_client.queries import Params
from songcheck.detection_api import DetectionSettings
from songcheck.detection_api import router as detection_router
from songcheck.schema import SCHEMA, SEGMENT_INDEX_NAME, SEGMENTS_TABLE, SONGS_TABLE
from songcheck.seed import SEGMENT_ID_STRIDE
from songcheck.settings import CopyrightSettings
from songcheck.windows import WINDOWING

NAME = "copyright"
VERSION = "0.1.0"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
ENGINE_STAGE = "gestor"
SONG_COLUMNS = "id, titulo, artista, genero, duracion"
SONG_BY_ID = f"SELECT {SONG_COLUMNS} FROM {SONGS_TABLE} WHERE id = $1"
SONG_PATH = f"SELECT ruta FROM {SONGS_TABLE} WHERE id = $1"
AUDIO_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"content": {"audio/*": {}}, "description": "El audio entero."},
    206: {"content": {"audio/*": {}}, "description": "El trozo que pidió `Range`."},
}
SEGMENTS_OF_SONG = (
    f"SELECT inicio, fin FROM {SEGMENTS_TABLE} WHERE id BETWEEN $1 AND $2 ORDER BY id"
)
GENRES = f"SELECT genero, COUNT(*) AS canciones FROM {SONGS_TABLE} GROUP BY genero ORDER BY genero"
COUNT_ROWS = "SELECT COUNT(*) FROM {table}"


class Song(BaseModel):
    """Una canción original del catálogo. `score` es su relevancia BM25 si se buscó texto."""

    id: int
    title: str
    artist: str
    genre: str
    duration_seconds: float
    score: float | None = None


class SongPage(BaseModel):
    songs: list[Song]


class Segment(BaseModel):
    start_seconds: float
    end_seconds: float


class SongDetail(Song):
    """Una canción con los tramos con que está indexada."""

    segments: list[Segment]


class GenreCount(BaseModel):
    genre: str
    songs: int


class CatalogSummary(BaseModel):
    """El tamaño del catálogo, las ventanas con que se cortó y si ya se puede buscar en él."""

    songs: int
    segments: int
    window_seconds: float
    hop_seconds: float
    segments_indexed: bool
    genres: list[GenreCount]


router = APIRouter()


@router.get("/songs")
async def list_songs(
    engine: Engine,
    query: str | None = Query(default=None, min_length=1, description="título o artista"),
    genre: str | None = None,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> SongPage:
    """Canciones del catálogo: las que mejor responden a `query` (BM25 sobre título y
    artista) o, sin ella, en orden de id; `genre` filtra por género exacto."""
    sql, params = songs_query(query, genre, limit, offset)
    with timed(ENGINE_STAGE):
        rows = await engine.fetch_all(sql, params)
    return SongPage(songs=[song_of(row) for row in rows])


@router.get("/songs/{song_id}")
async def read_song(song_id: int, engine: Engine) -> SongDetail:
    with timed(ENGINE_STAGE):
        row = await engine.fetch_one(SONG_BY_ID, [song_id])
        if row is None:
            raise ResourceNotFoundError(f"no hay una canción {song_id} en el catálogo")
        first = song_id * SEGMENT_ID_STRIDE
        segments = await engine.fetch_all(SEGMENTS_OF_SONG, [first, first + SEGMENT_ID_STRIDE - 1])
    return SongDetail(
        **song_of(row).model_dump(),
        segments=[
            Segment(start_seconds=item["inicio"], end_seconds=item["fin"]) for item in segments
        ],
    )


@router.get("/songs/{song_id}/audio", response_class=StreamingResponse, responses=AUDIO_RESPONSES)
async def song_audio(
    song_id: int,
    engine: Engine,
    byte_range: Annotated[str | None, Header(alias="Range")] = None,
) -> StreamingResponse:
    """El audio original de la canción, reenviado del gestor por trozos. Con `Range` responde
    `206` con solo ese trozo, así que un reproductor puede saltar al instante de una
    coincidencia sin bajar la pista entera; `404` si la canción no está en el catálogo."""
    with timed(ENGINE_STAGE):
        path = await engine.scalar(SONG_PATH, [song_id])
    if path is None:
        raise ResourceNotFoundError(f"no hay una canción {song_id} en el catálogo")
    resources = AsyncExitStack()
    stream = await resources.enter_async_context(engine.stream_file(path, byte_range=byte_range))
    return StreamingResponse(
        _forwarded(stream.chunks, resources),
        status_code=stream.status_code,
        headers=dict(stream.headers),
        background=BackgroundTask(resources.aclose),
    )


async def _forwarded(
    chunks: AsyncIterator[bytes], resources: AsyncExitStack
) -> AsyncIterator[bytes]:
    """Los trozos del gestor; la respuesta del gestor se cierra también si quien escucha
    corta a mitad (un reproductor que salta cancela la petición en curso)."""
    try:
        async for chunk in chunks:
            yield chunk
    finally:
        await resources.aclose()


@router.get("/catalog")
async def catalog_summary(engine: Engine) -> CatalogSummary:
    with timed(ENGINE_STAGE):
        songs = await engine.scalar(COUNT_ROWS.format(table=SONGS_TABLE))
        segments = await engine.scalar(COUNT_ROWS.format(table=SEGMENTS_TABLE))
        genres = await engine.fetch_all(GENRES)
        tables = {table.name: table for table in await engine.tables()}
    return CatalogSummary(
        songs=int(songs or 0),
        segments=int(segments or 0),
        window_seconds=WINDOWING.window_seconds,
        hop_seconds=WINDOWING.hop_seconds,
        segments_indexed=SEGMENT_INDEX_NAME in tables[SEGMENTS_TABLE].indexes,
        genres=[GenreCount(genre=row["genero"], songs=row["canciones"]) for row in genres],
    )


def songs_query(
    query: str | None, genre: str | None, limit: int, offset: int
) -> tuple[str, Params]:
    """El `SELECT` de una página de canciones y sus parámetros `$n`."""
    conditions: list[str] = []
    params: list[object] = []
    if query is not None:
        params.append(query)
        conditions.append(f"MATCH(busqueda, ${len(params)})")
    if genre is not None:
        params.append(genre)
        conditions.append(f"genero = ${len(params)}")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    if query is None:
        columns, ranking, order = SONG_COLUMNS, "", "id"
    else:
        columns, ranking, order = (
            f"{SONG_COLUMNS}, SCORE() AS puntaje",
            " USING BM25",
            "puntaje DESC",
        )
    params.extend((limit, offset))
    page = f" LIMIT ${len(params) - 1} OFFSET ${len(params)}"
    return f"SELECT {columns} FROM {SONGS_TABLE}{where}{ranking} ORDER BY {order}{page}", params


def song_of(row: Record) -> Song:
    return Song(
        id=row["id"],
        title=row["titulo"],
        artist=row["artista"],
        genre=row["genero"],
        duration_seconds=row["duracion"],
        score=row.get("puntaje"),
    )


def build_app(
    settings: CopyrightSettings, engine_transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    """La aplicación con `settings`; `engine_transport` reemplaza la red hacia el gestor en
    los tests."""
    app = create_service_app(
        settings,
        name=NAME,
        version=VERSION,
        migrations=SCHEMA,
        engine_transport=engine_transport,
    )
    app.state.detection = DetectionSettings.of(settings)
    app.include_router(router)
    app.include_router(detection_router)
    return app


def create_app() -> FastAPI:
    """La aplicación con la configuración del entorno (`COPYRIGHT_*`), para uvicorn."""
    settings = load_settings(CopyrightSettings)
    configure_logging(settings.log_level)
    return build_app(settings)
