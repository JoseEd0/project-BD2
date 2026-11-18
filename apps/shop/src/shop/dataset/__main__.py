"""Prepara el dataset de la tienda con un comando:

    uv run python -m shop.dataset prepare --data-dir data/datasets

Escribe en `<data-dir>/amazon_musical_instruments/` y toma las tiendas de
`<data-dir>/lugares_lima.csv` (la descarga de la Parte 2, `demos.descargar_lugares`). La
carpeta de datos sale de `--data-dir` o, si no se da, de la variable `SHOP_DATA_DIR`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from appkit.download import DownloadError, fetch_verified
from shop.dataset.errors import DatasetError
from shop.dataset.images import ImageSettings
from shop.dataset.prepare import PrepareSettings, prepare
from shop.dataset.stores import STORE_KINDS, InventorySettings
from shop.dataset.transport import UrllibTransport

DATA_DIR_VARIABLE = "SHOP_DATA_DIR"
DATASET_DIRECTORY = "amazon_musical_instruments"
PLACES_FILE = "lugares_lima.csv"
PREPARE_COMMAND = "prepare"
# 512 px de lado mayor: más que los 224 px que ve CLIP y de sobra para SIFT, y la variante
# `large` de Amazon (unos 500 px) ya lo cubre sin bajar la de 1 500 px.
DEFAULT_MAX_SIDE = 512
DEFAULT_JPEG_QUALITY = 85
# Descargas simultáneas al CDN de imágenes: pocas, para no parecer un ataque.
DEFAULT_WORKERS = 8
IMAGE_ATTEMPTS = 4
BACKOFF_SECONDS = 2.0
DEFAULT_SEED = 2023
DEFAULT_PRODUCTS_PER_STORE = 1000
DEFAULT_MAX_QUANTITY = 12
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
FAILURE_EXIT_CODE = 1


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    data_dir = parsed.data_dir or _data_dir_from_environment()
    if data_dir is None:
        print(f"falta --data-dir o la variable {DATA_DIR_VARIABLE}", file=sys.stderr)
        return FAILURE_EXIT_CODE
    places = parsed.places or data_dir / PLACES_FILE
    if not places.is_file():
        print(
            f"no está {places}: bájalo con `python -m demos.descargar_lugares` (ver README)",
            file=sys.stderr,
        )
        return FAILURE_EXIT_CODE
    settings = PrepareSettings(
        output=data_dir / DATASET_DIRECTORY,
        places=places,
        images=ImageSettings(
            max_side=parsed.max_side,
            jpeg_quality=parsed.jpeg_quality,
            workers=parsed.workers,
            attempts=IMAGE_ATTEMPTS,
            backoff_seconds=BACKOFF_SECONDS,
        ),
        inventory=InventorySettings(
            seed=parsed.seed,
            products_per_store=parsed.products_per_store,
            max_quantity=parsed.max_quantity,
        ),
        store_kinds=STORE_KINDS,
    )
    try:
        manifest = prepare(settings, fetch_verified, UrllibTransport(), time.sleep)
    except (DatasetError, DownloadError) as error:
        print(f"error: {error}", file=sys.stderr)
        return FAILURE_EXIT_CODE
    print(json.dumps(manifest["counts"], indent=2))
    print(json.dumps(manifest["images"], indent=2))
    return 0


def _data_dir_from_environment() -> Path | None:
    value = os.environ.get(DATA_DIR_VARIABLE, "").strip()
    return Path(value) if value else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m shop.dataset", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser(PREPARE_COMMAND, help="baja y prepara todo el dataset")
    command.add_argument(
        "--data-dir", type=Path, help=f"carpeta de datasets; por omisión ${DATA_DIR_VARIABLE}"
    )
    command.add_argument(
        "--places", type=Path, help=f"CSV de lugares de Lima; por omisión <data-dir>/{PLACES_FILE}"
    )
    command.add_argument("--max-side", type=int, default=DEFAULT_MAX_SIDE)
    command.add_argument("--jpeg-quality", type=int, default=DEFAULT_JPEG_QUALITY)
    command.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    command.add_argument("--seed", type=int, default=DEFAULT_SEED)
    command.add_argument("--products-per-store", type=int, default=DEFAULT_PRODUCTS_PER_STORE)
    command.add_argument("--max-quantity", type=int, default=DEFAULT_MAX_QUANTITY)
    return parser


if __name__ == "__main__":
    sys.exit(main())
