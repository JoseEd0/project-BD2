"""El trabajo de carga: el catálogo de FMA en el gestor, indexado por segmentos.

    COPYRIGHT_ENGINE_URL=http://engine:8000 COPYRIGHT_ENGINE_KEY=… \\
    COPYRIGHT_DATA_DIR=data/datasets python -m songcheck.seed

1. deja el catálogo completo y verificado en disco (`songcheck.fma.prepare`);
2. aplica las migraciones del servicio (`schema.SCHEMA`);
3. carga `cr_canciones` y `cr_segmentos` con la carga masiva NDJSON;
4. crea el HNSW de los tramos (`schema.SEGMENT_INDEX`), que extrae sus MFCC, entrena el
   vocabulario y construye el grafo.

**Repetible y reanudable.** Una tabla que ya tiene todas sus filas no se toca; una carga
cortada sigue después de la última fila confirmada (las filas van en orden de id y el gestor
confirma los lotes en orden), y el índice se crea con `IF NOT EXISTS`. Una segunda ejecución
solo verifica las pistas y cuenta filas. El gestor lee el audio por su ruta, así que la
carpeta de datasets tiene que estar entre sus `MINIGESTOR_IMPORT_DIRS`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import posixpath
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from appkit import configure_logging, engine_client, load_settings, migrate
from minigestor_client import NO_RETRIES, AsyncClient, LoadReport, Record
from songcheck.catalog import CatalogSong, read_catalog
from songcheck.errors import SeedError
from songcheck.fma.prepare import CatalogReport, prepare_catalog
from songcheck.schema import (
    SCHEMA,
    SEGMENT_END_COLUMN,
    SEGMENT_INDEX,
    SEGMENT_INDEX_NAME,
    SEGMENT_SONG_COLUMN,
    SEGMENT_START_COLUMN,
    SEGMENTS_TABLE,
    SONGS_TABLE,
)
from songcheck.settings import SeedSettings
from songcheck.windows import WINDOWING, Windowing, segment_windows

# El id de un tramo es `id de la canción · 1000 + número de ventana`: dice de qué canción es
# sin consultar nada y no cambia si el catálogo crece. 1000 ventanas de 5 s cada 2,5 s
# cubren canciones de hasta 41 min; los ids caben de sobra en el INT de 64 bits del gestor.
SEGMENT_ID_STRIDE = 1_000
PROGRESS_EVERY_ROWS = 10_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TableLoad:
    """Una tabla del catálogo: filas esperadas, cuántas se cargaron ahora y en cuánto."""

    table: str
    expected_rows: int
    loaded_rows: int
    seconds: float


@dataclass(frozen=True, slots=True)
class IndexBuild:
    """El HNSW de los tramos: si se creó ahora y cuánto tardó la sentencia."""

    created: bool
    seconds: float


@dataclass(frozen=True, slots=True)
class CatalogLoad:
    """Lo que hizo la carga en el gestor."""

    songs: TableLoad
    segments: TableLoad
    index: IndexBuild


@dataclass(frozen=True, slots=True)
class SeedReport:
    """Todo el trabajo de carga: el catálogo en disco y la carga en el gestor."""

    catalog: CatalogReport
    catalog_seconds: float
    load: CatalogLoad


def segment_id(track_id: int, window_number: int) -> int:
    """El id del tramo número `window_number` de la canción `track_id`.

    Raises:
        SeedError: si la canción tiene más ventanas que `SEGMENT_ID_STRIDE`.
    """
    if window_number >= SEGMENT_ID_STRIDE:
        raise SeedError(
            f"la canción {track_id} tiene más de {SEGMENT_ID_STRIDE} ventanas: es demasiado larga"
        )
    return track_id * SEGMENT_ID_STRIDE + window_number


def song_rows(songs: Iterable[CatalogSong], media_root: str) -> Iterator[Record]:
    """Las filas de `cr_canciones`, con la ruta de cada pista tal como la ve el gestor."""
    for song in songs:
        yield {
            "id": song.track_id,
            "titulo": song.title,
            "artista": song.artist,
            "genero": song.genre,
            "duracion": song.duration_seconds,
            "ruta": posixpath.join(media_root, song.path),
            "busqueda": f"{song.title} {song.artist}",
        }


def segment_rows(
    songs: Iterable[CatalogSong], media_root: str, windowing: Windowing = WINDOWING
) -> Iterator[Record]:
    """Las filas de `cr_segmentos`: una por ventana de cada canción, en orden de id."""
    for song in songs:
        path = posixpath.join(media_root, song.path)
        for number, window in enumerate(segment_windows(song.duration_seconds, windowing)):
            yield {
                "id": segment_id(song.track_id, number),
                SEGMENT_SONG_COLUMN: song.track_id,
                SEGMENT_START_COLUMN: window.start,
                SEGMENT_END_COLUMN: window.end,
                "ruta": window.of(path),
            }


def segment_count(songs: Iterable[CatalogSong], windowing: Windowing = WINDOWING) -> int:
    """Cuántos tramos tiene el catálogo."""
    return sum(len(segment_windows(song.duration_seconds, windowing)) for song in songs)


async def load_catalog(
    engine: AsyncClient, indexer: AsyncClient, songs: Sequence[CatalogSong], media_root: str
) -> CatalogLoad:
    """Migra, carga lo que falte de las dos tablas y crea el HNSW de los tramos. `engine`
    necesita rol `admin` (el DDL); `indexer` es otro cliente con la misma clave y un plazo
    de lectura que alcance para construir el índice.

    Raises:
        appkit.MigrationError: si las tablas existen con otra definición.
        SeedError: si el gestor rechaza un lote o una tabla queda con otras filas.
    """
    ordered = sorted(songs, key=lambda song: song.track_id)
    await migrate(engine, SCHEMA)
    songs_load = await load_missing(
        engine, SONGS_TABLE, song_rows(ordered, media_root), len(ordered)
    )
    segments_load = await load_missing(
        engine, SEGMENTS_TABLE, segment_rows(ordered, media_root), segment_count(ordered)
    )
    return CatalogLoad(songs_load, segments_load, await build_segment_index(indexer))


async def load_missing(
    engine: AsyncClient, table: str, rows: Iterable[Record], expected_rows: int
) -> TableLoad:
    """Carga las filas de `rows` (en orden de id) que siguen a la última de `table`.

    Raises:
        SeedError: si el gestor rechaza un lote o la tabla no termina con `expected_rows`.
    """
    started = time.perf_counter()
    present = await _count(engine, table)
    if present == expected_rows:
        logger.info("%s: ya tiene sus %d filas, no se carga nada", table, expected_rows)
        return TableLoad(table, expected_rows, 0, time.perf_counter() - started)
    last_id = await engine.scalar(f"SELECT MAX(id) FROM {table}")
    pending = (row for row in rows if last_id is None or int(row["id"]) > int(last_id))
    logger.info("%s: %d de %d filas presentes, cargando el resto", table, present, expected_rows)
    report = await engine.bulk_insert(table, _logged(pending, table, expected_rows - present))
    _reject_partial_load(report)
    loaded = await _count(engine, table)
    if loaded != expected_rows:
        raise SeedError(
            f"{table} quedó con {loaded} filas y el catálogo tiene {expected_rows}: borra la "
            "tabla y vuelve a cargar si cambió el catálogo"
        )
    seconds = time.perf_counter() - started
    logger.info("%s: %d filas cargadas en %.1f s", table, report.rows_loaded, seconds)
    return TableLoad(table, expected_rows, report.rows_loaded, seconds)


async def build_segment_index(indexer: AsyncClient) -> IndexBuild:
    """Crea el HNSW de los tramos si no existe. No se reintenta sin respuesta: el gestor
    puede seguir construyéndolo."""
    started = time.perf_counter()
    tables = {table.name: table for table in await indexer.tables()}
    existed = SEGMENT_INDEX_NAME in tables[SEGMENTS_TABLE].indexes
    if not existed:
        logger.info(
            "%s: extrayendo MFCC, entrenando el vocabulario y armando el HNSW", SEGMENTS_TABLE
        )
    await indexer.execute(SEGMENT_INDEX, idempotent=False)
    seconds = time.perf_counter() - started
    logger.info(
        "índice %s %s en %.1f s", SEGMENT_INDEX_NAME, "ya estaba" if existed else "creado", seconds
    )
    return IndexBuild(created=not existed, seconds=seconds)


def run_seed(settings: SeedSettings) -> SeedReport:
    """El trabajo de carga completo con la configuración del entorno."""
    started = time.perf_counter()
    catalog = prepare_catalog(settings.data_dir, settings.sources(), settings.download_threads)
    catalog_seconds = time.perf_counter() - started
    songs = read_catalog(catalog.catalog)
    load = asyncio.run(_load_with_clients(settings, songs))
    return SeedReport(catalog, catalog_seconds, load)


async def _load_with_clients(settings: SeedSettings, songs: Sequence[CatalogSong]) -> CatalogLoad:
    key = settings.migration_key or settings.engine_key
    index_timeouts = dataclasses.replace(
        settings.timeouts(), read_seconds=settings.index_timeout_seconds
    )
    async with (
        engine_client(settings, key) as engine,
        AsyncClient(
            settings.engine_base_url,
            key.get_secret_value(),
            timeouts=index_timeouts,
            retry=NO_RETRIES,
        ) as indexer,
    ):
        return await load_catalog(engine, indexer, songs, settings.engine_media_root())


async def _count(engine: AsyncClient, table: str) -> int:
    return int(await engine.scalar(f"SELECT COUNT(*) FROM {table}") or 0)


def _reject_partial_load(report: LoadReport) -> None:
    if not report.rows_rejected:
        return
    first = report.rejected[0]
    raise SeedError(
        f"{report.table}: el gestor rechazó {report.rows_rejected} filas; el primer lote, de "
        f"la línea {first.first_line} a la {first.last_line}: {first.reason}"
    )


def _logged(rows: Iterable[Record], table: str, total: int) -> Iterator[Record]:
    for sent, row in enumerate(rows, start=1):
        yield row
        if sent % PROGRESS_EVERY_ROWS == 0:
            logger.info("%s: %d de %d filas enviadas", table, sent, total)


def main() -> None:
    settings = load_settings(SeedSettings)
    configure_logging(settings.log_level)
    report = run_seed(settings)
    load = report.load
    logger.info(
        "catálogo listo: %d canciones y %d tramos; disco %.1f s, canciones %.1f s, tramos "
        "%.1f s, índice %.1f s",
        load.songs.expected_rows,
        load.segments.expected_rows,
        report.catalog_seconds,
        load.songs.seconds,
        load.segments.seconds,
        load.index.seconds,
    )


if __name__ == "__main__":
    main()
