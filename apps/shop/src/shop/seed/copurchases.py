"""Materializa `shop_compras_conjuntas` con el `GROUP BY` del gestor.

Calcular las compras conjuntas de un producto en cada petición es un autojoin de las
compras: minutos sobre `shop_compras` (el planificador no lleva el filtro por debajo del
`JOIN`, así que reúne 7,6 millones de filas) y segundos sobre `shop_historial` (el lado
izquierdo entra por su B+, el derecho se recorre entero; cifras en el README). Ninguna cabe
en una petición. El gestor no tiene `INSERT ... SELECT` ni `CREATE TABLE ... AS`, y una
respuesta trae como mucho `max_result_rows` filas, así que los pares se calculan por lotes de
productos: cada lote es una consulta `GROUP BY` del gestor cuyo resultado va directo a la
carga masiva de la tabla.

Los lotes se arman por **compradores**, no por productos: lo que crece con el lote es el
`JOIN` (cada compra del lote se reúne con todas las de su cliente), y un producto con 1 900
compradores pesa como cien de los comunes. Un producto que pasa del presupuesto va solo. Si
un lote no cabe en una respuesta o pasa del plazo de sentencia del gestor, se parte en dos y
se repite; un producto solo que pasa del plazo detiene la carga con un error que lo nombra.

De cada producto se guardan sus `max_partners` pares con más clientes, que es todo lo que el
recomendador pide; la consulta los ordena así, de modo que un producto solo que no cabe en
una respuesta conserva los mejores.

Al terminar se anota en `shop_materializaciones` con qué partición, soporte y pares por
producto se calculó: una
carga repetida con los mismos no la vuelve a calcular, y una cortada o con otros parámetros
la rehace desde cero.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from minigestor_client import AsyncClient, QueryResult, Record, StatementTimeoutError
from shop.catalog.rows import Row
from shop.catalog.schema import COPURCHASES, COPURCHASES_INDEX_STATEMENT, COPURCHASES_TABLE
from shop.recommend import queries

from .report import CopurchaseStep

Clock = Callable[[], float]
Chunk = tuple[str, ...]
HALVES = 2

logger = logging.getLogger(__name__)


class CopurchaseError(Exception):
    """Un producto solo no cabe en el plazo de sentencia del gestor."""


@dataclass(frozen=True, slots=True)
class CopurchaseSettings:
    """Partición de las compras, clientes mínimos por par, pares que se guardan por producto
    y compradores que suma, como mucho, un lote."""

    split: str
    min_support: int
    max_partners: int
    chunk_buyers: int


def count_buyers(history: Iterable[Row], split: str) -> Counter[str]:
    """Compras de cada producto en la partición: la estimación con que se arman los lotes."""
    return Counter(str(row["producto"]) for row in history if row["particion"] == split)


def plan_chunks(buyers: Mapping[str, int], budget: int) -> list[Chunk]:
    """Los productos en orden de id, en lotes que suman como mucho `budget` compradores; un
    producto que solo ya pasa del presupuesto va en su propio lote."""
    chunks: list[Chunk] = []
    current: list[str] = []
    weight = 0
    for product in sorted(buyers):
        if current and weight + buyers[product] > budget:
            chunks.append(tuple(current))
            current, weight = [], 0
        current.append(product)
        weight += buyers[product]
    if current:
        chunks.append(tuple(current))
    return chunks


async def materialize_copurchases(
    engine: AsyncClient,
    buyers: Mapping[str, int],
    settings: CopurchaseSettings,
    clock: Clock = time.perf_counter,
) -> CopurchaseStep:
    """Calcula los pares de los productos de `buyers` y los carga en
    `shop_compras_conjuntas`, salvo que ya estén calculados con los mismos parámetros.

    Raises:
        CopurchaseError: si un producto solo pasa del plazo de sentencia del gestor.
        minigestor_client.MinigestorError: si el gestor rechaza una consulta o la carga.
    """
    done = await engine.fetch_one(queries.MATERIALIZATION_OF, [COPURCHASES])
    if done is not None and _same(done, settings):
        logger.info("%s ya está calculada (%d pares)", COPURCHASES, done["filas"])
        return CopurchaseStep(int(done["filas"]), 0, 0.0, skipped=True)
    started = clock()
    await _recreate(engine)
    pending = deque(plan_chunks(buyers, settings.chunk_buyers))
    rows = queries_run = 0
    while pending:
        chunk = pending.popleft()
        queries_run += 1
        chunk_started = clock()
        result = await _pairs_of(engine, chunk, settings)
        if result is None or (result.truncated and len(chunk) > 1):
            pending.extendleft(reversed(_halves(chunk)))
            continue
        found = _strongest(result.records(), settings.max_partners)
        if found:
            await engine.bulk_insert(COPURCHASES, [_pair(row, settings.split) for row in found])
        rows += len(found)
        logger.info(
            "%s: lote %s…%s (%d productos, %d compras), %d pares en %.1f s; faltan %d lotes",
            COPURCHASES,
            chunk[0],
            chunk[-1],
            len(chunk),
            sum(buyers[product] for product in chunk),
            len(found),
            clock() - chunk_started,
            len(pending),
        )
    await engine.execute(
        queries.RECORD_MATERIALIZATION,
        [COPURCHASES, settings.split, settings.min_support, settings.max_partners, rows],
    )
    seconds = clock() - started
    logger.info("%s: %d pares en %d consultas, %.1f s", COPURCHASES, rows, queries_run, seconds)
    return CopurchaseStep(rows, queries_run, seconds, skipped=False)


async def _pairs_of(
    engine: AsyncClient, chunk: Chunk, settings: CopurchaseSettings
) -> QueryResult | None:
    """El `GROUP BY` del lote; `None` si pasó del plazo y hay que partirlo.

    Raises:
        CopurchaseError: si pasó del plazo con un solo producto.
    """
    try:
        return await engine.query(
            queries.copurchase_pairs(len(chunk)),
            [settings.split, settings.min_support, *chunk],
        )
    except StatementTimeoutError as error:
        if len(chunk) == 1:
            raise CopurchaseError(
                f"los pares de {chunk[0]} pasan del plazo de sentencia del gestor ({error}): "
                "sube MINIGESTOR_STATEMENT_TIMEOUT"
            ) from error
        logger.warning("un lote de %d productos pasó del plazo: se parte", len(chunk))
        return None


def _halves(chunk: Chunk) -> list[Chunk]:
    middle = len(chunk) // HALVES
    return [chunk[:middle], chunk[middle:]]


def _strongest(found: list[Record], limit: int) -> list[Record]:
    """Los `limit` primeros pares de cada producto; la consulta ya los ordena por clientes."""
    kept: Counter[str] = Counter()
    strongest = []
    for row in found:
        product = str(row["producto"])
        if kept[product] < limit:
            kept[product] += 1
            strongest.append(row)
    return strongest


async def _recreate(engine: AsyncClient) -> None:
    await engine.execute(queries.FORGET_MATERIALIZATION, [COPURCHASES])
    await engine.execute(queries.DROP_COPURCHASES)
    await engine.execute(COPURCHASES_TABLE, idempotent=True)
    await engine.execute(COPURCHASES_INDEX_STATEMENT, idempotent=True)


def _same(done: Record, settings: CopurchaseSettings) -> bool:
    recorded = (done["particion"], done["soporte"], done["por_producto"])
    return recorded == (settings.split, settings.min_support, settings.max_partners)


def _pair(row: Record, split: str) -> Record:
    return {
        "producto": row["producto"],
        "otro": row["otro"],
        "particion": split,
        "veces": row["veces"],
    }
