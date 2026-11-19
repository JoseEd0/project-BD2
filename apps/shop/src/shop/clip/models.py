"""CLIP ViT-B/32 exportado a ONNX: dónde se baja y dónde se busca.

    python -m shop.clip models --salida data/models/shop

Es la exportación de Xenova (`Xenova/clip-vit-base-patch32`, licencia MIT como el modelo de
OpenAI del que sale) en una revisión fija del Hugging Face Hub: la torre de imagen
(`vision_model.onnx`, 335 MiB), la de texto (`text_model.onnx`, 242 MiB) y el tokenizador
BPE (`tokenizer.json`). Las dos torres están en `float32`, sin cuantizar: el embedding es
el del modelo publicado, y la comparación con SIFT (#82) no mide una aproximación.

Cada archivo se acepta solo si su SHA-256 es el que publica el Hub (el `oid` de Git LFS de
los modelos). La carpeta sale de `--salida` o de `SHOP_MODELS_DIR`, la misma que usan el
servicio, la carga y los tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from appkit.download import RemoteFile, fetch_verified

from .errors import ModelNotFoundError

MODELS_DIRECTORY_VARIABLE = "SHOP_MODELS_DIR"
HUB_REPOSITORY = "Xenova/clip-vit-base-patch32"
HUB_REVISION = "6b7c523703ae26e39be4b03774966f8571c7f523"
HUB_FILES = f"https://huggingface.co/{HUB_REPOSITORY}/resolve/{HUB_REVISION}"
MODEL_DIRECTORY = "clip-vit-base-patch32"
VISION_FILENAME = "vision_model.onnx"
TEXT_FILENAME = "text_model.onnx"
TOKENIZER_FILENAME = "tokenizer.json"

VISION_MODEL = RemoteFile(
    url=f"{HUB_FILES}/onnx/{VISION_FILENAME}",
    sha256="fd6e1402a588279d1723c7534d4bcba5bc0b14b47dfab0e46f8c47b8270d7d40",
    size=351_685_709,
)
TEXT_MODEL = RemoteFile(
    url=f"{HUB_FILES}/onnx/{TEXT_FILENAME}",
    sha256="3f6571f5bad13a97c469c1622e1cfc4d9aef78b79fdbfcff804ca357bfada8cc",
    size=254_058_553,
)
TOKENIZER = RemoteFile(
    url=f"{HUB_FILES}/{TOKENIZER_FILENAME}",
    sha256="f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd",
    size=2_224_119,
)


@dataclass(frozen=True, slots=True)
class ModelPaths:
    """Dónde están en disco las dos torres y el tokenizador."""

    vision: Path
    text: Path
    tokenizer: Path

    @classmethod
    def inside(cls, directory: Path) -> ModelPaths:
        folder = directory / MODEL_DIRECTORY
        return cls(folder / VISION_FILENAME, folder / TEXT_FILENAME, folder / TOKENIZER_FILENAME)


def download_models(directory: Path) -> ModelPaths:
    """Baja los tres archivos a `directory`, comprobando su SHA-256; los que ya están
    verificados no se vuelven a bajar.

    Raises:
        appkit.download.DownloadError: si un archivo no llega completo o con su suma.
    """
    paths = ModelPaths.inside(directory)
    fetch_verified(VISION_MODEL, paths.vision)
    fetch_verified(TEXT_MODEL, paths.text)
    fetch_verified(TOKENIZER, paths.tokenizer)
    return paths


def locate_models(directory: Path) -> ModelPaths:
    """Los archivos de `directory`, sin volver a calcular su suma: se comprobó al bajarlos.

    Raises:
        ModelNotFoundError: si falta alguno.
    """
    paths = ModelPaths.inside(directory)
    files = (paths.vision, paths.text, paths.tokenizer)
    missing = [path.name for path in files if not path.is_file()]
    if missing:
        raise ModelNotFoundError(
            f"faltan {', '.join(missing)} en {directory / MODEL_DIRECTORY}: bájalos con "
            f"`python -m shop.clip models --salida {directory}`"
        )
    return paths


def models_directory_from_environment() -> Path:
    """La carpeta de `SHOP_MODELS_DIR`.

    Raises:
        ModelNotFoundError: si la variable no está definida.
    """
    configured = os.environ.get(MODELS_DIRECTORY_VARIABLE)
    if not configured:
        raise ModelNotFoundError(f"falta {MODELS_DIRECTORY_VARIABLE}: la carpeta de los modelos")
    return Path(configured)
