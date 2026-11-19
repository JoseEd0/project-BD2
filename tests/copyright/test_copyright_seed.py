"""El trabajo de carga del servicio de copyright contra el gestor real, en el mismo proceso.

Vive en el árbol del gestor porque construye su aplicación (`api.main.create_app`), y
ningún archivo de `apps/` puede importar el motor. Las canciones son audios generados.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from itertools import islice
from pathlib import Path

import httpx
import pytest
from apps.copyright.tests.copyright_support import serving as serving_fma
from apps.copyright.tests.copyright_support import toy_fma
from fastapi import FastAPI
from tests.client.engine_server import serving as serving_engine
from tests.multimedia.media_support import chirp, tone, write_audio

from api.main import create_app
from appkit import load_settings, migrate
from config import DEFAULT_PAGE_SIZE, EngineConfig
from minigestor_client import AsyncClient, Client
from songcheck.catalog import CatalogSong
from songcheck.errors import SeedError
from songcheck.schema import SCHEMA, SEGMENT_INDEX_NAME, SEGMENTS_TABLE, SONGS_TABLE
from songcheck.seed import (
    load_catalog,
    run_seed,
    segment_count,
    segment_rows,
)
from songcheck.settings import ENV_PREFIX, SeedSettings

pytestmark = pytest.mark.anyio

ASGI_URL = "http://gestor"
VOCABULARY = 8
SONG_SECONDS = (12.5, 6.0, 3.0)
TOO_LONG_GENRE = "g" * 41
INTERRUPTED_AFTER = 3


@pytest.fixture
def media(tmp_path: Path) -> Path:
    directory = tmp_path / "datasets"
    directory.mkdir()
    return directory


@pytest.fixture
def engine_app(config: EngineConfig, media: Path) -> Iterator[FastAPI]:
    app = create_app(
        replace(
            config,
            page_size=DEFAULT_PAGE_SIZE,
            import_directories=(media,),
            feature_workers=1,
            vocabulary_size=VOCABULARY,
        )
    )
    yield app
    app.state.service.close()


@pytest.fixture
async def engine(engine_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(ASGI_URL, transport=httpx.ASGITransport(app=engine_app)) as client:
        yield client


def catalog(media: Path) -> list[CatalogSong]:
    songs = []
    for number, seconds in enumerate(SONG_SECONDS, start=1):
        samples = 0.7 * chirp(seconds) + 0.3 * tone(seconds)
        path = write_audio(media / f"cancion{number}.wav", samples)
        songs.append(
            CatalogSong(number, f"Canción {number}", "Ava", "Folk", seconds, path.name, 0, 0)
        )
    return songs


async def table_ids(engine: AsyncClient, table: str) -> list[int]:
    rows = await engine.fetch_all(f"SELECT id FROM {table} ORDER BY id")
    return [row["id"] for row in rows]


async def test_the_catalog_lands_in_the_engine_indexed_by_segments(
    engine: AsyncClient, media: Path
):
    songs = catalog(media)
    load = await load_catalog(engine, engine, songs, str(media))
    assert (load.songs.loaded_rows, load.segments.loaded_rows) == (3, segment_count(songs))
    assert load.segments.loaded_rows == 4 + 1 + 1
    assert load.index.created
    tables = {table.name: table for table in await engine.tables()}
    assert SEGMENT_INDEX_NAME in tables[SEGMENTS_TABLE].indexes
    segment = await engine.fetch_one(f"SELECT * FROM {SEGMENTS_TABLE} WHERE id = $1", [1001])
    assert segment == {
        "id": 1001,
        "cancion": 1,
        "inicio": 2.5,
        "fin": 7.5,
        "ruta": f"{media}/cancion1.wav#t=2.5,7.5",
    }


async def test_a_segment_finds_itself_through_the_hnsw(engine: AsyncClient, media: Path):
    await load_catalog(engine, engine, catalog(media), str(media))
    nearest = await engine.fetch_one(
        f"SELECT cancion, inicio FROM {SEGMENTS_TABLE} WHERE SIMILAR_TO($1, k = 1)",
        [f"{media}/cancion1.wav#t=5,10"],
    )
    assert nearest == {"cancion": 1, "inicio": 5.0}


async def test_a_second_load_changes_nothing(engine: AsyncClient, media: Path):
    songs = catalog(media)
    await load_catalog(engine, engine, songs, str(media))
    again = await load_catalog(engine, engine, songs, str(media))
    assert (again.songs.loaded_rows, again.segments.loaded_rows) == (0, 0)
    assert not again.index.created
    assert await table_ids(engine, SONGS_TABLE) == [1, 2, 3]


async def test_an_interrupted_load_continues_after_the_last_row(engine: AsyncClient, media: Path):
    songs = catalog(media)
    root = str(media)
    await migrate(engine, SCHEMA)
    await engine.bulk_insert(SEGMENTS_TABLE, islice(segment_rows(songs, root), INTERRUPTED_AFTER))
    load = await load_catalog(engine, engine, songs, root)
    assert load.segments.loaded_rows == segment_count(songs) - INTERRUPTED_AFTER
    assert await table_ids(engine, SEGMENTS_TABLE) == [1000, 1001, 1002, 1003, 2000, 3000]


async def test_a_rejected_batch_stops_the_load(engine: AsyncClient, media: Path):
    songs = [replace(song, genre=TOO_LONG_GENRE) for song in catalog(media)]
    with pytest.raises(SeedError, match="rechazó"):
        await load_catalog(engine, engine, songs, str(media))


def test_the_seed_job_downloads_loads_and_repeats_without_doing_anything(
    engine_app: FastAPI, media: Path, monkeypatch: pytest.MonkeyPatch
):
    with serving_fma(toy_fma()) as fma, serving_engine(engine_app) as engine_url:
        sources = fma.sources()
        for variable, value in {
            "ENGINE_URL": engine_url,
            "ENGINE_KEY": "sin-claves-en-el-gestor-de-prueba",
            "DATA_DIR": str(media),
            "AUDIO_URL": sources.audio_url,
            "METADATA_URL": sources.metadata_url,
        }.items():
            monkeypatch.setenv(ENV_PREFIX + variable, value)
        settings = load_settings(SeedSettings)
        first = run_seed(settings)
        second = run_seed(settings)
        with Client(engine_url) as client:
            titles = client.fetch_all(f"SELECT id, titulo FROM {SONGS_TABLE} ORDER BY id")
    assert (first.catalog.fetched, first.load.songs.loaded_rows, first.load.index.created) == (
        4,
        4,
        True,
    )
    assert first.load.segments.loaded_rows == 4
    assert (second.catalog.fetched, second.load.songs.loaded_rows) == (0, 0)
    assert (second.load.segments.loaded_rows, second.load.index.created) == (0, False)
    assert titles[0] == {"id": 2, "titulo": "Food"}
