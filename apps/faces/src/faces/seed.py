"""Carga las personas públicas de LFW en `face_famosos`, para «¿A qué famoso te pareces?»:

    docker compose run --rm faces-seed

    FACES_ENGINE_URL=http://localhost:8000 FACES_ENGINE_KEY=… FACES_MODELS_DIR=data/models/faces \\
    FACES_DATA_DIR=data/datasets uv run python -m faces.seed

Lee las 13 233 fotos de `<FACES_DATA_DIR>/lfw/lfw` (la de `python -m faces lfw`), saca el
embedding del rostro del centro de cada una en `FACES_SEED_WORKERS` procesos y carga una
fila por persona con la carga masiva. La clave tiene que ser `admin` (`FACES_MIGRATION_KEY`
o, si no está, `FACES_ENGINE_KEY`): aplica las migraciones del servicio.

**Repetible.** Si la tabla ya tiene filas no hace nada. La carga va en una sola petición
masiva; si el gestor rechaza un lote, se borra lo cargado para que la siguiente ejecución
empiece de cero.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import httpx

from appkit import SettingsError, configure_logging, engine_client, load_settings, migrate
from faces.api.settings import FacesSeedSettings
from faces.api.store import SCHEMA
from faces.config import PipelineConfig
from faces.errors import FaceError
from faces.famous import (
    COUNT_FAMOUS,
    FAMOUS_TABLE,
    famous_people,
    lfw_images_directory,
    lfw_photo_embedding,
)
from faces.lfw import lfw_images
from faces.models import locate_models
from faces.pipeline import FacePipeline
from faces.recognition import Embedding
from minigestor_client import AsyncClient, MinigestorError

FAILURE_EXIT_CODE = 1
# Las fotos van a los procesos en tandas: una por foto pagaría más en mensajes que el pipeline.
PHOTOS_PER_TASK = 64
HTTP_LOGGER = "httpx"
DELETE_FAMOUS = f"DELETE FROM {FAMOUS_TABLE}"

logger = logging.getLogger(__name__)

_worker_pipeline: FacePipeline | None = None


class SeedError(Exception):
    """La carga no pudo terminar: faltan las fotos o el gestor rechazó filas."""


def main() -> int:
    try:
        settings = load_settings(FacesSeedSettings)
    except SettingsError as error:
        print(f"error: {error}", file=sys.stderr)
        return FAILURE_EXIT_CODE
    configure_logging(settings.log_level)
    logging.getLogger(HTTP_LOGGER).setLevel(logging.WARNING)
    try:
        asyncio.run(seed(settings))
    except (SeedError, MinigestorError) as error:
        logger.error("la carga falló: %s", error)
        return FAILURE_EXIT_CODE
    return 0


async def seed(
    settings: FacesSeedSettings, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    """Aplica las migraciones y carga `face_famosos` si está vacía. Devuelve las personas
    cargadas ahora (0 si ya estaban). `transport` reemplaza la red, para los tests.

    Raises:
        SeedError: si no están las fotos de LFW o el gestor rechaza filas.
    """
    key = settings.migration_key or settings.engine_key
    async with engine_client(settings, key, transport) as engine:
        await migrate(engine, SCHEMA)
        present = int(await engine.scalar(COUNT_FAMOUS) or 0)
        if present > 0:
            logger.info("%s ya tiene %d personas de LFW: no se carga nada", FAMOUS_TABLE, present)
            return 0
        return await _load(engine, settings)


async def _load(engine: AsyncClient, settings: FacesSeedSettings) -> int:
    images_directory = lfw_images_directory(settings.data_dir)
    images = lfw_images(images_directory)
    if not images:
        raise SeedError(
            f"no hay fotos de LFW en {images_directory}: bájalas con "
            f"`python -m faces lfw --salida {settings.data_dir / 'lfw'}`"
        )
    started = time.perf_counter()
    embeddings = _embeddings(
        [image.path for image in images],
        settings.models_dir,
        settings.pipeline(),
        settings.seed_workers,
    )
    people = famous_people(images, embeddings, images_directory)
    logger.info(
        "embeddings de %d de %d fotos (%d personas) en %.1f s",
        len(embeddings),
        len(images),
        len(people),
        time.perf_counter() - started,
    )
    loading = time.perf_counter()
    report = await engine.bulk_insert(
        FAMOUS_TABLE, (person.row(row_id) for row_id, person in enumerate(people))
    )
    if report.rows_rejected or report.rows_loaded != len(people):
        await engine.execute(DELETE_FAMOUS)
        raise SeedError(
            f"el gestor cargó {report.rows_loaded} de {len(people)} personas: se borró lo "
            "cargado; vuelve a correr la carga"
        )
    logger.info(
        "%s: %d personas cargadas en %.1f s (%.1f s en total)",
        FAMOUS_TABLE,
        report.rows_loaded,
        time.perf_counter() - loading,
        time.perf_counter() - started,
    )
    return report.rows_loaded


def _embeddings(
    paths: list[Path], models: Path, config: PipelineConfig, workers: int
) -> dict[Path, Embedding]:
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_start_worker, initargs=(models, config)
    ) as pool:
        results = list(pool.map(_embed_path, paths, chunksize=PHOTOS_PER_TASK))
    return {
        path: embedding
        for path, embedding in zip(paths, results, strict=True)
        if embedding is not None
    }


def _start_worker(models: Path, config: PipelineConfig) -> None:
    global _worker_pipeline
    _worker_pipeline = FacePipeline(locate_models(models), config)


def _embed_path(path: Path) -> Embedding | None:
    assert _worker_pipeline is not None
    try:
        return lfw_photo_embedding(_worker_pipeline, path.read_bytes())
    except FaceError as error:
        logger.warning("%s: %s", path.name, error)
        return None


if __name__ == "__main__":
    sys.exit(main())
