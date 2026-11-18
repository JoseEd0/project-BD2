"""Lo que hay que bajar antes de usar el servicio, con su SHA-256 comprobado.

    python -m faces models --salida data/models/faces   # YuNet y SFace (37 MiB)
    python -m faces lfw --salida data/datasets/lfw      # LFW y sus pares (172 MiB)

Sin `--salida`, los modelos van a `FACES_MODELS_DIR`. Volver a correrlo no baja lo que ya
está verificado, y una descarga cortada sigue donde quedó.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .lfw import LFW_IMAGES, LFW_PEOPLE, download_lfw
from .models import MODELS_DIRECTORY_VARIABLE, download_models, models_directory_from_environment


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m faces", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    models = commands.add_parser("models", help="los modelos YuNet y SFace")
    models.add_argument(
        "--salida",
        type=Path,
        help=f"carpeta de los modelos (por omisión, ${MODELS_DIRECTORY_VARIABLE})",
    )
    lfw = commands.add_parser("lfw", help="Labeled Faces in the Wild y sus pares")
    lfw.add_argument("--salida", type=Path, required=True, help="carpeta del dataset")
    arguments = parser.parse_args()
    if arguments.command == "models":
        _download_models(arguments.salida)
    else:
        _download_lfw(arguments.salida)


def _download_models(directory: Path | None) -> None:
    paths = download_models(directory or models_directory_from_environment())
    print(f"YuNet en {paths.detector}\nSFace en {paths.recognizer}")


def _download_lfw(root: Path) -> None:
    images_directory = download_lfw(root)
    print(f"{LFW_IMAGES} fotos de {LFW_PEOPLE} personas en {images_directory}")


if __name__ == "__main__":
    main()
