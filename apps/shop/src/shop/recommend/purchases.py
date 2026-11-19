"""Registrar una compra: la compra, su fila del historial, el contador del usuario y los pares
de `shop_compras_conjuntas`, todo en una transacción.

`shop_compras_conjuntas` es una vista materializada: la carga la calcula con los pares que
compraron al menos `copurchase_min_support` clientes, y cada compra nueva la mantiene. Un par
que ya estaba suma un cliente en los dos sentidos; uno que no estaba entra con 1. Si la carga
lo había dejado fuera por no llegar al soporte, ese 1 cuenta solo las compras nuevas: es una
cota inferior hasta que la carga vuelva a calcular la tabla.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from appkit import ResourceConflictError
from minigestor_client import AsyncClient, AsyncTransaction, ConflictError
from shop.catalog.schema import PURCHASES

from . import queries
from .recommender import UnknownProductError, UnknownUserError

MILLISECONDS_PER_SECOND = 1000
FIRST_PURCHASE_ID = 1
# El número de filas de `/tables` es un contador que puede contar inserciones aún sin
# confirmar; buscar el último id desde un poco antes lo encuentra igual, con un rango corto
# del archivo secuencial en vez de recorrerlo.
LAST_ID_LOOKBACK = 1000
NEW_PAIR_CUSTOMERS = 1
PAIR_DIRECTIONS = 2


@dataclass(frozen=True, slots=True)
class Purchase:
    """Una compra registrada y qué pares de compras conjuntas cambió."""

    id: int
    user: str
    product: str
    instant_ms: int
    split: str
    pairs_incremented: int
    pairs_added: int


class PurchaseConflictError(ResourceConflictError):
    """Otras compras simultáneas se quedaron con el id en todos los intentos."""


async def record_purchase(
    engine: AsyncClient,
    user: str,
    product: str,
    split: str,
    history: Sequence[str],
    attempts: int,
) -> Purchase:
    """Registra que `user` compró `product` en la partición `split`. `history` son los
    productos que ya había comprado en ella: con ellos forma los pares nuevos.

    Raises:
        UnknownProductError: si `product` no existe.
        UnknownUserError: si `user` no existe.
        PurchaseConflictError: si en `attempts` intentos otra compra tomó el mismo id.
    """
    if await engine.fetch_one(queries.PRODUCT_EXISTS, [product]) is None:
        raise UnknownProductError(f"no hay un producto {product}")
    partners = [] if product in history else sorted(set(history))
    for _ in range(attempts):
        try:
            return await _record_once(engine, user, product, split, partners)
        except ConflictError:
            continue
    raise PurchaseConflictError(f"la compra chocó {attempts} veces con otras simultáneas")


async def _record_once(
    engine: AsyncClient, user: str, product: str, split: str, partners: Sequence[str]
) -> Purchase:
    instant = int(time.time() * MILLISECONDS_PER_SECOND)
    day = datetime.fromtimestamp(instant / MILLISECONDS_PER_SECOND, tz=UTC).date().isoformat()
    purchase_id = await _next_purchase_id(engine)
    async with engine.transaction() as transaction:
        if await transaction.execute(queries.COUNT_USER_PURCHASE, [user]) == 0:
            raise UnknownUserError(f"no hay un usuario {user}")
        await transaction.execute(
            queries.INSERT_PURCHASE, [purchase_id, user, product, instant, day, split]
        )
        await transaction.execute(queries.INSERT_HISTORY, [user, product, instant, split])
        incremented, added = await _count_pairs(transaction, product, split, partners)
    return Purchase(purchase_id, user, product, instant, split, incremented, added)


async def _next_purchase_id(engine: AsyncClient) -> int:
    counts = {table.name: table.row_count for table in await engine.tables()}
    lower = max(FIRST_PURCHASE_ID, counts.get(PURCHASES, 0) - LAST_ID_LOOKBACK)
    last = await engine.scalar(queries.LAST_PURCHASE_FROM, [lower])
    if last is None and lower > FIRST_PURCHASE_ID:
        last = await engine.scalar(queries.LAST_PURCHASE_FROM, [FIRST_PURCHASE_ID])
    return FIRST_PURCHASE_ID if last is None else int(last) + 1


async def _count_pairs(
    transaction: AsyncTransaction, product: str, split: str, partners: Sequence[str]
) -> tuple[int, int]:
    """Suma un cliente a cada par de `product` con `partners`: `(sumados, nuevos)`."""
    if not partners:
        return 0, 0
    existing = {
        str(row["otro"])
        for row in await transaction.fetch_all(queries.PARTNERS_OF_PRODUCT, [product, split])
    }
    known = [partner for partner in partners if partner in existing]
    new = [partner for partner in partners if partner not in existing]
    if known:
        parameters = [product, split, *known]
        await transaction.execute(queries.increment_pairs_from(len(known)), parameters)
        await transaction.execute(queries.increment_pairs_to(len(known)), parameters)
    if new:
        rows = [
            value
            for partner in new
            for pair in ((product, partner), (partner, product))
            for value in (*pair, split, NEW_PAIR_CUSTOMERS)
        ]
        await transaction.execute(queries.insert_pairs(PAIR_DIRECTIONS * len(new)), rows)
    return len(known), len(new)
