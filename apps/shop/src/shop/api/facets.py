"""Listas que no dependen de la petición: los departamentos del catálogo, para filtrar, y los
usuarios que se ofrecen para «recomendado para ti».

Las dos recorren una tabla entera en el gestor (un `GROUP BY` sobre 24 587 fichas y un orden
sobre 57 439 usuarios), unos segundos cada una, y solo cambian con una carga nueva: cada
proceso del API las pide la primera vez que alguien las necesita y las recuerda. Una lista
vacía (el catálogo aún sin cargar) no se recuerda.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Generic, TypeVar

from fastapi import APIRouter, Depends, Request, Response

from appkit import Engine, timed
from minigestor_client import AsyncClient, Record

from . import queries
from .models import Customer, Department
from .routes import ENGINE_STAGE, Settings

# Lo que guarda el navegador: la lista no cambia sin una carga nueva.
FACET_CACHE_SECONDS = 3600

Item = TypeVar("Item")
router = APIRouter()


class Remembered(Generic[Item]):
    """Una lista que se pide una vez por proceso, aunque la pidan varias peticiones a la vez."""

    def __init__(self) -> None:
        self._items: list[Item] | None = None
        self._lock = asyncio.Lock()

    async def get(self, load: Callable[[], Awaitable[list[Item]]]) -> list[Item]:
        async with self._lock:
            if self._items is None:
                loaded = await load()
                self._items = loaded or None
                return loaded
            return self._items


class Facets:
    """Lo que el API recuerda del catálogo."""

    def __init__(self) -> None:
        self.departments: Remembered[Department] = Remembered()
        self.customers: Remembered[Customer] = Remembered()


def facets_of(request: Request) -> Facets:
    facets: Facets = request.app.state.facets
    return facets


FacetsOf = Annotated[Facets, Depends(facets_of)]


@router.get("/departamentos")
async def departments(
    engine: Engine, settings: Settings, facets: FacetsOf, response: Response
) -> list[Department]:
    """Los departamentos con al menos `SHOP_DEPARTMENT_MIN_PRODUCTS` productos, del que más
    tiene al que menos."""

    async def load() -> list[Department]:
        rows = await _rows(engine, queries.DEPARTMENTS, [settings.department_min_products])
        return [Department.model_validate(row) for row in rows]

    _cache_for_browsers(response)
    return await facets.departments.get(load)


@router.get("/usuarios")
async def featured_customers(
    engine: Engine, settings: Settings, facets: FacetsOf, response: Response
) -> list[Customer]:
    """Los `SHOP_FEATURED_USERS` usuarios con más compras, con cuántas tenían al cargar."""

    async def load() -> list[Customer]:
        rows = await _rows(engine, queries.FEATURED_USERS, [settings.featured_users])
        return [Customer.model_validate(row) for row in rows]

    _cache_for_browsers(response)
    return await facets.customers.get(load)


async def _rows(engine: AsyncClient, sql: str, parameters: list[object]) -> list[Record]:
    with timed(ENGINE_STAGE):
        return await engine.fetch_all(sql, parameters)


def _cache_for_browsers(response: Response) -> None:
    response.headers["Cache-Control"] = f"public, max-age={FACET_CACHE_SECONDS}"
