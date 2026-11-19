"""Lecturas del catálogo: ficha, búsqueda de texto, fotos por texto y tiendas cercanas.

Cada una es una consulta del gestor: `MATCH` sobre el invertido en inglés, `SIMILAR_TO`
sobre el HNSW de CLIP y `distancia` con el R-Tree. Recomendar es de #81.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from appkit import BadRequestError, Engine, ResourceNotFoundError, ServiceError, timed
from minigestor_client import Point, Record
from shop.catalog.queries import CLIP_SEARCH, fetch_products
from shop.clip.encoder import Embeddings
from shop.settings import ShopSettings

from . import queries
from .models import NearbyStore, Product, ProductSummary, ScoredProduct

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


@router.get("/productos/{product_id}")
async def product(product_id: str, engine: Engine) -> Product:
    with timed(ENGINE_STAGE):
        found = await engine.fetch_one(queries.PRODUCT_BY_ID, [product_id])
    if found is None:
        raise ResourceNotFoundError(f"no hay un producto {product_id}")
    return Product.model_validate(found)


@router.get("/busqueda/texto")
async def text_search(
    q: SearchText, engine: Engine, settings: Settings, limite: Limit = None
) -> list[ScoredProduct]:
    """Productos cuyo título o descripción contienen las palabras de `q`, por BM25."""
    with timed(ENGINE_STAGE):
        found = await engine.fetch_all(queries.TEXT_SEARCH, [q, _limit(limite, settings)])
    return [_scored(row, row["puntaje"]) for row in found]


@router.get("/busqueda/imagenes")
async def image_search(
    q: SearchText, engine: Engine, settings: Settings, encoder: TextEncoder, limite: Limit = None
) -> list[ScoredProduct]:
    """Productos cuya foto se parece a lo que describe `q`, con CLIP: el embedding del texto
    contra el HNSW de los embeddings de las fotos."""
    with timed(CLIP_STAGE):
        (vector,) = await run_in_threadpool(encoder.embed, [q])
    with timed(ENGINE_STAGE):
        neighbours = await engine.fetch_all(
            CLIP_SEARCH, [vector, _limit(limite, settings)]
        )
        products = await fetch_products(engine, [str(row["id"]) for row in neighbours])
    return [
        _scored(products[str(row["id"])], row["puntaje"])
        for row in neighbours
        if str(row["id"]) in products
    ]


@router.get("/tiendas/cercanas")
async def nearby_stores(
    lat: Annotated[float, Query(ge=-LATITUDE_LIMIT, le=LATITUDE_LIMIT)],
    lon: Annotated[float, Query(ge=-LONGITUDE_LIMIT, le=LONGITUDE_LIMIT)],
    engine: Engine,
    settings: Settings,
    limite: Limit = None,
) -> list[NearbyStore]:
    """Las tiendas más cercanas a `(lat, lon)`, con su distancia en metros (Haversine)."""
    with timed(ENGINE_STAGE):
        found = await engine.fetch_all(
            queries.NEAREST_STORES, [Point(lat, lon), _limit(limite, settings)]
        )
    return [NearbyStore.model_validate(row) for row in found]


def _scored(row: Record, score: object) -> ScoredProduct:
    if not isinstance(score, int | float):
        raise TypeError(f"el gestor devolvió un puntaje que no es un número: {score!r}")
    return ScoredProduct(producto=ProductSummary.model_validate(row), puntaje=float(score))


def _limit(requested: int | None, settings: ShopSettings) -> int:
    if requested is None:
        return settings.search_limit
    if requested > settings.max_search_limit:
        raise BadRequestError(
            f"se piden {requested} resultados y el máximo es {settings.max_search_limit}"
        )
    return requested
