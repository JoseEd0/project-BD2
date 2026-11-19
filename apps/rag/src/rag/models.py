"""El modelo de embeddings, multilingual-e5-small (MIT), exportado a ONNX por sus autores.

    python -m rag.models --salida data/models/rag [--variante fp32 --variante int8]

Las URL apuntan a una revisión fija del repositorio `intfloat/multilingual-e5-small` de
Hugging Face, y cada archivo se acepta solo si su SHA-256 es el de su puntero de Git LFS en
esa revisión. Hay dos variantes del mismo modelo, `fp32` (`onnx/model.onnx`, 449 MiB) e
`int8` (`onnx/model_qint8_avx512_vnni.onnx`, 113 MiB, cuantizado dinámicamente); el
tokenizador (`tokenizer.json`) es el mismo para las dos. La carpeta sale de `--salida` o de
la variable `RAG_MODELS_DIR`, la misma que usan la ingesta, el servicio y los tests.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from appkit.download import RemoteFile, fetch_verified

from .errors import ModelNotFoundError

MODELS_DIRECTORY_VARIABLE = "RAG_MODELS_DIR"
MODEL_NAME = "multilingual-e5-small"
MODEL_REPOSITORY = f"https://huggingface.co/intfloat/{MODEL_NAME}/resolve"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_FILES = f"{MODEL_REPOSITORY}/{MODEL_REVISION}"
# Doce caracteres de la revisión bastan para distinguirla en la caché de embeddings.
SHORT_REVISION = 12
TOKENIZER_FILENAME = "tokenizer.json"

TOKENIZER = RemoteFile(
    url=f"{MODEL_FILES}/{TOKENIZER_FILENAME}",
    sha256="0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    size=17_082_730,
)


class ModelVariant(StrEnum):
    """La precisión de los pesos del modelo."""

    FP32 = "fp32"
    INT8 = "int8"


# Medido en `benchmarks/rag_ingest_benchmark.py` (ver el README del servicio).
DEFAULT_MODEL_VARIANT = ModelVariant.INT8

VARIANT_FILES = {
    ModelVariant.FP32: (
        "model.onnx",
        RemoteFile(
            url=f"{MODEL_FILES}/onnx/model.onnx",
            sha256="ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665",
            size=470_268_510,
        ),
    ),
    ModelVariant.INT8: (
        "model_qint8_avx512_vnni.onnx",
        RemoteFile(
            url=f"{MODEL_FILES}/onnx/model_qint8_avx512_vnni.onnx",
            sha256="dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88",
            size=118_346_824,
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class ModelFiles:
    """Dónde están en disco el modelo de una variante y su tokenizador."""

    variant: ModelVariant
    model: Path
    tokenizer: Path

    @classmethod
    def inside(cls, directory: Path, variant: ModelVariant) -> ModelFiles:
        filename, _ = VARIANT_FILES[variant]
        return cls(variant, directory / filename, directory / TOKENIZER_FILENAME)

    @property
    def model_id(self) -> str:
        """Identifica los vectores que da este modelo: nombre, revisión y precisión."""
        return f"{MODEL_NAME}@{MODEL_REVISION[:SHORT_REVISION]}-{self.variant}"


def download_model(directory: Path, variant: ModelVariant) -> ModelFiles:
    """Baja la variante y el tokenizador a `directory`, comprobando su SHA-256; lo que ya
    está verificado no se vuelve a bajar.

    Raises:
        appkit.download.DownloadError: si un archivo no llega completo o con su suma.
    """
    files = ModelFiles.inside(directory, variant)
    _, remote = VARIANT_FILES[variant]
    fetch_verified(TOKENIZER, files.tokenizer)
    fetch_verified(remote, files.model)
    return files


def locate_model(directory: Path, variant: ModelVariant) -> ModelFiles:
    """Los archivos de la variante en `directory`, sin volver a calcular su suma: se
    comprobó al bajarlos.

    Raises:
        ModelNotFoundError: si falta alguno.
    """
    files = ModelFiles.inside(directory, variant)
    missing = [path.name for path in (files.model, files.tokenizer) if not path.is_file()]
    if missing:
        raise ModelNotFoundError(
            f"faltan {', '.join(missing)} en {directory}: bájalos con "
            f"`python -m rag.models --variante {variant} --salida {directory}`"
        )
    return files


def models_directory_from_environment() -> Path:
    """La carpeta de `RAG_MODELS_DIR`.

    Raises:
        ModelNotFoundError: si la variable no está definida.
    """
    configured = os.environ.get(MODELS_DIRECTORY_VARIABLE)
    if not configured:
        raise ModelNotFoundError(f"falta {MODELS_DIRECTORY_VARIABLE}: la carpeta de los modelos")
    return Path(configured)


def main(arguments: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="rag.models", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--salida",
        type=Path,
        help=f"carpeta de los modelos (por omisión, ${MODELS_DIRECTORY_VARIABLE})",
    )
    parser.add_argument(
        "--variante",
        type=ModelVariant,
        action="append",
        choices=list(ModelVariant),
        help=f"se repite para bajar varias (por omisión, {DEFAULT_MODEL_VARIANT})",
    )
    options = parser.parse_args(arguments)
    directory = options.salida or models_directory_from_environment()
    for variant in options.variante or [DEFAULT_MODEL_VARIANT]:
        files = download_model(directory, variant)
        print(f"{files.model_id}: {files.model} y {files.tokenizer}")


if __name__ == "__main__":
    main()
