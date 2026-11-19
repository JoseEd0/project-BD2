"""La aplicación del servicio de rostros.

uvicorn --factory faces.api:create_app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable

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
from faces.api.analysis import AnalysisPool
from faces.api.enrollments import EnrollmentRules, EnrollmentService
from faces.api.gallery import Gallery
from faces.api.identification import IdentificationService
from faces.api.people import PeopleService, purge_periodically
from faces.api.realtime.connection import ConnectionSlots, StreamLimits, StreamServices
from faces.api.realtime.recognizer import FrameLimits, FrameRecognizer
from faces.api.realtime.routes import router as realtime_router
from faces.api.realtime.tracks import TrackStore
from faces.api.routes import FacesServices, router
from faces.api.settings import FacesSettings
from faces.api.store import SCHEMA, FaceStore
from faces.consent import consent_text
from faces.models import locate_models
from faces.pipeline import FacePipeline
from faces.quality import QualityConfig

NAME = "faces"
VERSION = "0.1.0"

Clock = Callable[[], float]


def create_app(
    settings: FacesSettings | None = None,
    *,
    engine_transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock = time.time,
    quality: QualityConfig | None = None,
    redis: RedisFactory | None = None,
) -> FastAPI:
    """El API de rostros. Sin argumentos lee `FACES_*` del entorno y configura los logs; los
    argumentos son para los tests y los benchmarks: otro transporte hacia el gestor, otro
    reloj, otros umbrales de calidad y otro Redis (si no, el de `FACES_REDIS_URL`).

    Raises:
        appkit.SettingsError: si falta o no vale una variable `FACES_*`.
    """
    if settings is None:
        settings = load_settings(FacesSettings)
        configure_logging(settings.log_level)
    quality_config = quality or QualityConfig()
    cache = OptionalRedis(redis or _redis_of(settings), settings.redis_retry_seconds)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with cache.connected():
            store = FaceStore(app.state.engine)
            app.state.faces = _services(settings, store, cache, quality_config, clock)
            purge = asyncio.create_task(
                purge_periodically(app.state.faces.people, settings.purge_interval_seconds)
            )
            try:
                yield
            finally:
                purge.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await purge

    app = create_service_app(
        settings,
        name=NAME,
        version=VERSION,
        migrations=SCHEMA,
        lifespan=lifespan,
        engine_transport=engine_transport,
        degradables=[cache],
    )
    app.include_router(router)
    app.include_router(realtime_router)
    return app


def _redis_of(settings: FacesSettings) -> RedisFactory | None:
    if settings.redis_url is None:
        return None
    return redis_factory(settings.redis_url, settings.redis_timeout_seconds)


def _services(
    settings: FacesSettings,
    store: FaceStore,
    cache: OptionalRedis,
    quality: QualityConfig,
    clock: Clock,
) -> FacesServices:
    models = locate_models(settings.models_dir)
    pool = AnalysisPool(
        [FacePipeline(models, settings.pipeline()) for _ in range(settings.analysis_workers)]
    )
    gallery = Gallery(store.gallery, settings.thresholds(), settings.search_neighbours)
    consent = consent_text(settings.retention_days)
    rules = EnrollmentRules(
        consent=consent,
        plan=settings.pose_plan(),
        quality=quality,
        thresholds=settings.thresholds(),
        session_seconds=settings.enrollment_ttl_seconds,
        retention_seconds=settings.retention_seconds,
        max_attempts=settings.max_shot_attempts,
    )
    return FacesServices(
        consent=consent,
        enrollments=EnrollmentService(rules, store, gallery, pool, clock),
        identification=IdentificationService(
            gallery, pool, quality, settings.max_query_faces, clock
        ),
        people=PeopleService(store, clock),
        stream=_stream_services(settings, pool, gallery, cache, quality, clock),
        max_upload_bytes=settings.max_upload_bytes,
    )


def _stream_services(
    settings: FacesSettings,
    pool: AnalysisPool,
    gallery: Gallery,
    cache: OptionalRedis,
    quality: QualityConfig,
    clock: Clock,
) -> StreamServices:
    limits = StreamLimits(
        max_frame_bytes=settings.stream_max_frame_bytes,
        max_frame_pixels=settings.stream_max_frame_pixels,
        max_fps=settings.stream_max_fps,
        idle_timeout_seconds=settings.stream_idle_timeout_seconds,
        max_connections=settings.stream_max_connections,
    )
    recognizer = FrameRecognizer(
        pool,
        gallery,
        TrackStore(cache, settings.track_session_ttl_seconds),
        settings.tracking(),
        FrameLimits(
            max_pixels=settings.stream_max_frame_pixels,
            max_faces=settings.max_query_faces,
            min_face_width=quality.min_face_width,
        ),
        clock,
    )
    return StreamServices(recognizer, limits, ConnectionSlots(limits.max_connections))
