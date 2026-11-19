"""El modelo de embeddings para los tests y sus vectores de referencia.

El modelo (113 MiB en int8, 449 MiB en fp32) no se versiona: se busca en `RAG_MODELS_DIR`.
Sin la variable, los tests que lo necesitan se saltan diciendo cómo bajarlo. Con la variable,
la variante por omisión tiene que estar —la CI la define y la baja antes—, así que nunca se
salta en silencio; la otra variante se prueba solo si está.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rag.embeddings import E5Embedder, EmbedderSettings
from rag.models import (
    DEFAULT_MODEL_VARIANT,
    MODELS_DIRECTORY_VARIABLE,
    ModelFiles,
    ModelVariant,
    locate_model,
)

FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE_FILE = FIXTURES / "e5_reference.json"
# Lotes chicos: los tests pasan por más de un lote con pocos textos.
TEST_BATCH_SIZE = 2
TEST_THREADS = 2


def models_directory() -> Path:
    configured = os.environ.get(MODELS_DIRECTORY_VARIABLE)
    if not configured:
        pytest.skip(
            f"sin {MODELS_DIRECTORY_VARIABLE}: baja el modelo con "
            "`python -m rag.models --salida <carpeta>` y define la variable"
        )
    return Path(configured)


def model_files(variant: ModelVariant) -> ModelFiles:
    directory = models_directory()
    present = ModelFiles.inside(directory, variant).model.is_file()
    if variant is not DEFAULT_MODEL_VARIANT and not present:
        pytest.skip(f"la variante {variant} no está en {directory} (`--variante {variant}`)")
    return locate_model(directory, variant)


def loaded_embedder(variant: ModelVariant = DEFAULT_MODEL_VARIANT) -> E5Embedder:
    settings = EmbedderSettings(threads=TEST_THREADS, batch_size=TEST_BATCH_SIZE)
    return E5Embedder(model_files(variant), settings)


def reference_items() -> list[dict[str, object]]:
    reference = json.loads(REFERENCE_FILE.read_text(encoding="utf-8"))
    items: list[dict[str, object]] = reference["items"]
    return items
