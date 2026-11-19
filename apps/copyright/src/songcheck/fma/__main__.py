"""Baja el catálogo completo de `fma_small` (8 000 pistas de 30 s) y escribe `catalog.csv`.

    uv run python -m songcheck.fma --data-dir data/datasets

Repetible: si el catálogo ya está completo y verificado no va a la red; si no, baja solo las
pistas que faltan o están dañadas. El trabajo de carga (`python -m songcheck.seed`) hace
esto mismo antes de cargar el gestor.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from appkit import configure_logging
from songcheck.fma.prepare import (
    DEFAULT_AUDIO_URL,
    DEFAULT_METADATA_URL,
    FmaSources,
    prepare_catalog,
)
from songcheck.settings import DATA_DIRECTORY_VARIABLE, DEFAULT_DOWNLOAD_THREADS

LOG_LEVEL = "INFO"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=os.environ.get(DATA_DIRECTORY_VARIABLE),
        help=f"carpeta de datasets, con el catálogo en fma_small/ (${DATA_DIRECTORY_VARIABLE})",
    )
    parser.add_argument(
        "--threads", type=int, default=DEFAULT_DOWNLOAD_THREADS, help="pistas a la vez"
    )
    parser.add_argument("--audio-url", default=DEFAULT_AUDIO_URL, help="el .zip de fma_small")
    parser.add_argument("--metadata-url", default=DEFAULT_METADATA_URL, help="el .zip de metadatos")
    arguments = parser.parse_args()
    if arguments.data_dir is None:
        parser.error(f"falta --data-dir o {DATA_DIRECTORY_VARIABLE}")
    configure_logging(LOG_LEVEL)
    report = prepare_catalog(
        Path(arguments.data_dir),
        FmaSources(arguments.audio_url, arguments.metadata_url),
        arguments.threads,
    )
    print(
        f"{report.songs} pistas en {report.catalog}: {report.fetched} bajadas ahora, "
        f"{report.already_intact} ya estaban sanas, {report.unreadable} ilegibles"
    )
    print("Free Music Archive: metadatos CC BY 4.0; cada pista, con la licencia de su artista")


if __name__ == "__main__":
    main()
