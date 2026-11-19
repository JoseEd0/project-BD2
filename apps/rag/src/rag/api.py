"""El API del servicio de preguntas a papers.

    uvicorn --factory rag.api:create_app --host 0.0.0.0 --port 8000

- `POST /search`: los fragmentos más relevantes en modo BM25, vectorial o híbrido, con el
  SQL de cada consulta que corrió.
- `POST /answers`: la respuesta de Claude con citas `[n]`, en Server-Sent Events.
- `GET /papers/{id}/pages/{n}` y `GET /papers/{id}/file`: la página citada y su archivo.
- `GET /facets`: los años y las categorías del corpus, con cuántos papers tiene cada uno.

Sin estado: el gestor guarda los fragmentos y sus índices, y cada réplica carga el modelo
de embeddings al arrancar (`RAG_MODELS_DIR`). Además de las rutas de `appkit` (`/health`,
`/ready`, migraciones al arrancar, `X-Request-ID` y `Server-Timing`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from http import HTTPStatus
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, Path, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StringConstraints

from appkit import Engine, ServiceError, configure_logging, create_service_app, load_settings

from .answering import Answerer
from .budget import DailyBudget
from .config import RagSettings, load_embedder, open_answer_model
from .embeddings import Embedder
from .facets import Facets, facets_of
from .llm import AnswerModel
from .papers import PageResponse, corpus_file, page_of, stored_paper
from .schema import ID_LENGTH, SCHEMA
from .search import (
    DEFAULT_RESULTS,
    MAX_RESULTS,
    ExecutedQuery,
    Retriever,
    SearchFilters,
    SearchHit,
    SearchMode,
)
from .sse import EVENT_STREAM_MEDIA_TYPE, EventStreamResponse

NAME = "rag"
VERSION = "0.1.0"
# Una pregunta es una o dos oraciones; mil caracteres dejan pegar un párrafo sin que una
# petición pueda mandar un documento entero al modelo.
MAX_QUERY_CHARACTERS = 1000
# Los identificadores del corpus: arXiv nuevos (`2501.01234`), antiguos (`cs/0101001`) y los
# del material del curso (`curso:informe`).
PAPER_ID_PATTERN = rf"^[A-Za-z0-9._:/-]{{1,{ID_LENGTH}}}$"
INLINE_DISPOSITION = "inline"

EmbedderFactory = Callable[[RagSettings], Embedder]
AnswerModelFactory = Callable[[RagSettings], AbstractAsyncContextManager[AnswerModel | None]]
Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUERY_CHARACTERS)
]
PaperId = Annotated[str, Path(pattern=PAPER_ID_PATTERN)]


class AnswersUnavailableError(ServiceError):
    """El servicio no tiene un modelo de lenguaje configurado."""

    status = HTTPStatus.SERVICE_UNAVAILABLE


class SearchRequest(BaseModel):
    query: Question
    mode: SearchMode = SearchMode.HYBRID
    k: int = Field(default=DEFAULT_RESULTS, ge=1, le=MAX_RESULTS)
    filters: SearchFilters | None = None


class SearchResponse(BaseModel):
    query: str
    mode: SearchMode
    results: list[SearchHit]
    queries: list[ExecutedQuery]


class AnswerRequest(BaseModel):
    question: Question
    filters: SearchFilters | None = None


def embedder_of(request: Request) -> Embedder:
    embedder: Embedder = request.app.state.embedder
    return embedder


def settings_of(request: Request) -> RagSettings:
    settings: RagSettings = request.app.state.settings
    return settings


def answerer_of(request: Request) -> Answerer:
    answerer: Answerer | None = request.app.state.answerer
    if answerer is None:
        raise AnswersUnavailableError(
            "las respuestas no están configuradas: falta la clave del modelo de lenguaje"
        )
    return answerer


router = APIRouter()


@router.post("/search")
async def search(
    body: SearchRequest,
    engine: Engine,
    embedder: Annotated[Embedder, Depends(embedder_of)],
    settings: Annotated[RagSettings, Depends(settings_of)],
) -> SearchResponse:
    """Los `k` fragmentos más relevantes para `query`, con su paper, sus páginas y sus
    puntajes, de mejor a peor, solo de los papers que cumplen `filters`; y el SQL de cada
    consulta que corrió en el gestor."""
    retriever = Retriever(engine, embedder, rrf_k=settings.rrf_k)
    hits = await retriever.search(body.query, body.mode, body.k, body.filters)
    return SearchResponse(
        query=body.query, mode=body.mode, results=hits, queries=retriever.executed
    )


@router.get("/facets")
async def facets(engine: Engine) -> Facets:
    """Los años y las categorías primarias del corpus con cuántos papers tiene cada uno: los
    valores que admiten los filtros de `/search` y `/answers`."""
    return await facets_of(engine)


@router.post(
    "/answers",
    response_class=EventStreamResponse,
    responses={HTTPStatus.OK.value: {"content": {EVENT_STREAM_MEDIA_TYPE: {}}}},
)
async def answers(
    body: AnswerRequest,
    engine: Engine,
    embedder: Annotated[Embedder, Depends(embedder_of)],
    answerer: Annotated[Answerer, Depends(answerer_of)],
) -> EventStreamResponse:
    """La respuesta a `question` en Server-Sent Events: `sources`, `delta`… y `done` (o
    `error`). Ver `rag.answering`. La búsqueda y el tope de gasto se resuelven antes de
    empezar: sus errores son respuestas JSON con su estado."""
    prepared = await answerer.prepare(body.question, engine, embedder, body.filters)
    return EventStreamResponse(answerer.events(prepared))


@router.get("/papers/{paper_id:path}/pages/{page}")
async def paper_page(
    paper_id: PaperId, page: Annotated[int, Path(ge=1)], engine: Engine
) -> PageResponse:
    """La página `page` del documento: el paper, su total de páginas, los pasajes del corpus
    que caen en ella y la URL para abrir el archivo en esa página."""
    return await page_of(engine, paper_id, page)


@router.get(
    "/papers/{paper_id:path}/file",
    response_class=FileResponse,
    responses={HTTPStatus.PARTIAL_CONTENT.value: {"description": "el rango pedido con `Range`"}},
)
async def paper_file(
    paper_id: PaperId,
    engine: Engine,
    settings: Annotated[RagSettings, Depends(settings_of)],
) -> FileResponse:
    """El archivo del documento (PDF o Markdown) desde el corpus montado, con `Range`."""
    stored = await stored_paper(engine, paper_id)
    path = corpus_file(settings.corpus_root, stored.paper)
    return FileResponse(
        path,
        media_type=stored.paper.media_type,
        filename=path.name,
        content_disposition_type=INLINE_DISPOSITION,
    )


def create_app(
    settings: RagSettings | None = None,
    *,
    embedder_factory: EmbedderFactory = load_embedder,
    answer_model_factory: AnswerModelFactory = open_answer_model,
    engine_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """La aplicación del servicio. Sin `settings`, la lee del entorno y configura los logs
    (así la llama uvicorn con `--factory`); los tests le dan su configuración, sus modelos y
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
        app.state.settings = configured
        app.state.embedder = embedder_factory(configured)
        async with answer_model_factory(configured) as model:
            app.state.answerer = (
                None
                if model is None
                else Answerer(
                    model, DailyBudget(configured.daily_budget_usd), configured.answering()
                )
            )
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
