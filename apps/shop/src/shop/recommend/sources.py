"""Las señales contra el gestor: cada función corre la consulta de su señal y devuelve sus
candidatos con el SQL exacto, los parámetros y lo que tardó."""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from types import TracebackType

import numpy as np

from appkit import record_stage
from minigestor_client import AsyncClient, Point
from shop.catalog.queries import CLIP_SEARCH

from . import queries
from .candidates import Candidate, CopurchaseEvidence, Signal, SignalRun, StockEvidence

MILLISECONDS_PER_SECOND = 1000.0
# Paréntesis y AND/OR/NOT en mayúsculas harían de un título una consulta booleana de `MATCH`.
BOOLEAN_SYNTAX = re.compile(r"[()]")
TEXT_SEPARATOR = " "


def shown(value: object) -> object:
    """Un parámetro tal como se muestra en la respuesta: un vector, por su dimensión."""
    if isinstance(value, np.ndarray | list | tuple):
        return f"vector({len(value)})"
    if isinstance(value, Point):
        return {"lat": value.lat, "lon": value.lon}
    return value


def free_text(titles: Sequence[str]) -> str:
    """Los títulos como una consulta de texto libre para BM25."""
    joined = TEXT_SEPARATOR.join(titles).lower()
    return BOOLEAN_SYNTAX.sub(TEXT_SEPARATOR, joined)


async def copurchases_of_product(
    engine: AsyncClient, product: str, split: str, wanted: int
) -> SignalRun:
    """Los productos que más clientes compraron junto con `product`, de la tabla
    materializada."""
    parameters = (product, split, wanted)
    with Stopwatch(Signal.COPURCHASE) as watch:
        rows = await engine.fetch_all(queries.COPURCHASES_OF_PRODUCT, parameters)
    candidates = tuple(
        Candidate(
            str(row["otro"]),
            float(row["veces"]),
            CopurchaseEvidence(int(row["veces"]), 1),
        )
        for row in rows
    )
    return _run(Signal.COPURCHASE, queries.COPURCHASES_OF_PRODUCT, parameters, candidates, watch)


async def copurchases_of_products(
    engine: AsyncClient, products: Sequence[str], split: str, wanted: int
) -> SignalRun:
    """Los productos comprados junto con varios a la vez (las compras de un usuario), con
    los clientes sumados por candidato."""
    sql = queries.copurchases_of_products(len(products))
    parameters = (split, wanted, *products)
    with Stopwatch(Signal.COPURCHASE) as watch:
        rows = await engine.fetch_all(sql, parameters)
    candidates = tuple(
        Candidate(
            str(row["otro"]),
            float(row["clientes"]),
            CopurchaseEvidence(int(row["clientes"]), int(row["compras"])),
        )
        for row in rows
    )
    return _run(Signal.COPURCHASE, sql, parameters, candidates, watch)


async def clip_neighbours(
    engine: AsyncClient, products: Sequence[str], wanted: int
) -> SignalRun | None:
    """Los productos cuya foto más se parece, con CLIP: el embedding de la foto de partida, o
    la media de las de varias, contra el HNSW (coseno). `None` si ninguna tiene foto."""
    with Stopwatch(Signal.VISUAL) as watch:
        found = await engine.fetch_all(queries.clip_embeddings(len(products)), list(products))
        if not found:
            return None
        query = _mean_direction([row["embedding"] for row in found])
        rows = await engine.fetch_all(CLIP_SEARCH, [query, wanted])
    candidates = tuple(Candidate(str(row["id"]), float(row["puntaje"])) for row in rows)
    return _run(Signal.VISUAL, CLIP_SEARCH, (query, wanted), candidates, watch)


async def sift_neighbours(engine: AsyncClient, photo: str | None, wanted: int) -> SignalRun | None:
    """Los productos cuya foto más se parece con SIFT y la bolsa de palabras visuales de la
    Parte 4: el gestor extrae los descriptores de `photo` y busca en su HNSW."""
    if photo is None:
        return None
    parameters = (photo, wanted)
    with Stopwatch(Signal.VISUAL) as watch:
        rows = await engine.fetch_all(queries.SIFT_SEARCH, parameters)
    candidates = tuple(Candidate(str(row["id"]), float(row["puntaje"])) for row in rows)
    return _run(Signal.VISUAL, queries.SIFT_SEARCH, parameters, candidates, watch)


async def text_neighbours(
    engine: AsyncClient, titles: Sequence[str], wanted: int
) -> SignalRun | None:
    """Los productos de descripción más parecida a los títulos, por BM25 sobre el invertido
    en inglés. `None` si no hay título."""
    if not titles:
        return None
    parameters = (free_text(titles), wanted)
    with Stopwatch(Signal.TEXT) as watch:
        rows = await engine.fetch_all(queries.TEXT_NEIGHBOURS, parameters)
    candidates = tuple(Candidate(str(row["id"]), float(row["puntaje"])) for row in rows)
    return _run(Signal.TEXT, queries.TEXT_NEIGHBOURS, parameters, candidates, watch)


async def stock_nearby(
    engine: AsyncClient, products: Sequence[str], location: Point, radius_meters: float
) -> SignalRun | None:
    """De `products`, los que hay en una tienda a menos de `radius_meters` de `location`,
    con la más cercana; el puntaje es `1 - metros / radio`. `None` si no hay productos."""
    if not products:
        return None
    sql = queries.stock_nearby(len(products))
    parameters = (location, radius_meters, *products)
    with Stopwatch(Signal.NEARBY) as watch:
        rows = await engine.fetch_all(sql, parameters)
    nearest: dict[str, StockEvidence] = {}
    for row in rows:
        product = str(row["producto"])
        stock = StockEvidence(str(row["tienda"]), float(row["metros"]))
        current = nearest.get(product)
        if current is None or (stock.meters, stock.store) < (current.meters, current.store):
            nearest[product] = stock
    candidates = tuple(
        Candidate(product, 1.0 - stock.meters / radius_meters, stock)
        for product, stock in nearest.items()
    )
    return _run(Signal.NEARBY, sql, parameters, candidates, watch)


def _mean_direction(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    """La media de embeddings de norma 1, devuelta a norma 1: con el coseno, la dirección
    promedio de las fotos."""
    mean = np.mean(np.asarray(vectors, dtype=np.float32), axis=0)
    norm = float(np.linalg.norm(mean))
    return mean / norm if norm else mean


class Stopwatch:
    """Mide un bloque de una señal y lo anota en el `Server-Timing` de la petición con el
    nombre de la señal."""

    def __init__(self, signal: Signal) -> None:
        self.signal = signal
        self.milliseconds = 0.0
        self._started = 0.0

    def __enter__(self) -> Stopwatch:
        self._started = time.perf_counter()
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.milliseconds = (time.perf_counter() - self._started) * MILLISECONDS_PER_SECOND
        record_stage(self.signal.value, self.milliseconds)


def _run(
    signal: Signal,
    sql: str,
    parameters: Sequence[object],
    candidates: tuple[Candidate, ...],
    watch: Stopwatch,
) -> SignalRun:
    shown_parameters = tuple(shown(value) for value in parameters)
    return SignalRun(signal, sql, shown_parameters, candidates, watch.milliseconds)
