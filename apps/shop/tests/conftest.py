"""Fixtures de la tienda: la red falsa del dataset y los modelos de CLIP.

Los modelos (577 MiB) no se versionan: se buscan en `SHOP_MODELS_DIR`. Sin la variable,
los tests que los necesitan se saltan diciendo cómo bajarlos; con la variable y sin los
modelos, fallan, para que la CI —que la define— nunca los salte en silencio.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from apps.shop.tests.shop_support import FakeTransport

from shop.clip import ClipImageEncoder, ClipTextEncoder, ClipTokenizer, ModelPaths, locate_models
from shop.clip.models import MODELS_DIRECTORY_VARIABLE


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture(scope="session")
def clip_models() -> ModelPaths:
    directory = os.environ.get(MODELS_DIRECTORY_VARIABLE)
    if not directory:
        pytest.skip(
            f"sin {MODELS_DIRECTORY_VARIABLE}: baja CLIP con "
            "`python -m shop.clip models --salida <carpeta>` y define la variable"
        )
    return locate_models(Path(directory))


@pytest.fixture(scope="session")
def clip_tokenizer(clip_models: ModelPaths) -> ClipTokenizer:
    return ClipTokenizer(clip_models.tokenizer)


@pytest.fixture(scope="session")
def image_encoder(clip_models: ModelPaths) -> ClipImageEncoder:
    return ClipImageEncoder(clip_models.vision)


@pytest.fixture(scope="session")
def text_encoder(clip_models: ModelPaths, clip_tokenizer: ClipTokenizer) -> ClipTextEncoder:
    return ClipTextEncoder(clip_models.text, clip_tokenizer)
