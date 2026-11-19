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

from appkit import configure_logging, create_service_app, load_settings
from faces.api.analysis import AnalysisPool
from faces.api.enrollments import EnrollmentRules, EnrollmentService
from faces.api.gallery import Gallery
from faces.api.identification import IdentificationService
from faces.api.people import PeopleService, purge_periodically
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
) -> FastAPI:
    """El API de rostros. Sin argumentos lee `FACES_*` del entorno y configura los logs; los
    argumentos son para los tests y los benchmarks: otro transporte hacia el gestor, otro
    reloj y otros umbrales de calidad.

    Raises:
        appkit.SettingsError: si falta o no vale una variable `FACES_*`.
    """
    if settings is None:
        settings = load_settings(FacesSettings)
        configure_logging(settings.log_level)
    quality_config = quality or QualityConfig()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.faces = _services(settings, FaceStore(app.state.engine), quality_config, clock)
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
    )
    app.include_router(router)
    return app


def _services(
    settings: FacesSettings, store: FaceStore, quality: QualityConfig, clock: Clock
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
        max_upload_bytes=settings.max_upload_bytes,
    )
