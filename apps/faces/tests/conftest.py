"""Fotos y modelos para los tests de rostros.

Los modelos (37 MiB) no se versionan: se buscan en `FACES_MODELS_DIR`. Sin la variable,
los tests que los necesitan se saltan diciendo cómo bajarlos; con la variable y sin los
modelos, fallan, para que la CI —que la define— nunca los salte en silencio.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from faces_support import FIXTURES, SINGLE_PORTRAIT
from PIL import Image

from faces import FacePipeline, ModelPaths, PipelineConfig, locate_models
from faces.models import MODELS_DIRECTORY_VARIABLE


@pytest.fixture(scope="session")
def models() -> ModelPaths:
    directory = os.environ.get(MODELS_DIRECTORY_VARIABLE)
    if not directory:
        pytest.skip(
            f"sin {MODELS_DIRECTORY_VARIABLE}: baja los modelos con "
            "`python -m faces models --salida <carpeta>` y define la variable"
        )
    return locate_models(Path(directory))


@pytest.fixture(scope="session")
def pipeline(models: ModelPaths) -> FacePipeline:
    return FacePipeline(models, PipelineConfig())


@pytest.fixture
def photo() -> Callable[[str], bytes]:
    return lambda name: (FIXTURES / name).read_bytes()


@pytest.fixture
def portrait() -> Image.Image:
    with Image.open(FIXTURES / SINGLE_PORTRAIT) as image:
        return image.copy()
