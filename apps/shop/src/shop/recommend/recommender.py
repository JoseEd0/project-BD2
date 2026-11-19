"""Recomendar a partir de un producto o de las compras de un usuario.

Las tres señales que proponen candidatos —compras conjuntas, parecido visual y texto— van al
gestor a la vez; la cercanía, si se pide, después y sobre la unión de sus candidatos. Cada
una pide, además de los que hacen falta, tantos como productos se van a excluir, para que
excluir no deje a la señal corta.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass

from appkit import ResourceNotFoundError
from minigestor_client import AsyncClient, Point, Record
from shop.catalog.queries import fetch_products

from . import queries, sources
from .candidates import SIFT_METHOD, Signal, SignalRun, VisualMethod
from .explanations import Scope
from .fusion import Fusion, FusionWeights, fuse


class UnknownProductError(ResourceNotFoundError):
    """Se pidió recomendar a partir de un producto que no está en el catálogo."""


class UnknownUserError(ResourceNotFoundError):
    """Se pidió recomendar a un usuario que no existe."""


@dataclass(frozen=True, slots=True)
class Nearby:
    """Dónde está quien pregunta y hasta qué distancia cuenta una tienda."""

    location: Point
    radius_meters: float


@dataclass(frozen=True, slots=True)
class RecommendationRequest:
    """Lo que se pide: cuántas recomendaciones, con qué parecido visual, desde dónde, y
    con qué pesos."""

    limit: int
    visual: VisualMethod
    weights: FusionWeights
    nearby: Nearby | None = None


@dataclass(frozen=True, slots=True)
class Recommendations:
    """El resultado: las consultas de cada señal, la fusión y las fichas de los productos
    recomendados (y de los de partida, para nombrarlos)."""

    scope: Scope
    runs: tuple[SignalRun, ...]
    fusion: Fusion
    products: dict[str, Record]


@dataclass(frozen=True, slots=True)
class _Origin:
    products: tuple[str, ...]
    titles: tuple[str, ...]
    photo: str | None


class Recommender:
    """Las recomendaciones de la tienda sobre el gestor.

    `split` es la partición de las compras que las alimenta; `candidates` cuántos pide cada
    señal; `profile_products` cuántas compras recientes describen a un usuario.
    """

    def __init__(
        self, engine: AsyncClient, split: str, candidates: int, profile_products: int
    ) -> None:
        self._engine = engine
        self._split = split
        self._candidates = candidates
        self._profile_products = profile_products

    async def for_product(
        self, product: str, request: RecommendationRequest, user: str | None = None
    ) -> Recommendations:
        """Productos para quien mira `product`; sin los que ya compró `user`, si se da.

        Raises:
            UnknownProductError: si `product` no existe.
            UnknownUserError: si se da `user` y no existe.
        """
        found = await self._engine.fetch_one(queries.PRODUCT_TO_RECOMMEND_FROM, [product])
        if found is None:
            raise UnknownProductError(f"no hay un producto {product}")
        bought = await self._history(user) if user is not None else []
        origin = _Origin((product,), _titles([found]), found["imagen"])
        excluded = {product, *bought}
        wanted = self._wanted(request, excluded)
        copurchases = sources.copurchases_of_product(self._engine, product, self._split, wanted)
        return await self._recommend(Scope.PRODUCT, origin, copurchases, excluded, request)

    async def for_user(self, user: str, request: RecommendationRequest) -> Recommendations:
        """Productos para `user` a partir de sus compras recientes, sin las que ya hizo.

        Raises:
            UnknownUserError: si `user` no existe.
        """
        history = await self._history(user)
        if not history:
            return Recommendations(Scope.USER, (), fuse({}, request.weights, (), 0), {})
        profile = history[: self._profile_products]
        found = await self._engine.fetch_all(
            queries.products_to_recommend_from(len(profile)), profile
        )
        by_id = {str(row["id"]): row for row in found}
        ordered = [by_id[product] for product in profile if product in by_id]
        photos = [row["imagen"] for row in ordered if row["imagen"] is not None]
        origin = _Origin(tuple(profile), _titles(ordered), photos[0] if photos else None)
        excluded = set(history)
        wanted = self._wanted(request, excluded)
        copurchases = sources.copurchases_of_products(self._engine, profile, self._split, wanted)
        return await self._recommend(Scope.USER, origin, copurchases, excluded, request)

    async def _history(self, user: str) -> list[str]:
        return await user_history(self._engine, user, self._split)

    async def _recommend(
        self,
        scope: Scope,
        origin: _Origin,
        copurchases: Awaitable[SignalRun],
        excluded: set[str],
        request: RecommendationRequest,
    ) -> Recommendations:
        wanted = self._wanted(request, excluded)
        proposing = await asyncio.gather(
            copurchases,
            self._visual(origin, request.visual, wanted),
            sources.text_neighbours(self._engine, origin.titles, wanted),
        )
        runs = [run for run in proposing if run is not None]
        if request.nearby is not None:
            nearby = await self._nearby(runs, excluded, request.nearby)
            runs.extend([nearby] if nearby is not None else [])
        found = {run.signal: run.candidates for run in runs}
        fusion = fuse(found, request.weights, excluded, request.limit)
        products = await fetch_products(
            self._engine, [fused.product for fused in fusion.ranked]
        )
        return Recommendations(scope, tuple(runs), fusion, products)

    async def _visual(
        self, origin: _Origin, method: VisualMethod, wanted: int
    ) -> SignalRun | None:
        if method == SIFT_METHOD:
            return await sources.sift_neighbours(self._engine, origin.photo, wanted)
        return await sources.clip_neighbours(self._engine, origin.products, wanted)

    async def _nearby(
        self, runs: Sequence[SignalRun], excluded: set[str], nearby: Nearby
    ) -> SignalRun | None:
        proposed = sorted(
            {
                candidate.product
                for run in runs
                if run.signal is not Signal.NEARBY
                for candidate in run.candidates
                if candidate.product not in excluded
            }
        )
        return await sources.stock_nearby(
            self._engine, proposed, nearby.location, nearby.radius_meters
        )

    def _wanted(self, request: RecommendationRequest, excluded: set[str]) -> int:
        return max(self._candidates, request.limit) + len(excluded)


async def user_history(engine: AsyncClient, user: str, split: str) -> list[str]:
    """Los productos que compró `user` en la partición `split`, del más reciente al más
    antiguo y sin repetir.

    Raises:
        UnknownUserError: si `user` no existe.
    """
    known, purchases = await asyncio.gather(
        engine.fetch_one(queries.USER_BY_ID, [user]),
        engine.fetch_all(queries.USER_HISTORY, [user, split]),
    )
    if known is None:
        raise UnknownUserError(f"no hay un usuario {user}")
    newest_first = sorted(purchases, key=lambda row: (-int(row["instante"]), row["producto"]))
    return list(dict.fromkeys(str(row["producto"]) for row in newest_first))


def _titles(rows: Sequence[Record]) -> tuple[str, ...]:
    return tuple(str(row["titulo"]) for row in rows if row["titulo"])
