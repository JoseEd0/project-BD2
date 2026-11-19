"""Baja CLIP ViT-B/32 en ONNX con su SHA-256 comprobado:

    python -m shop.clip models --salida data/models/shop

Sin `--salida`, a `SHOP_MODELS_DIR`. Volver a correrlo no baja lo que ya está verificado, y
una descarga cortada sigue donde quedó.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .models import MODELS_DIRECTORY_VARIABLE, download_models, models_directory_from_environment


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m shop.clip", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    models = commands.add_parser("models", help="las dos torres de CLIP y su tokenizador")
    models.add_argument(
        "--salida",
        type=Path,
        help=f"carpeta de los modelos (por omisión, ${MODELS_DIRECTORY_VARIABLE})",
    )
    arguments = parser.parse_args()
    paths = download_models(arguments.salida or models_directory_from_environment())
    print(f"imagen en {paths.vision}\ntexto en {paths.text}\ntokenizador en {paths.tokenizer}")


if __name__ == "__main__":
    main()
