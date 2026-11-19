"""Materializa `shop_compras_conjuntas` con el `GROUP BY` del gestor.

Calcular las compras conjuntas de un producto en cada petición es un autojoin de las
compras: minutos sobre `shop_compras` (el planificador no lleva el filtro por debajo del
`JOIN`, así que reúne 7,6 millones de filas) y segundos sobre `shop_historial` (el lado
izquierdo entra por su B+, el derecho se recorre entero; cifras en el README). Ninguna cabe
en una petición. El
gestor no tiene `INSERT ... SELECT` ni `CREATE TABLE ... AS`, y una respuesta trae como mucho
`max_result_rows` filas, así que los pares se calculan por lotes de productos: cada lote es
una consulta `GROUP BY` del gestor cuyo resultado va directo a la carga masiva de la tabla.
Si un lote no cabe en una respuesta, se parte en dos (y así se sabe cuántas filas caben);
si cupo con holgura, el siguiente es el doble de grande.

Al terminar se anota en `shop_materializaciones` con qué partición y soporte se calculó: una
carga repetida con los mismos no la vuelve a calcular, y una cortada o con otros parámetros
la rehace desde cero.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from minigestor_client import AsyncClient, Record
from shop.catalog.schema import COPURCHASES, COPURCHASES_INDEX_STATEMENT, COPURCHASES_TABLE
from shop.recommend import queries

from .report import CopurchaseStep

Clock = Callable[[], float]
GROWTH_FACTOR = 2
# Un lote que llenó menos de esta fracción de una respuesta deja crecer al siguiente.
GROW_BELOW_SHARE = 0.5

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CopurchaseSettings:
    """Partición de las compras, clientes mínimos por par y productos del primer lote."""

    split: str
    min_support: int
    chunk_products: int


async def materialize_copurchases(
    engine: AsyncClient,
    products: Sequence[str],
    settings: CopurchaseSettings,
    clock: Clock = time.perf_counter,
) -> CopurchaseStep:
    """Calcula los pares de `products` y los carga en `shop_compras_conjuntas`, salvo que ya
    estén calculados con la misma partición y soporte.

    Raises:
        minigestor_client.MinigestorError: si el gestor rechaza una consulta o la carga.
    """
    done = await engine.fetch_one(queries.MATERIALIZATION_OF, [COPURCHASES])
    if done is not None and _same(done, settings):
        logger.info("%s ya está calculada (%d pares)", COPURCHASES, done["filas"])
        return CopurchaseStep(int(done["filas"]), 0, 0.0, skipped=True)
    started = clock()
    await _recreate(engine)
    pending = sorted(products)
    size = settings.chunk_products
    rows = chunks = 0
    capacity: int | None = None
    while pending:
        chunk = pending[:size]
        result = await engine.query(
            queries.copurchase_pairs(len(chunk)),
            [settings.split, settings.min_support, *chunk],
        )
        chunks += 1
        if result.truncated and len(chunk) > 1:
            capacity = len(result.rows)
            size = max(1, len(chunk) // GROWTH_FACTOR)
            continue
        found = result.records()
        if found:
            await engine.bulk_insert(COPURCHASES, [_pair(row, settings.split) for row in found])
        rows += len(found)
        pending = pending[len(chunk) :]
        if capacity is None or len(found) < capacity * GROW_BELOW_SHARE:
            size *= GROWTH_FACTOR
        logger.info("%s: %d pares, faltan %d productos", COPURCHASES, rows, len(pending))
    await engine.execute(
        queries.RECORD_MATERIALIZATION,
        [COPURCHASES, settings.split, settings.min_support, rows],
    )
    seconds = clock() - started
    logger.info("%s: %d pares en %d consultas, %.1f s", COPURCHASES, rows, chunks, seconds)
    return CopurchaseStep(rows, chunks, seconds, skipped=False)


async def _recreate(engine: AsyncClient) -> None:
    await engine.execute(queries.FORGET_MATERIALIZATION, [COPURCHASES])
    await engine.execute(queries.DROP_COPURCHASES)
    await engine.execute(COPURCHASES_TABLE, idempotent=True)
    await engine.execute(COPURCHASES_INDEX_STATEMENT, idempotent=True)


def _same(done: Record, settings: CopurchaseSettings) -> bool:
    return (done["particion"], done["soporte"]) == (settings.split, settings.min_support)


def _pair(row: Record, split: str) -> Record:
    return {
        "producto": row["producto"],
        "otro": row["otro"],
        "particion": split,
        "veces": row["veces"],
    }
