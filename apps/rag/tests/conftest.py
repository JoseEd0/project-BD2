"""El modelo de embeddings, cargado una vez por sesión para cada variante."""

from __future__ import annotations

import pytest
from rag_models_support import loaded_embedder

from rag.embeddings import E5Embedder
from rag.models import ModelVariant


@pytest.fixture(scope="session", params=list(ModelVariant), ids=str)
def embedder(request: pytest.FixtureRequest) -> E5Embedder:
    variant: ModelVariant = request.param
    return loaded_embedder(variant)
