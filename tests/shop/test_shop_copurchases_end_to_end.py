"""La materialización de las compras conjuntas contra el gestor real con un historial sesgado:
un producto que compran todos, lotes que no caben en una respuesta y lotes que pasan del
plazo de sentencia."""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from typing import Any

import httpx
import pytest
from tests.shop.conftest import ENGINE_KEY, ENGINE_URL

from api.main import create_app
from appkit import migrate
from config import EngineConfig
from minigestor_client import AsyncClient, QueryResult, StatementTimeoutError
from shop.catalog.schema import COPURCHASES, HISTORY, SCHEMA
from shop.seed.copurchases import (
    CopurchaseError,
    CopurchaseSettings,
    count_buyers,
    materialize_copurchases,
    plan_chunks,
)

pytestmark = pytest.mark.anyio

HUB = "HUB0000000"
CUSTOMERS = 60
COMMON_PRODUCTS = 6
# Pocas filas por respuesta: el producto que compran todos tiene 66 pares y no cabe solo.
MAX_RESULT_ROWS = 15
SETTINGS = CopurchaseSettings(split="train", min_support=1, max_partners=4, chunk_buyers=25)


def skewed_history() -> list[dict[str, object]]:
    """Cada cliente compra el producto común a todos, uno de seis productos frecuentes y uno
    solo suyo; además, una compra de validación que no debe contar."""
    rows: list[dict[str, object]] = []
    for customer in range(CUSTOMERS):
        user = f"USER-{customer:03d}"
        bought = (HUB, f"COMMON{customer % COMMON_PRODUCTS:04d}", f"OWN{customer:07d}")
        rows.extend(
            {"usuario": user, "producto": product, "instante": customer, "particion": "train"}
            for product in bought
        )
    rows.append({"usuario": "USER-000", "producto": "LATER00000", "instante": 999, "particion": "valid"})
    return rows


def expected_pairs(rows: Sequence[dict[str, object]], limit: int) -> set[tuple[str, str, int]]:
    """Los pares de entrenamiento con sus clientes, los `limit` mejores de cada producto
    (empates por id), calculados aquí sin el gestor."""
    baskets: dict[object, set[str]] = {}
    for row in rows:
        if row["particion"] == "train":
            baskets.setdefault(row["usuario"], set()).add(str(row["producto"]))
    counts: Counter[tuple[str, str]] = Counter(
        (first, second)
        for basket in baskets.values()
        for first in basket
        for second in basket
        if first != second
    )
    by_product: dict[str, list[tuple[str, int]]] = {}
    for (first, second), customers in counts.items():
        by_product.setdefault(first, []).append((second, customers))
    return {
        (product, partner, customers)
        for product, partners in by_product.items()
        for partner, customers in sorted(partners, key=lambda pair: (-pair[1], pair[0]))[:limit]
    }


class TimingOutEngine:
    """El cliente del gestor, salvo que el `GROUP BY` de los pares pasa del plazo cuando su
    lote tiene más de `max_products` productos."""

    def __init__(self, engine: AsyncClient, max_products: int) -> None:
        self._engine = engine
        self._max_products = max_products
        self.timeouts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    async def query(self, sql: str, params: Sequence[object] = ()) -> QueryResult:
        chunk = len(params) - 2
        if "GROUP BY a.producto" in sql and chunk > self._max_products:
            self.timeouts += 1
            raise StatementTimeoutError(
                "la sentencia pasó de statement_timeout", request_id="prueba", status=504,
                kind="StatementTimeoutError",
            )
        return await self._engine.query(sql, params)


@pytest.fixture
async def small_engine(engine_config: EngineConfig) -> AsyncIterator[AsyncClient]:
    app = create_app(replace(engine_config, max_result_rows=MAX_RESULT_ROWS))
    try:
        transport = httpx.ASGITransport(app=app)
        async with AsyncClient(ENGINE_URL, ENGINE_KEY, transport=transport) as client:
            await migrate(client, SCHEMA)
            await client.bulk_insert(HISTORY, skewed_history())
            yield client
    finally:
        app.state.service.close()


async def stored_pairs(engine: AsyncClient) -> set[tuple[str, str, int]]:
    """Todos los pares guardados, de a `MAX_RESULT_ROWS` por consulta."""
    pairs: set[tuple[str, str, int]] = set()
    offset = 0
    while True:
        rows = await engine.fetch_all(
            f"SELECT producto, otro, veces FROM {COPURCHASES} ORDER BY producto, otro "
            "LIMIT $1 OFFSET $2",
            [MAX_RESULT_ROWS, offset],
        )
        if not rows:
            return pairs
        pairs.update((str(row["producto"]), str(row["otro"]), int(row["veces"])) for row in rows)
        offset += MAX_RESULT_ROWS


def test_a_product_heavier_than_the_budget_goes_alone():
    buyers = count_buyers(skewed_history(), "train")
    chunks = plan_chunks(buyers, SETTINGS.chunk_buyers)
    assert (HUB,) in chunks
    assert sorted(product for chunk in chunks for product in chunk) == sorted(buyers)
    assert all(sum(buyers[product] for product in chunk) <= SETTINGS.chunk_buyers
               for chunk in chunks if len(chunk) > 1)
    assert "LATER00000" not in buyers


async def test_the_skewed_history_keeps_the_strongest_pairs_of_each_product(
    small_engine: AsyncClient,
):
    rows = skewed_history()
    step = await materialize_copurchases(small_engine, count_buyers(rows, "train"), SETTINGS)

    stored = await stored_pairs(small_engine)
    assert stored == expected_pairs(rows, SETTINGS.max_partners)
    hub = sorted(pair for pair in stored if pair[0] == HUB)
    assert hub == [(HUB, f"COMMON{index:04d}", 10) for index in range(SETTINGS.max_partners)]
    assert step.pairs == len(stored)
    assert step.queries > len(plan_chunks(count_buyers(rows, "train"), SETTINGS.chunk_buyers))


async def test_chunks_that_time_out_are_split_until_they_fit(small_engine: AsyncClient):
    rows = skewed_history()
    slow = TimingOutEngine(small_engine, max_products=1)

    await materialize_copurchases(slow, count_buyers(rows, "train"), SETTINGS)  # type: ignore[arg-type]

    assert slow.timeouts > 0
    assert await stored_pairs(small_engine) == expected_pairs(rows, SETTINGS.max_partners)


async def test_a_single_product_that_times_out_stops_the_seed_naming_it(
    small_engine: AsyncClient,
):
    slow = TimingOutEngine(small_engine, max_products=0)
    with pytest.raises(CopurchaseError, match=HUB):
        await materialize_copurchases(slow, {HUB: CUSTOMERS}, SETTINGS)  # type: ignore[arg-type]
