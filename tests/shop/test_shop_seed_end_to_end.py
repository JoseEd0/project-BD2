"""`shop.seed` contra el gestor real: carga todo, una segunda vez no repite nada y una carga
cortada sigue donde quedó."""

from __future__ import annotations

from dataclasses import replace
from itertools import islice

import httpx
import pytest
from apps.shop.tests.tiny_dataset import TINY_PRODUCTS, TINY_PURCHASES
from tests.shop.conftest import ENGINE_KEY, ENGINE_URL, FakeImageEncoder

from api.main import create_app
from config import EngineConfig
from minigestor_client import AsyncClient, DomainError
from shop.catalog import rows
from shop.catalog.schema import (
    CLIP,
    CLIP_INDEX,
    INVENTORY,
    INVENTORY_INDEX,
    PRODUCTS,
    PURCHASES,
    SCHEMA,
    SIFT_INDEX_NAME,
    STORES,
    STORES_INDEX,
    TEXT_INDEX,
    USERS,
)
from shop.seed import SeedError, run_seed
from shop.seed.job import EncoderLoader
from shop.settings import SeedSettings

pytestmark = pytest.mark.anyio

PRODUCTS_WITH_PHOTO = sum(1 for product in TINY_PRODUCTS if product["image_seed"])
DISTINCT_PHOTOS = len({product["image_seed"] for product in TINY_PRODUCTS} - {None})
EXPECTED_ROWS = {
    STORES: 3,
    PRODUCTS: len(TINY_PRODUCTS),
    USERS: 2,
    PURCHASES: len(TINY_PURCHASES),
    INVENTORY: 3,
    CLIP: PRODUCTS_WITH_PHOTO,
}


async def row_counts(engine: AsyncClient) -> dict[str, int]:
    return {table.name: table.row_count for table in await engine.tables()}


async def test_the_seed_loads_every_table_and_index(
    engine: AsyncClient,
    seed_settings: SeedSettings,
    load_encoder: EncoderLoader,
    image_encoder: FakeImageEncoder,
):
    report = await run_seed(engine, seed_settings, load_encoder)

    assert await row_counts(engine) == EXPECTED_ROWS
    indexes = {table.name: set(table.indexes) for table in await engine.tables()}
    assert {TEXT_INDEX, SIFT_INDEX_NAME} <= indexes[PRODUCTS]
    assert CLIP_INDEX in indexes[CLIP]
    assert STORES_INDEX in indexes[STORES]
    assert INVENTORY_INDEX in indexes[INVENTORY]
    deferred = {load.table: set(load.deferred_indexes) for load in report.tables}
    assert deferred[PRODUCTS] == {TEXT_INDEX}
    assert deferred[STORES] == {STORES_INDEX}
    assert deferred[CLIP] == {CLIP_INDEX}
    assert [(index.index, index.created) for index in report.indexes] == [(SIFT_INDEX_NAME, True)]
    assert report.embeddings is not None
    assert (report.embeddings.images, report.embeddings.computed) == (
        DISTINCT_PHOTOS,
        DISTINCT_PHOTOS,
    )
    assert image_encoder.embedded == DISTINCT_PHOTOS


async def test_nulls_and_purchase_order_reach_the_engine(
    engine: AsyncClient, seed_settings: SeedSettings, load_encoder: EncoderLoader
):
    await run_seed(engine, seed_settings, load_encoder)

    drums = await engine.fetch_one(f"SELECT precio, imagen FROM {PRODUCTS} WHERE id = 'B0000DRUMS'")
    cable = await engine.fetch_one(f"SELECT marca, imagen FROM {PRODUCTS} WHERE id = 'B000CABLES'")
    assert drums is not None and drums["precio"] is None and drums["imagen"] is not None
    assert cable == {"marca": None, "imagen": None}
    dates = await engine.fetch_all(f"SELECT id, fecha FROM {PURCHASES}")
    assert [row["id"] for row in dates] == list(range(1, len(TINY_PURCHASES) + 1))
    assert [row["fecha"] for row in dates] == sorted(row["fecha"] for row in dates)
    assert await engine.scalar(f"SELECT id FROM {CLIP} WHERE id = 'B000CABLES'") is None


async def test_the_catalog_answers_the_four_kinds_of_query(
    engine: AsyncClient, seed_settings: SeedSettings, load_encoder: EncoderLoader
):
    await run_seed(engine, seed_settings, load_encoder)

    found = await engine.fetch_all(
        f"SELECT id FROM {PRODUCTS} WHERE MATCH(texto, $1) USING BM25", ["guitars"]
    )
    assert {row["id"] for row in found} == {"B000GUITAR", "B000STRING", "B000COPYGT"}
    photo = await engine.scalar(f"SELECT imagen FROM {PRODUCTS} WHERE id = 'B000GUITAR'")
    similar = await engine.fetch_all(
        f"SELECT id, SIMILARITY_SCORE() AS s FROM {PRODUCTS} "
        "WHERE SIMILAR_TO(imagen, $1, k = 2) USING HNSW",
        [photo],
    )
    assert {row["id"] for row in similar} == {"B000GUITAR", "B000COPYGT"}
    vector = await engine.scalar(f"SELECT embedding FROM {CLIP} WHERE id = 'B000STRING'")
    nearest = await engine.fetch_one(
        f"SELECT id FROM {CLIP} WHERE SIMILAR_TO(embedding, $1, k = 1)", [vector]
    )
    assert nearest == {"id": "B000STRING"}
    stores = await engine.fetch_all(
        f"SELECT id FROM {STORES} WHERE distancia(ubicacion, POINT(-12.05, -77.04)) < 2000"
    )
    assert [row["id"] for row in stores] == [101]


async def test_a_second_run_changes_nothing(
    engine: AsyncClient,
    seed_settings: SeedSettings,
    load_encoder: EncoderLoader,
    image_encoder: FakeImageEncoder,
):
    await run_seed(engine, seed_settings, load_encoder)
    image_encoder.embedded = 0

    again = await run_seed(engine, seed_settings, load_encoder)

    assert all(load.skipped for load in again.tables)
    assert [index.created for index in again.indexes] == [False]
    assert image_encoder.embedded == 0
    assert await row_counts(engine) == EXPECTED_ROWS


async def test_a_reseed_of_a_new_engine_reuses_the_embedding_cache(
    engine: AsyncClient,
    seed_settings: SeedSettings,
    load_encoder: EncoderLoader,
    image_encoder: FakeImageEncoder,
):
    await run_seed(engine, seed_settings, load_encoder)
    await engine.execute(f"DROP TABLE {CLIP}")
    image_encoder.embedded = 0

    report = await run_seed(engine, seed_settings, load_encoder)

    assert image_encoder.embedded == 0
    assert report.embeddings is not None
    assert report.embeddings.cached == DISTINCT_PHOTOS
    assert (await row_counts(engine))[CLIP] == PRODUCTS_WITH_PHOTO


async def test_an_interrupted_load_resumes_after_its_last_row(
    engine: AsyncClient, seed_settings: SeedSettings, load_encoder: EncoderLoader
):
    await run_seed(engine, seed_settings, load_encoder)
    await engine.execute(f"DROP TABLE {PURCHASES}")
    await engine.execute(f"DROP TABLE {USERS}")
    for statement in SCHEMA:
        await engine.execute(statement)
    first = list(islice(rows.purchase_rows(seed_settings.dataset), 3))
    await engine.bulk_insert(PURCHASES, first)

    report = await run_seed(engine, seed_settings, load_encoder)

    loads = {load.table: load.loaded for load in report.tables}
    assert loads[PURCHASES] == len(TINY_PURCHASES) - len(first)
    assert loads[USERS] == 2
    ids = await engine.fetch_all(f"SELECT id FROM {PURCHASES}")
    assert [row["id"] for row in ids] == list(range(1, len(TINY_PURCHASES) + 1))


async def test_a_table_with_more_rows_than_the_dataset_stops_the_seed(
    engine: AsyncClient, seed_settings: SeedSettings, load_encoder: EncoderLoader
):
    await run_seed(engine, seed_settings, load_encoder)
    await engine.execute(f"INSERT INTO {USERS} VALUES ('USER-Z', 1)")
    with pytest.raises(SeedError, match="vacíala"):
        await run_seed(engine, seed_settings, load_encoder)


async def test_the_engine_must_be_allowed_to_read_the_photos(
    engine_config: EngineConfig, seed_settings: SeedSettings, load_encoder: EncoderLoader
):
    app = create_app(replace(engine_config, import_directories=()))
    try:
        transport = httpx.ASGITransport(app=app)
        async with AsyncClient(ENGINE_URL, ENGINE_KEY, transport=transport) as engine:
            with pytest.raises(DomainError, match="fuera de los directorios"):
                await run_seed(engine, seed_settings, load_encoder)
    finally:
        app.state.service.close()
