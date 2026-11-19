"""Carga el catálogo de la tienda en el gestor:

    SHOP_ENGINE_URL=http://localhost:8000 SHOP_ENGINE_KEY=... SHOP_DATA_DIR=data/datasets \\
    SHOP_MODELS_DIR=data/models/shop uv run python -m shop.seed --informe carga.json

La configuración sale del entorno (`shop.settings.SeedSettings`). La clave tiene que ser
`admin` (`SHOP_MIGRATION_KEY` o, si no está, `SHOP_ENGINE_KEY`): la carga crea tablas e
índices. Volver a correrlo no repite nada de lo que ya está; si se cortó, sigue donde quedó.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from appkit import MigrationError, SettingsError, configure_logging, load_settings
from minigestor_client import AsyncClient, MinigestorError, Timeouts
from shop.catalog.errors import CatalogError
from shop.clip import ClipError
from shop.settings import SeedSettings

from .job import SeedError, run_seed
from .manifest import ManifestError
from .report import SeedReport

FAILURE_EXIT_CODE = 1
SEED_FAILURES = (
    SeedError,
    ManifestError,
    CatalogError,
    ClipError,
    MigrationError,
    MinigestorError,
)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m shop.seed", description=__doc__)
    parser.add_argument("--informe", type=Path, help="JSON con el tiempo de cada paso")
    parsed = parser.parse_args(arguments)
    try:
        settings = load_settings(SeedSettings)
    except SettingsError as error:
        print(f"error: {error}", file=sys.stderr)
        return FAILURE_EXIT_CODE
    configure_logging(settings.log_level)
    try:
        report = asyncio.run(_seed(settings))
    except SEED_FAILURES as error:
        logging.getLogger(__name__).error("la carga falló: %s", error)
        return FAILURE_EXIT_CODE
    if parsed.informe is not None:
        parsed.informe.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    return 0


def seed_client(settings: SeedSettings) -> AsyncClient:
    """Un cliente con la clave de administración y el plazo largo de la carga."""
    key = settings.migration_key or settings.engine_key
    timeouts = Timeouts(
        connect_seconds=settings.engine_connect_timeout_seconds,
        read_seconds=settings.job_timeout_seconds,
        write_seconds=settings.job_timeout_seconds,
        load_seconds=settings.job_timeout_seconds,
    )
    return AsyncClient(
        settings.engine_base_url, key.get_secret_value(), timeouts=timeouts, retry=settings.retry()
    )


async def _seed(settings: SeedSettings) -> SeedReport:
    async with seed_client(settings) as engine:
        return await run_seed(engine, settings)


if __name__ == "__main__":
    sys.exit(main())
