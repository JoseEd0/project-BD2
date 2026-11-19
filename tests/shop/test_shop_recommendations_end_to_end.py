"""El recomendador contra el gestor real con el catálogo diminuto: cada señal, la fusión con
su explicación, las compras y la caché (fakeredis, y un Redis que no responde)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import fakeredis
import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.shop.conftest import ENGINE_KEY, ENGINE_URL

from appkit import RedisFactory, redis_factory
from minigestor_client import AsyncClient
from shop.api import create_app
from shop.catalog.schema import COPURCHASES, HISTORY, PURCHASES
from shop.recommend import queries
from shop.settings import ShopSettings

GUITAR = "B000GUITAR"
CABLE = "B000CABLES"
STARTER_PACK = "B000COPYGT"
STRINGS = "B000STRING"
DRUMS = "B0000DRUMS"
NEAR_CENTRO = {"lat": -12.0470, "lon": -77.0430}
CLOSED_PORT_REDIS = "redis://127.0.0.1:1/0"
REDIS_TIMEOUT_SECONDS = 0.2


class AnyTextEncoder:
    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), 512), dtype=np.float32)


def shop_client(engine_app: FastAPI, tmp_path: Path, redis: RedisFactory) -> TestClient:
    settings = ShopSettings(
        engine_url=ENGINE_URL,  # type: ignore[arg-type]
        engine_key=ENGINE_KEY,  # type: ignore[arg-type]
        models_dir=tmp_path / "modelos",
        ready_cache_seconds=0,
    )
    app = create_app(
        settings,
        text_encoder=AnyTextEncoder(),
        engine_transport=httpx.ASGITransport(app=engine_app),
        redis=redis,
    )
    return TestClient(app)


@pytest.fixture
def shop(seeded: None, engine_app: FastAPI, tmp_path: Path) -> Iterator[TestClient]:
    with shop_client(engine_app, tmp_path, fakeredis.FakeAsyncRedis) as client:
        yield client


def engine_rows(engine_app: FastAPI, sql: str, params: list[object]) -> list[dict[str, object]]:
    async def run() -> list[dict[str, object]]:
        transport = httpx.ASGITransport(app=engine_app)
        async with AsyncClient(ENGINE_URL, ENGINE_KEY, transport=transport) as engine:
            return await engine.fetch_all(sql, params)

    return asyncio.run(run())


def ids(response: httpx.Response) -> list[str]:
    return [item["producto"]["id"] for item in response.json()["recomendaciones"]]


def reasons_of(response: httpx.Response, product: str) -> list[str]:
    (found,) = [
        item for item in response.json()["recomendaciones"] if item["producto"]["id"] == product
    ]
    return [reason["texto"] for reason in found["motivos"]]


@pytest.mark.parametrize("table", [PURCHASES, HISTORY])
def test_the_materialized_pairs_match_the_self_join_on_the_fly(
    seeded: None, engine_app: FastAPI, table: str
):
    on_the_fly = engine_rows(engine_app, queries.copurchases_on_the_fly(table), [GUITAR, "train", 10])
    stored = engine_rows(
        engine_app, f"SELECT otro, veces FROM {COPURCHASES} WHERE producto = $1", [GUITAR]
    )
    assert on_the_fly == [
        {"otro": CABLE, "veces": 2},
        {"otro": DRUMS, "veces": 1},
        {"otro": STRINGS, "veces": 1},
    ]
    assert stored == [{"otro": CABLE, "veces": 2}]


def test_a_product_is_recommended_what_was_bought_with_it(shop: TestClient):
    found = shop.get(f"/productos/{GUITAR}/recomendaciones")
    assert found.status_code == 200, found.json()
    body = found.json()
    assert ids(found)[:2] == [STARTER_PACK, CABLE]
    assert GUITAR not in ids(found)
    assert body["arranque_en_frio"] is False
    assert reasons_of(found, CABLE)[0] == "comprado junto con este producto por 2 clientes"
    signals = {signal["senal"]: signal for signal in body["senales"]}
    assert set(signals) == {"compras", "visual", "texto"}
    assert signals["compras"]["sql"] == queries.COPURCHASES_OF_PRODUCT
    assert signals["compras"]["parametros"][:2] == [GUITAR, "train"]
    assert signals["visual"]["parametros"][0] == "vector(512)"
    assert "MATCH(texto, $1) USING BM25" in signals["texto"]["sql"]


def test_a_product_nobody_bought_with_another_is_a_cold_start(shop: TestClient):
    found = shop.get(f"/productos/{STARTER_PACK}/recomendaciones").json()
    assert found["arranque_en_frio"] is True
    assert found["pesos"]["compras"] == 0
    assert found["pesos"]["visual"] + found["pesos"]["texto"] == pytest.approx(0.9)
    first = found["recomendaciones"][0]
    assert first["producto"]["id"] == GUITAR
    assert {reason["texto"] for reason in first["motivos"]} >= {
        "se ve parecido",
        "descripción parecida",
    }


def test_stock_nearby_is_explained_with_the_store_and_distance(shop: TestClient):
    found = shop.get(f"/productos/{GUITAR}/recomendaciones", params=NEAR_CENTRO)
    signals = [signal["senal"] for signal in found.json()["senales"]]
    assert signals[-1] == "cercania"
    assert any(text.startswith("en stock a") and text.endswith("en Centro")
               for text in reasons_of(found, CABLE))


def test_the_sift_index_is_the_other_visual_signal(shop: TestClient):
    found = shop.get(f"/productos/{STARTER_PACK}/recomendaciones", params={"visual": "sift"})
    visual = [signal for signal in found.json()["senales"] if signal["senal"] == "visual"]
    assert "SIMILAR_TO(imagen, $1" in visual[0]["sql"]
    assert ids(found)[0] == GUITAR


def test_a_user_is_recommended_from_their_training_purchases(shop: TestClient):
    found = shop.get("/usuarios/USER-C/recomendaciones")
    assert found.status_code == 200, found.json()
    assert {GUITAR, STRINGS}.isdisjoint(ids(found))
    assert CABLE in ids(found)
    assert reasons_of(found, CABLE)[0] == "comprado junto con una de tus compras por 2 clientes"


def test_unknown_products_users_and_half_locations_are_rejected(shop: TestClient):
    assert shop.get("/productos/NOPE/recomendaciones").status_code == 404
    assert shop.get("/usuarios/NOPE/recomendaciones").status_code == 404
    half = shop.get(f"/productos/{GUITAR}/recomendaciones", params={"lat": -12.0})
    assert (half.status_code, half.json()["kind"]) == (400, "BadRequestError")


def test_a_response_is_served_from_the_cache_the_second_time(shop: TestClient):
    first = shop.get(f"/productos/{GUITAR}/recomendaciones")
    second = shop.get(f"/productos/{GUITAR}/recomendaciones")
    other = shop.get(f"/productos/{GUITAR}/recomendaciones", params={"limite": 2})
    assert [first.headers["X-Cache"], second.headers["X-Cache"]] == ["miss", "hit"]
    assert second.json() == first.json()
    assert other.headers["X-Cache"] == "miss"


def test_a_purchase_updates_the_pairs_and_invalidates_the_cache(
    shop: TestClient, engine_app: FastAPI
):
    shop.get(f"/productos/{GUITAR}/recomendaciones")
    shop.get("/usuarios/USER-C/recomendaciones")

    bought = shop.post("/usuarios/USER-C/compras", json={"producto": CABLE})

    assert bought.status_code == 201, bought.json()
    assert bought.json()["id"] == 14
    assert (bought.json()["pares_sumados"], bought.json()["pares_nuevos"]) == (1, 1)
    after = shop.get(f"/productos/{GUITAR}/recomendaciones")
    assert after.headers["X-Cache"] == "miss"
    assert reasons_of(after, CABLE)[0] == "comprado junto con este producto por 3 clientes"
    user = shop.get("/usuarios/USER-C/recomendaciones")
    assert user.headers["X-Cache"] == "miss"
    assert CABLE not in ids(user)
    pairs = engine_rows(
        engine_app, f"SELECT veces FROM {COPURCHASES} WHERE producto = $1 AND otro = $2",
        [STRINGS, CABLE],
    )
    assert pairs == [{"veces": 1}]


def test_a_purchase_of_an_unknown_product_writes_nothing(shop: TestClient, engine_app: FastAPI):
    missing = shop.post("/usuarios/USER-C/compras", json={"producto": "NOPE"})
    assert missing.status_code == 404
    assert len(engine_rows(engine_app, f"SELECT id FROM {PURCHASES}", [])) == 13


def test_without_redis_the_service_still_answers(
    seeded: None, engine_app: FastAPI, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    unreachable = redis_factory(CLOSED_PORT_REDIS, REDIS_TIMEOUT_SECONDS)
    with shop_client(engine_app, tmp_path, unreachable) as shop:
        first = shop.get(f"/productos/{GUITAR}/recomendaciones")
        second = shop.get(f"/productos/{GUITAR}/recomendaciones")
        bought = shop.post("/usuarios/USER-C/compras", json={"producto": CABLE})
        shop_health = shop.get("/health").json()
    assert [first.status_code, second.status_code, bought.status_code] == [200, 200, 201]
    assert first.headers["X-Cache"] == second.headers["X-Cache"] == "bypass"
    assert shop_health["degraded"]["redis"].startswith("sin caché")
    warnings = [record for record in caplog.records if "Redis no responde" in record.message]
    assert len(warnings) == 1
