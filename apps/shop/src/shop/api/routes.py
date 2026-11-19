"""Lecturas del catálogo: ficha, búsqueda de texto, fotos por texto, tiendas cercanas y
dónde hay stock de un producto.

Cada una es una consulta del gestor: `MATCH` sobre el invertido en inglés, `SIMILAR_TO`
sobre el HNSW de CLIP y `distancia` con el R-Tree. Las búsquedas admiten el filtro de
departamento y precio (`filters.py`). Recomendar es de #81; las fotos, de `photos.py`.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from appkit import BadRequestError, Engine, ResourceNotFoundError, ServiceError, timed
from minigestor_client import AsyncClient, Point, Record
from shop.catalog.queries import CLIP_SEARCH, fetch_products
from shop.clip.encoder import Embeddings
from shop.settings import ShopSettings

from . import queries
from .filters import CatalogFilter, Filter
from .models import NearbyStore, Product, ProductSummary, ScoredProduct, StoreWithStock

QUERY_MAX_CHARACTERS = 200
LATITUDE_LIMIT = 90.0
LONGITUDE_LIMIT = 180.0
ENGINE_STAGE = "gestor"
CLIP_STAGE = "clip"


class TextEmbedder(Protocol):
    """La torre de texto de CLIP (`ClipTextEncoder`), o una de mentira en los tests."""

    def embed(self, texts: list[str]) -> Embeddings: ...


class TextEncoderNotLoadedError(ServiceError):
    """El servicio arrancó sin la torre de texto de CLIP."""


router = APIRouter()


def settings_of(request: Request) -> ShopSettings:
    settings: ShopSettings = request.app.state.settings
    return settings


def text_encoder_of(request: Request) -> TextEmbedder:
    encoder: TextEmbedder | None = getattr(request.app.state, "text_encoder", None)
    if encoder is None:
        raise TextEncoderNotLoadedError("el servicio no cargó la torre de texto de CLIP")
    return encoder


Settings = Annotated[ShopSettings, Depends(settings_of)]
TextEncoder = Annotated[TextEmbedder, Depends(text_encoder_of)]
SearchText = Annotated[str, Query(min_length=1, max_length=QUERY_MAX_CHARACTERS)]
Limit = Annotated[int | None, Query(ge=1)]
Latitude = Annotated[float, Query(ge=-LATITUDE_LIMIT, le=LATITUDE_LIMIT)]
Longitude = Annotated[float, Query(ge=-LONGITUDE_LIMIT, le=LONGITUDE_LIMIT)]


@router.get("/productos/{product_id}")
async def product(product_id: str, engine: Engine) -> Product:
    with timed(ENGINE_STAGE):
        found = await engine.fetch_one(queries.PRODUCT_BY_ID, [product_id])
    if found is None:
        raise ResourceNotFoundError(f"no hay un producto {product_id}")
    return Product.model_validate(found)


@router.get("/busqueda/texto")
async def text_search(
    q: SearchText, engine: Engine, settings: Settings, filters: Filter, limite: Limit = None
) -> list[ScoredProduct]:
    """Productos cuyo título o descripción contienen las palabras de `q`, por BM25, y que
    cumplen el filtro de departamento y precio."""
    conditions, values = filters.conditions(queries.TEXT_SEARCH_FIXED_PARAMETERS + 1)
    with timed(ENGINE_STAGE):
        found = await engine.fetch_all(
            queries.text_search(conditions), [q, search_limit(limite, settings), *values]
        )
    return [_scored(row, row["puntaje"]) for row in found]


@router.get("/busqueda/imagenes")
async def image_search(
    q: SearchText,
    engine: Engine,
    settings: Settings,
    encoder: TextEncoder,
    filters: Filter,
    limite: Limit = None,
) -> list[ScoredProduct]:
    """Productos cuya foto se parece a lo que describe `q`, con CLIP: el embedding del texto
    contra el HNSW de los embeddings de las fotos."""
    with timed(CLIP_STAGE):
        (vector,) = await run_in_threadpool(encoder.embed, [q])
    return await clip_search(engine, settings, vector, search_limit(limite, settings), filters)


@router.get("/tiendas/cercanas")
async def nearby_stores(
    lat: Latitude, lon: Longitude, engine: Engine, settings: Settings, limite: Limit = None
) -> list[NearbyStore]:
    """Las tiendas más cercanas a `(lat, lon)`, con su distancia en metros (Haversine)."""
    with timed(ENGINE_STAGE):
        found = await engine.fetch_all(
            queries.NEAREST_STORES, [Point(lat, lon), search_limit(limite, settings)]
        )
    return [NearbyStore.model_validate(row) for row in found]


@router.get("/productos/{product_id}/tiendas")
async def stores_with_stock(
    product_id: str,
    lat: Latitude,
    lon: Longitude,
    engine: Engine,
    settings: Settings,
    limite: Limit = None,
) -> list[StoreWithStock]:
    """Las tiendas que tienen `product_id` en stock, de la más cercana a `(lat, lon)` a la
    más lejana, con sus unidades: el hash del inventario y la distancia del R-Tree."""
    with timed(ENGINE_STAGE):
        found = await engine.fetch_all(
            queries.STORES_WITH_STOCK, [product_id, Point(lat, lon), search_limit(limite, settings)]
        )
    return [StoreWithStock.model_validate(row) for row in found]


async def clip_search(
    engine: AsyncClient,
    settings: ShopSettings,
    vector: Embeddings,
    limit: int,
    filters: CatalogFilter,
) -> list[ScoredProduct]:
    """Los `limit` productos de foto más parecida a `vector` que cumplen `filters`.

    El HNSW de CLIP no conoce el departamento ni el precio (viven en `shop_productos`): con un
    filtro se piden `filtered_search_factor` veces más vecinos y el filtro va en el `WHERE`
    de las fichas, así que un filtro muy estrecho puede devolver menos de `limit`.
    """
    wanted = limit if filters.is_empty else limit * settings.filtered_search_factor
    with timed(ENGINE_STAGE):
        neighbours = await engine.fetch_all(CLIP_SEARCH, [vector, wanted])
        ids = [str(row["id"]) for row in neighbours]
        products = await _filtered_products(engine, ids, filters)
    ranked = [
        _scored(products[str(row["id"])], row["puntaje"])
        for row in neighbours
        if str(row["id"]) in products
    ]
    return ranked[:limit]


async def _filtered_products(
    engine: AsyncClient, ids: list[str], filters: CatalogFilter
) -> dict[str, Record]:
    if filters.is_empty:
        return await fetch_products(engine, ids)
    if not ids:
        return {}
    conditions, values = filters.conditions(len(ids) + 1)
    found = await engine.fetch_all(
        queries.filtered_products_by_ids(len(ids), conditions), [*ids, *values]
    )
    return {str(row["id"]): row for row in found}


def _scored(row: Record, score: object) -> ScoredProduct:
    if not isinstance(score, int | float):
        raise TypeError(f"el gestor devolvió un puntaje que no es un número: {score!r}")
    return ScoredProduct(producto=ProductSummary.model_validate(row), puntaje=float(score))


def search_limit(requested: int | None, settings: ShopSettings) -> int:
    if requested is None:
        return settings.search_limit
    if requested > settings.max_search_limit:
        raise BadRequestError(
            f"se piden {requested} resultados y el máximo es {settings.max_search_limit}"
        )
    return requested
