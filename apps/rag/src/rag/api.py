"""El API del servicio de preguntas a papers. Por ahora, solo recuperación: `POST /search`.

    uvicorn --factory rag.api:create_app --host 0.0.0.0 --port 8000

Sin estado: el gestor guarda los fragmentos y sus índices, y cada réplica carga el modelo
de embeddings al arrancar (`RAG_MODELS_DIR`). Además de las rutas de `appkit` (`/health`,
`/ready`, migraciones al arrancar, `X-Request-ID` y `Server-Timing`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, Field, StringConstraints

from appkit import Engine, configure_logging, create_service_app, load_settings

from .config import RagSettings, load_embedder
from .embeddings import Embedder
from .schema import SCHEMA
from .search import DEFAULT_RESULTS, MAX_RESULTS, Retriever, SearchHit, SearchMode

NAME = "rag"
VERSION = "0.1.0"
# Una pregunta es una o dos oraciones; mil caracteres dejan pegar un párrafo sin que una
# petición pueda mandar un documento entero al modelo.
MAX_QUERY_CHARACTERS = 1000

EmbedderFactory = Callable[[RagSettings], Embedder]


class SearchRequest(BaseModel):
    query: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUERY_CHARACTERS)
    ]
    mode: SearchMode = SearchMode.HYBRID
    k: int = Field(default=DEFAULT_RESULTS, ge=1, le=MAX_RESULTS)


class SearchResponse(BaseModel):
    query: str
    mode: SearchMode
    results: list[SearchHit]


def embedder_of(request: Request) -> Embedder:
    embedder: Embedder = request.app.state.embedder
    return embedder


router = APIRouter()


@router.post("/search")
async def search(
    body: SearchRequest, engine: Engine, embedder: Annotated[Embedder, Depends(embedder_of)]
) -> SearchResponse:
    """Los `k` fragmentos más relevantes para `query`, con su paper, sus páginas y sus
    puntajes, de mejor a peor."""
    hits = await Retriever(engine, embedder).search(body.query, body.mode, body.k)
    return SearchResponse(query=body.query, mode=body.mode, results=hits)


def create_app(
    settings: RagSettings | None = None,
    *,
    embedder_factory: EmbedderFactory = load_embedder,
    engine_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """La aplicación del servicio. Sin `settings`, la lee del entorno y configura los logs
    (así la llama uvicorn con `--factory`); los tests le dan su configuración, su modelo y
    su transporte hacia el gestor.

    Raises:
        appkit.SettingsError: si la configuración del entorno está incompleta.
    """
    if settings is None:
        settings = load_settings(RagSettings)
        configure_logging(settings.log_level)
    configured = settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.embedder = embedder_factory(configured)
        yield

    app = create_service_app(
        settings,
        name=NAME,
        version=VERSION,
        migrations=SCHEMA,
        lifespan=lifespan,
        engine_transport=engine_transport,
    )
    app.include_router(router)
    return app
