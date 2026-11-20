"""El API de la tienda (sin estado): `uvicorn --factory shop.api:create_app`.

    SHOP_ENGINE_URL=http://engine:8000 SHOP_ENGINE_KEY=... SHOP_MODELS_DIR=/models \\
    uvicorn --factory shop.api:create_app --host 0.0.0.0 --port 8000

Al arrancar aplica las migraciones del catálogo (`shop.catalog.schema.SCHEMA`) y carga las
dos torres de CLIP (la de texto para buscar fotos con palabras, la de imagen para buscar con
una foto); las rutas están en `routes.py`, `search.py`, `photos.py`, `facets.py` y
`recommendations.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from appkit import (
    OptionalRedis,
    RedisFactory,
    configure_logging,
    create_service_app,
    load_settings,
    redis_factory,
)
from shop.catalog.schema import SCHEMA
from shop.clip import ClipImageEncoder, ClipTextEncoder, ClipTokenizer, locate_models
from shop.recommend.cache import ResponseCache
from shop.settings import ShopSettings

from .facets import Facets
from .facets import router as facets_router
from .photos import PixelEmbedder
from .photos import router as photos_router
from .recommendations import router as recommendations_router
from .routes import TextEmbedder, router
from .search import RememberedVocabulary
from .search import router as search_router

NAME = "shop"
VERSION = "0.1.0"


def create_app(
    settings: ShopSettings | None = None,
    *,
    text_encoder: TextEmbedder | None = None,
    image_encoder: PixelEmbedder | None = None,
    engine_transport: httpx.AsyncBaseTransport | None = None,
    redis: RedisFactory | None = None,
) -> FastAPI:
    """La aplicación de la tienda. Sin argumentos lee la configuración del entorno, que es
    como la arranca uvicorn; `text_encoder`, `image_encoder`, `engine_transport` y `redis`
    (otro Redis en lugar del de `SHOP_REDIS_URL`) son para los tests y los benchmarks.

    Raises:
        appkit.SettingsError: si falta o no vale una variable `SHOP_*`.
        shop.clip.ModelNotFoundError: al arrancar, si faltan los modelos de CLIP.
    """
    if settings is None:
        settings = load_settings(ShopSettings)
        configure_logging(settings.log_level)
    configured = settings
    cache = OptionalRedis(redis or _redis_of(settings), settings.cache_retry_seconds)

    @asynccontextmanager
    async def load_encoders(app: FastAPI) -> AsyncIterator[None]:
        app.state.text_encoder = text_encoder or _clip_text_encoder(configured)
        app.state.image_encoder = image_encoder or _clip_image_encoder(configured)
        async with cache.connected():
            app.state.cache = ResponseCache(
                cache, configured.cache_ttl_seconds, _cache_version(configured)
            )
            yield

    app = create_service_app(
        settings,
        name=NAME,
        version=VERSION,
        migrations=SCHEMA,
        lifespan=load_encoders,
        engine_transport=engine_transport,
        degradables=[cache],
    )
    app.state.settings = settings
    app.state.facets = Facets()
    app.state.vocabulary = RememberedVocabulary(settings)
    app.include_router(router)
    app.include_router(search_router)
    app.include_router(photos_router)
    app.include_router(facets_router)
    app.include_router(recommendations_router)
    return app


def _redis_of(settings: ShopSettings) -> RedisFactory | None:
    if settings.redis_url is None:
        return None
    return redis_factory(str(settings.redis_url), settings.redis_timeout_seconds)


def _cache_version(settings: ShopSettings) -> str:
    """Los pesos y lo demás que cambia una recomendación: con otra configuración, otras
    claves."""
    return "-".join(
        (
            settings.fusion_weights().version(),
            settings.recommendation_split,
            str(settings.candidates_per_signal),
            str(settings.user_profile_products),
        )
    )


def _clip_image_encoder(settings: ShopSettings) -> ClipImageEncoder:
    return ClipImageEncoder(locate_models(settings.models_dir).vision, settings.clip_threads)


def _clip_text_encoder(settings: ShopSettings) -> ClipTextEncoder:
    models = locate_models(settings.models_dir)
    return ClipTextEncoder(models.text, ClipTokenizer(models.tokenizer), settings.clip_threads)
