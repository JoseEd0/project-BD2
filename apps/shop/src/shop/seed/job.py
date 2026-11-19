"""Llena el gestor con el catálogo de la tienda. Repetible y reanudable.

Pasos, en orden:

1. **migraciones**: las de `catalog.schema.SCHEMA`, las mismas que aplica el API al arrancar;
2. **tablas** por carga masiva NDJSON (`bulk_insert`): tiendas, productos, usuarios, compras,
   inventario e historial. Los índices que se construyen de una vez ya existen con la tabla
   vacía, y el gestor los construye al final de la carga;
3. **compras conjuntas**: los pares del historial con el `GROUP BY` del gestor, materializados
   en `shop_compras_conjuntas` (`copurchases.py`);
4. **SIFT**: el `CREATE INDEX ... USING HNSW (imagen)`, que necesita las fotos de la tabla
   para entrenar su vocabulario;
5. **CLIP**: los embeddings de cada foto (con su caché en disco) y la carga de `shop_clip`,
   que construye su HNSW al final.

**Repetible:** una tabla con todas sus filas no se vuelve a cargar y un índice que existe no
se vuelve a crear; una segunda ejecución solo pregunta. **Reanudable:** cada lote de la carga
se confirma por separado y las filas entran en el orden del CSV, así que una tabla a medias
tiene exactamente las primeras `n` filas; la siguiente ejecución envía desde la `n + 1`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from itertools import islice

from appkit import migrate
from minigestor_client import AsyncClient, LoadReport
from shop.catalog import rows
from shop.catalog.rows import DatasetPaths, Row
from shop.catalog.schema import (
    CLIP,
    HISTORY,
    INVENTORY,
    PRODUCTS,
    PURCHASES,
    SCHEMA,
    SIFT_INDEX,
    SIFT_INDEX_NAME,
    STORES,
    USERS,
)
from shop.clip import (
    BatchSettings,
    ClipImageEncoder,
    EmbeddingCache,
    PixelEncoder,
    fill_cache,
    locate_models,
)
from shop.clip.encoder import Embeddings
from shop.clip.models import VISION_MODEL
from shop.clip.preprocessing import PixelValues
from shop.settings import SeedSettings

from .copurchases import CopurchaseSettings, count_buyers, materialize_copurchases
from .manifest import expected_rows
from .report import EmbeddingStep, IndexBuild, SeedReport, TableLoad

Clock = Callable[[], float]
EncoderLoader = Callable[[SeedSettings], PixelEncoder]

logger = logging.getLogger(__name__)


class SeedError(Exception):
    """La carga no dejó lo que debía: un lote rechazado, filas de más o de menos."""


@dataclass(frozen=True, slots=True)
class TableSource:
    """Una tabla, de dónde salen sus filas y cuántas tiene que tener."""

    table: str
    rows: Callable[[], Iterator[Row]]
    expected: int


def clip_image_encoder(settings: SeedSettings) -> PixelEncoder:
    """La torre de imagen de CLIP de `settings.models_dir`."""
    return ClipImageEncoder(locate_models(settings.models_dir).vision, settings.clip_threads)


async def run_seed(
    engine: AsyncClient,
    settings: SeedSettings,
    load_encoder: EncoderLoader = clip_image_encoder,
    clock: Clock = time.perf_counter,
) -> SeedReport:
    """Aplica las migraciones, carga las tablas, crea el índice de SIFT y carga CLIP.

    `engine` necesita rol `admin` (crea tablas e índices) y un plazo de lectura que cubra
    construir un índice (`SeedSettings.job_timeout_seconds`). `load_encoder` da la torre de
    imagen solo si hay fotos que calcular.

    Raises:
        SeedError: si una tabla queda con otras filas que las del dataset.
        appkit.MigrationError: si una migración choca con lo que ya existe.
        shop.clip.ClipError: si faltan los modelos o una foto no se puede leer.
        minigestor_client.MinigestorError: si el gestor rechaza una petición.
    """
    started = clock()
    report = SeedReport()
    report.migrations_seconds = await _timed_migrations(engine, clock)
    paths = DatasetPaths(settings.dataset, settings.engine_dataset)
    for source in _table_sources(settings, paths):
        report.tables.append(await _load_table(engine, source, settings.progress_rows, clock))
    report.copurchases = await materialize_copurchases(
        engine,
        count_buyers(rows.history_rows(settings.dataset), settings.recommendation_split),
        CopurchaseSettings(
            settings.recommendation_split,
            settings.copurchase_min_support,
            settings.copurchase_max_partners,
            settings.copurchase_chunk_buyers,
        ),
        clock,
    )
    report.indexes.append(await _create_sift_index(engine, clock))
    clip_load, embeddings = await _load_clip(engine, settings, paths, load_encoder, clock)
    report.tables.append(clip_load)
    report.embeddings = embeddings
    report.total_seconds = clock() - started
    logger.info("carga terminada en %.1f s", report.total_seconds)
    return report


def _table_sources(settings: SeedSettings, paths: DatasetPaths) -> list[TableSource]:
    dataset = settings.dataset
    counts = expected_rows(dataset)
    return [
        TableSource(STORES, lambda: rows.store_rows(dataset), counts.stores),
        TableSource(PRODUCTS, lambda: rows.product_rows(paths), counts.products),
        TableSource(USERS, lambda: rows.user_rows(dataset), counts.users),
        TableSource(PURCHASES, lambda: rows.purchase_rows(dataset), counts.purchases),
        TableSource(INVENTORY, lambda: rows.inventory_rows(dataset), counts.inventory),
        TableSource(HISTORY, lambda: rows.history_rows(dataset), counts.purchases),
    ]


async def _timed_migrations(engine: AsyncClient, clock: Clock) -> float:
    started = clock()
    await migrate(engine, SCHEMA)
    return clock() - started


async def _row_counts(engine: AsyncClient) -> dict[str, int]:
    return {table.name: table.row_count for table in await engine.tables()}


async def _load_table(
    engine: AsyncClient, source: TableSource, progress_rows: int, clock: Clock
) -> TableLoad:
    present = (await _row_counts(engine)).get(source.table, 0)
    if present == source.expected:
        logger.info("%s ya tiene sus %d filas", source.table, present)
        return TableLoad(source.table, source.expected, 0, 0.0)
    if present > source.expected:
        raise SeedError(
            f"{source.table} tiene {present} filas y el dataset {source.expected}: vacíala"
        )
    logger.info(
        "%s: cargando %d filas desde la %d", source.table, source.expected - present, present + 1
    )
    pending = _logged(islice(source.rows(), present, None), source.table, present, progress_rows)
    started = clock()
    load = await engine.bulk_insert(source.table, pending)
    finished = clock()
    _require_complete(load, source, present)
    logger.info(
        "%s: %d filas en %.1f s (índices al final: %s)",
        source.table,
        load.rows_loaded,
        finished - started,
        ", ".join(load.deferred_indexes) or "ninguno",
    )
    return TableLoad(
        source.table,
        source.expected,
        load.rows_loaded,
        finished - started,
        tuple(load.deferred_indexes),
    )


def _require_complete(load: LoadReport, source: TableSource, present: int) -> None:
    if load.rejected:
        first = load.rejected[0]
        raise SeedError(
            f"{source.table}: el gestor rechazó {load.rows_rejected} filas; la primera, en la "
            f"línea {first.line} del envío: {first.reason}"
        )
    if load.interrupted is not None:
        raise SeedError(
            f"{source.table}: la carga se cortó ({load.interrupted}); vuelve a correrla"
        )
    if present + load.rows_loaded != source.expected:
        raise SeedError(
            f"{source.table} quedó con {present + load.rows_loaded} filas y el dataset tiene "
            f"{source.expected}"
        )


async def _create_sift_index(engine: AsyncClient, clock: Clock) -> IndexBuild:
    tables = {table.name: table for table in await engine.tables()}
    if SIFT_INDEX_NAME in tables[PRODUCTS].indexes:
        logger.info("el índice %s ya existe", SIFT_INDEX_NAME)
        return IndexBuild(SIFT_INDEX_NAME, False, 0.0)
    logger.info("SIFT: extrayendo, entrenando el vocabulario y construyendo el HNSW")
    started = clock()
    await engine.execute(SIFT_INDEX, idempotent=True)
    seconds = clock() - started
    logger.info("SIFT: índice %s en %.1f s", SIFT_INDEX_NAME, seconds)
    return IndexBuild(SIFT_INDEX_NAME, True, seconds)


async def _load_clip(
    engine: AsyncClient,
    settings: SeedSettings,
    paths: DatasetPaths,
    load_encoder: EncoderLoader,
    clock: Clock,
) -> tuple[TableLoad, EmbeddingStep]:
    images = list(rows.product_images(paths))
    present = (await _row_counts(engine)).get(CLIP, 0)
    if present == len(images):
        logger.info("%s ya tiene sus %d filas", CLIP, present)
        return TableLoad(CLIP, present, 0, 0.0), EmbeddingStep(0, 0, 0, 0.0)
    encoder = _LazyEncoder(load_encoder, settings)
    batches = BatchSettings(
        settings.clip_batch_size, settings.preprocess_workers, settings.progress_rows
    )
    with EmbeddingCache(settings.embeddings, VISION_MODEL.sha256) as cache:
        run = fill_cache((path for _, path in images), encoder, cache, batches, clock)
        logger.info(
            "CLIP: %d fotos distintas, %d calculadas y %d de la caché, en %.1f s",
            run.images,
            run.computed,
            run.cached,
            run.seconds,
        )
        def clip_rows() -> Iterator[Row]:
            for product, path in images:
                yield {"id": product, "embedding": cache.get(run.digests[path])}

        source = TableSource(CLIP, clip_rows, len(images))
        load = await _load_table(engine, source, settings.progress_rows, clock)
    return load, EmbeddingStep(run.images, run.computed, run.cached, run.seconds)


def _logged(items: Iterable[Row], table: str, start: int, every: int) -> Iterator[Row]:
    for position, item in enumerate(items, start=start + 1):
        if position % every == 0:
            logger.info("%s: %d filas enviadas", table, position)
        yield item


class _LazyEncoder:
    """Carga la torre de imagen (335 MiB) la primera vez que hace falta: si todas las fotos
    están en la caché, no se carga."""

    def __init__(self, load: EncoderLoader, settings: SeedSettings) -> None:
        self._load = load
        self._settings = settings
        self._encoder: PixelEncoder | None = None

    def embed_pixels(self, batch: PixelValues) -> Embeddings:
        if self._encoder is None:
            self._encoder = self._load(self._settings)
        return self._encoder.embed_pixels(batch)
