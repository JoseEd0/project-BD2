"""La configuración del servicio y de la ingesta, leída del entorno con el prefijo `RAG_`.

Además de lo común a todo servicio (`RAG_ENGINE_URL`, `RAG_ENGINE_KEY`,
`RAG_MIGRATION_KEY`... ver `apps/kit/README.md`):

| Variable | Para qué | Por defecto |
|---|---|---|
| `RAG_MODELS_DIR` | carpeta del modelo de embeddings (`python -m rag.models`) | **obligatoria** |
| `RAG_EMBEDDING_MODEL` | `int8` o `fp32` | `int8` |
| `RAG_EMBEDDING_THREADS` | hilos de onnxruntime | los núcleos de la máquina |
| `RAG_EMBEDDING_BATCH_SIZE` | secuencias por llamada al modelo | `32` |
| `RAG_DATA_DIR` | carpeta de datasets con `rag_papers/` (ingesta y archivos de papers) | ninguna |
| `RAG_EMBEDDING_CACHE_DIR` | caché de embeddings (solo la ingesta) | `rag_papers/embeddings` |
| `RAG_LLM_PROVIDER` | `anthropic` o `simulated` (sin red, para tests y CI) | `anthropic` |
| `RAG_ANTHROPIC_API_KEY` | clave de la API de Anthropic; sin ella, `/answers` da 503 | ninguna |
| `RAG_ANSWER_MODEL` | el modelo que responde | `claude-sonnet-5-5` |
| `RAG_ANSWER_EFFORT` | `low`, `medium`, `high`, `xhigh` o `max` | `low` |
| `RAG_ANSWER_SERVER_FALLBACK` | respaldo del servidor ante un rechazo | `true` |
| `RAG_ANSWER_MAX_TOKENS` | tope de salida (razonamiento incluido) por respuesta | `2048` |
| `RAG_ANSWER_CHUNKS` | fragmentos que recupera la búsqueda híbrida | `8` |
| `RAG_ANSWER_CONTEXT_TOKENS` | tokens estimados de documentos que caben en el prompt | `6000` |
| `RAG_RRF_K` | constante de Reciprocal Rank Fusion | `60` |
| `RAG_DAILY_BUDGET_USD` | gasto máximo del día en el modelo, por réplica | `5` |
| `RAG_ANTHROPIC_TIMEOUT_SECONDS` | plazo de la API de Anthropic | `60` |
| `RAG_ANTHROPIC_MAX_RETRIES` | reintentos del SDK (429, 5xx, red) | `2` |
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path

import anthropic
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

from appkit import ServiceSettings

from .answering import AnswerSettings
from .claude import ClaudeModel, ClaudeOptions, Effort
from .embeddings import DEFAULT_BATCH_SIZE, E5Embedder, EmbedderSettings
from .fusion import RRF_K
from .llm import AnswerModel
from .models import DEFAULT_MODEL_VARIANT, ModelVariant, locate_model
from .pricing import PRICES, SONNET_5_5
from .search import MAX_RESULTS
from .settings import CORPUS_DIRECTORY
from .simulated import SimulatedModel

ENV_PREFIX = "RAG_"
# Respuestas de uno a tres párrafos (unos 300 a 600 tokens) más el razonamiento que deja el
# esfuerzo `low`, que en preguntas simples suele ser ninguno. Por medir con la clave (README).
DEFAULT_ANSWER_MAX_TOKENS = 2048
# El tope de salida de Sonnet 5.5 en la API de mensajes.
MAX_OUTPUT_TOKENS = 128_000
# Ocho fragmentos de unas 220 palabras son unos 4 000 tokens estimados con su marcado; 6 000
# deja entrar los ocho aunque alguno llegue a las 500 palabras.
DEFAULT_ANSWER_CHUNKS = 8
DEFAULT_ANSWER_CONTEXT_TOKENS = 6000
DEFAULT_ANSWER_EFFORT: Effort = "low"
# La cota de una respuesta con los valores por omisión es de unos 0,05 USD: al menos cien
# preguntas al día; con el costo típico, varias veces más.
DEFAULT_DAILY_BUDGET_USD = 5.0
DEFAULT_ANTHROPIC_TIMEOUT_SECONDS = 60.0
DEFAULT_ANTHROPIC_MAX_RETRIES = 2

logger = logging.getLogger(__name__)


class LlmProvider(StrEnum):
    ANTHROPIC = "anthropic"
    SIMULATED = "simulated"


class RagSettings(ServiceSettings):
    """La configuración del servicio de preguntas a papers y de su ingesta."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True, extra="ignore")

    models_dir: Path
    embedding_model: ModelVariant = DEFAULT_MODEL_VARIANT
    embedding_threads: int = Field(default=EmbedderSettings().threads, ge=1)
    embedding_batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1)
    data_dir: Path | None = None
    embedding_cache_dir: Path | None = None
    llm_provider: LlmProvider = LlmProvider.ANTHROPIC
    anthropic_api_key: SecretStr | None = None
    answer_model: str = SONNET_5_5
    answer_effort: Effort = DEFAULT_ANSWER_EFFORT
    answer_server_fallback: bool = True
    answer_max_tokens: int = Field(default=DEFAULT_ANSWER_MAX_TOKENS, ge=1, le=MAX_OUTPUT_TOKENS)
    answer_chunks: int = Field(default=DEFAULT_ANSWER_CHUNKS, ge=1, le=MAX_RESULTS)
    answer_context_tokens: int = Field(default=DEFAULT_ANSWER_CONTEXT_TOKENS, ge=1)
    rrf_k: int = Field(default=RRF_K, ge=0)
    daily_budget_usd: float = Field(default=DEFAULT_DAILY_BUDGET_USD, ge=0)
    anthropic_timeout_seconds: float = Field(default=DEFAULT_ANTHROPIC_TIMEOUT_SECONDS, gt=0)
    anthropic_max_retries: int = Field(default=DEFAULT_ANTHROPIC_MAX_RETRIES, ge=0)

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def blank_key_is_missing(cls, value: object) -> object:
        """El compose pasa la variable vacía cuando `.env` no la define: es no tenerla."""
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("answer_model")
    @classmethod
    def priced_model(cls, model: str) -> str:
        """Sin precio no hay costo por respuesta ni tope diario: el servicio no arranca."""
        if model not in PRICES:
            raise ValueError(f"{model} no está en la tabla de precios de rag.pricing")
        return model

    def embedder(self) -> EmbedderSettings:
        return EmbedderSettings(
            threads=self.embedding_threads, batch_size=self.embedding_batch_size
        )

    def answering(self) -> AnswerSettings:
        return AnswerSettings(
            chunks=self.answer_chunks,
            context_tokens=self.answer_context_tokens,
            max_tokens=self.answer_max_tokens,
            rrf_k=self.rrf_k,
        )

    @property
    def corpus_root(self) -> Path | None:
        """La carpeta `rag_papers/` del corpus, si el servicio la tiene montada."""
        return self.data_dir / CORPUS_DIRECTORY if self.data_dir is not None else None


def load_embedder(settings: RagSettings) -> E5Embedder:
    """El modelo de `settings`, que ya tiene que estar en `RAG_MODELS_DIR`.

    Raises:
        ModelNotFoundError: si no está.
    """
    files = locate_model(settings.models_dir, settings.embedding_model)
    return E5Embedder(files, settings.embedder())


@asynccontextmanager
async def open_answer_model(settings: RagSettings) -> AsyncIterator[AnswerModel | None]:
    """El modelo de lenguaje de `settings` mientras dura el bloque, o `None` si el proveedor
    es Anthropic y no hay clave: el servicio busca igual y `/answers` responde 503.

    La clave se pasa explícita al cliente: el SDK no la busca en `ANTHROPIC_API_KEY` ni en
    perfiles del sistema, así que el servicio solo usa la de su propia variable."""
    if settings.llm_provider is LlmProvider.SIMULATED:
        yield SimulatedModel()
        return
    if settings.anthropic_api_key is None:
        logger.warning("sin RAG_ANTHROPIC_API_KEY: /answers no responde hasta configurarla")
        yield None
        return
    async with anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.anthropic_timeout_seconds,
        max_retries=settings.anthropic_max_retries,
    ) as client:
        yield ClaudeModel(client, _claude_options(settings))


def _claude_options(settings: RagSettings) -> ClaudeOptions:
    return ClaudeOptions(
        model=settings.answer_model,
        effort=settings.answer_effort,
        server_fallback=settings.answer_server_fallback,
    )
