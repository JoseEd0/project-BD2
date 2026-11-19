"""Recomendaciones por producto y por usuario, y registrar compras (#81).

Cada respuesta trae el SQL exacto de cada señal. Se guarda en la caché de Redis con la
generación del producto y del usuario de que depende; una compra las invalida.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from appkit import BadRequestError, Engine, timed
from minigestor_client import Point, Record
from shop.catalog.queries import fetch_products
from shop.recommend.cache import (
    PRODUCT_ENTITY,
    USER_ENTITY,
    CacheEntry,
    CacheStatus,
    ResponseCache,
)
from shop.recommend.candidates import Signal, VisualMethod
from shop.recommend.explanations import REASON_SEPARATOR, Scope, reason
from shop.recommend.fusion import FusedProduct
from shop.recommend.purchases import record_purchase
from shop.recommend.recommender import (
    Nearby,
    RecommendationRequest,
    Recommendations,
    Recommender,
    user_history,
)
from shop.settings import ShopSettings

from . import models
from .routes import ENGINE_STAGE, LATITUDE_LIMIT, LONGITUDE_LIMIT, Settings, search_limit

CACHE_HEADER = "X-Cache"
CACHE_STAGE = "cache"
JSON_MEDIA_TYPE = "application/json"
MAX_RADIUS_METERS = 50_000.0
PRODUCT_ROUTE = "producto"
USER_ROUTE = "usuario"
SCORE_DIGITS = 6

router = APIRouter()


def cache_of(request: Request) -> ResponseCache:
    cache: ResponseCache = request.app.state.cache
    return cache


Cache = Annotated[ResponseCache, Depends(cache_of)]
Limit = Annotated[int | None, Query(ge=1)]
Latitude = Annotated[float | None, Query(ge=-LATITUDE_LIMIT, le=LATITUDE_LIMIT)]
Longitude = Annotated[float | None, Query(ge=-LONGITUDE_LIMIT, le=LONGITUDE_LIMIT)]
Radius = Annotated[float | None, Query(gt=0, le=MAX_RADIUS_METERS)]


@router.get("/productos/{product_id}/recomendaciones", response_model=models.Recommendations)
async def product_recommendations(
    product_id: str,
    engine: Engine,
    settings: Settings,
    cache: Cache,
    limite: Limit = None,
    lat: Latitude = None,
    lon: Longitude = None,
    radio: Radius = None,
    usuario: str | None = None,
    visual: VisualMethod | None = None,
) -> Response:
    """Productos para quien mira `product_id`: comprados junto con él, parecidos en la foto
    y en la descripción y, con `lat` y `lon`, en stock cerca. Con `usuario`, sin lo que ya
    compró."""
    request = _request(settings, limite, lat, lon, radio, visual)
    entities = [(PRODUCT_ENTITY, product_id)]
    entities.extend([(USER_ENTITY, usuario)] if usuario is not None else [])
    entry = CacheEntry(PRODUCT_ROUTE, tuple(entities), _parameters(request, usuario))

    async def compute() -> Recommendations:
        return await _recommender(engine, settings).for_product(product_id, request, usuario)

    return await _cached(cache, entry, compute, product_id, Scope.PRODUCT)


@router.get("/usuarios/{user_id}/recomendaciones", response_model=models.Recommendations)
async def user_recommendations(
    user_id: str,
    engine: Engine,
    settings: Settings,
    cache: Cache,
    limite: Limit = None,
    lat: Latitude = None,
    lon: Longitude = None,
    radio: Radius = None,
    visual: VisualMethod | None = None,
) -> Response:
    """Productos para `user_id` a partir de sus compras recientes de la partición de
    entrenamiento, sin las que ya hizo."""
    request = _request(settings, limite, lat, lon, radio, visual)
    entry = CacheEntry(USER_ROUTE, ((USER_ENTITY, user_id),), _parameters(request, None))

    async def compute() -> Recommendations:
        return await _recommender(engine, settings).for_user(user_id, request)

    return await _cached(cache, entry, compute, user_id, Scope.USER)


@router.get("/usuarios/{user_id}/compras")
async def purchases(
    user_id: str, engine: Engine, settings: Settings, limite: Limit = None
) -> list[models.ProductSummary]:
    """Lo que compró `user_id` en la partición del recomendador, de lo más reciente a lo más
    antiguo: lo que describe al usuario y lo que sus recomendaciones dejan fuera."""
    limit = search_limit(limite, settings)
    history = await user_history(engine, user_id, settings.recommendation_split)
    with timed(ENGINE_STAGE):
        products = await fetch_products(engine, history[:limit])
    return [
        models.ProductSummary.model_validate(products[product])
        for product in history[:limit]
        if product in products
    ]


@router.post("/usuarios/{user_id}/compras", status_code=201)
async def purchase(
    user_id: str,
    body: models.PurchaseRequest,
    engine: Engine,
    settings: Settings,
    cache: Cache,
) -> models.PurchaseRecorded:
    """Registra la compra en el gestor e invalida las recomendaciones del usuario, del
    producto y de los que ya había comprado, cuyos pares cambian."""
    split = settings.recommendation_split
    history = await user_history(engine, user_id, split)
    recorded = await record_purchase(
        engine, user_id, body.producto, split, history, settings.purchase_attempts
    )
    with timed(CACHE_STAGE):
        await cache.invalidate([body.producto, *history], [user_id])
    return models.PurchaseRecorded(
        id=recorded.id,
        usuario=recorded.user,
        producto=recorded.product,
        instante=recorded.instant_ms,
        particion=recorded.split,
        pares_sumados=recorded.pairs_incremented,
        pares_nuevos=recorded.pairs_added,
    )


async def _cached(
    cache: ResponseCache,
    entry: CacheEntry,
    compute: Callable[[], Awaitable[Recommendations]],
    origin: str,
    scope: Scope,
) -> Response:
    with timed(CACHE_STAGE):
        lookup = await cache.lookup(entry)
    if lookup.payload is not None:
        return _json(lookup.payload, lookup.status)
    result = await compute()
    payload = response_of(result, origin, scope).model_dump_json().encode()
    with timed(CACHE_STAGE):
        await cache.store(lookup, payload)
    return _json(payload, lookup.status)


def response_of(result: Recommendations, origin: str, scope: Scope) -> models.Recommendations:
    """La respuesta del API a partir de lo que calculó el recomendador."""
    weights = result.fusion.weights
    return models.Recommendations(
        origen=origin,
        arranque_en_frio=result.fusion.cold_start,
        pesos={signal.value: weights.of(signal) for signal in Signal},
        version_pesos=weights.version(),
        senales=[
            models.SignalQuery(
                senal=run.signal.value,
                sql=run.sql,
                parametros=list(run.parameters),
                candidatos=len(run.candidates),
                milisegundos=run.milliseconds,
            )
            for run in result.runs
        ],
        recomendaciones=[
            _recommendation(fused, result.products[fused.product], scope)
            for fused in result.fusion.ranked
            if fused.product in result.products
        ],
    )


def _recommendation(fused: FusedProduct, product: Record, scope: Scope) -> models.Recommendation:
    reasons = [
        models.Reason(
            senal=part.signal.value,
            aporte=round(part.weighted, SCORE_DIGITS),
            texto=reason(part, scope),
        )
        for part in fused.contributions
    ]
    return models.Recommendation(
        producto=models.ProductSummary.model_validate(product),
        puntaje=round(fused.score, SCORE_DIGITS),
        motivos=reasons,
        explicacion=REASON_SEPARATOR.join(item.texto for item in reasons),
    )


def _request(
    settings: ShopSettings,
    limit: int | None,
    lat: float | None,
    lon: float | None,
    radius: float | None,
    visual: VisualMethod | None,
) -> RecommendationRequest:
    chosen = settings.search_limit if limit is None else limit
    if chosen > settings.max_search_limit:
        raise BadRequestError(
            f"se piden {chosen} recomendaciones y el máximo es {settings.max_search_limit}"
        )
    if (lat is None) != (lon is None):
        raise BadRequestError("la ubicación necesita lat y lon")
    nearby = None
    if lat is not None and lon is not None:
        nearby = Nearby(Point(lat, lon), radius or settings.nearby_radius_meters)
    return RecommendationRequest(
        chosen, visual or settings.visual_signal, settings.fusion_weights(), nearby
    )


def _parameters(request: RecommendationRequest, user: str | None) -> dict[str, object]:
    nearby = request.nearby
    return {
        "limite": request.limit,
        "visual": request.visual,
        "usuario": user,
        "lat": None if nearby is None else nearby.location.lat,
        "lon": None if nearby is None else nearby.location.lon,
        "radio": None if nearby is None else nearby.radius_meters,
    }


def _recommender(engine: Engine, settings: ShopSettings) -> Recommender:
    return Recommender(
        engine,
        settings.recommendation_split,
        settings.candidates_per_signal,
        settings.user_profile_products,
    )


def _json(payload: bytes, status: CacheStatus) -> Response:
    return Response(payload, media_type=JSON_MEDIA_TYPE, headers={CACHE_HEADER: status.value})
