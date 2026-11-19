"""La configuración del servicio y de la ingesta, leída del entorno con el prefijo `RAG_`.

Además de lo común a todo servicio (`RAG_ENGINE_URL`, `RAG_ENGINE_KEY`,
`RAG_MIGRATION_KEY`... ver `apps/kit/README.md`):

| Variable | Para qué | Por defecto |
|---|---|---|
| `RAG_MODELS_DIR` | carpeta del modelo de embeddings (`python -m rag.models`) | **obligatoria** |
| `RAG_EMBEDDING_MODEL` | `int8` o `fp32` | `int8` |
| `RAG_EMBEDDING_THREADS` | hilos de onnxruntime | los núcleos de la máquina |
| `RAG_EMBEDDING_BATCH_SIZE` | secuencias por llamada al modelo | `32` |
| `RAG_DATA_DIR` | carpeta de datasets con `rag_papers/` (solo la ingesta) | ninguna |
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from appkit import ServiceSettings

from .embeddings import DEFAULT_BATCH_SIZE, E5Embedder, EmbedderSettings
from .models import DEFAULT_MODEL_VARIANT, ModelVariant, locate_model

ENV_PREFIX = "RAG_"


class RagSettings(ServiceSettings):
    """La configuración del servicio de preguntas a papers y de su ingesta."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True, extra="ignore")

    models_dir: Path
    embedding_model: ModelVariant = DEFAULT_MODEL_VARIANT
    embedding_threads: int = Field(default=EmbedderSettings().threads, ge=1)
    embedding_batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1)
    data_dir: Path | None = None

    def embedder(self) -> EmbedderSettings:
        return EmbedderSettings(
            threads=self.embedding_threads, batch_size=self.embedding_batch_size
        )


def load_embedder(settings: RagSettings) -> E5Embedder:
    """El modelo de `settings`, que ya tiene que estar en `RAG_MODELS_DIR`.

    Raises:
        ModelNotFoundError: si no está.
    """
    files = locate_model(settings.models_dir, settings.embedding_model)
    return E5Embedder(files, settings.embedder())
