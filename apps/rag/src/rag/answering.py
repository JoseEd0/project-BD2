"""Responder una pregunta con los fragmentos del corpus: recuperar, armar el prompt, pedir la
respuesta en streaming y comprobar sus citas.

`Answerer.prepare` hace lo que puede fallar antes de responder —la búsqueda híbrida en el
gestor y la reserva del gasto del día— y lanza sus errores como cualquier ruta (JSON con su
estado). `Answerer.events` es el cuerpo SSE; una vez empezado, un fallo del modelo llega como
evento `error` y el stream se cierra limpio. Los eventos, en orden:

- `sources`, primero: la pregunta, el modelo y los fragmentos numerados (`n`, paper, páginas
  y texto);
- `delta`, por cada trozo de texto: `{"text": "…"}`;
- `fallback`, si la API pasó la respuesta a otro modelo: `{"from_model", "to_model"}`;
- `done`, al terminar: la respuesta, `stop_reason`, si está completa, las citas válidas e
  inválidas, los tokens de cada intento y el costo (`DoneData`);
- `error`, si el modelo falla: `{"error", "kind", "request_id"}`, como los errores del kit.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from minigestor_client import AsyncClient, current_request_id

from .budget import DailyBudget, Reservation
from .citations import Citation, check_citations
from .embeddings import Embedder
from .llm import (
    AnswerModel,
    Completion,
    LlmError,
    LlmUnavailableError,
    ModelSwitch,
    StopReason,
    TextDelta,
)
from .pricing import Attempt, TokenUsage, attempt_cost, total_cost, total_usage, worst_case_cost
from .prompt import PASSAGE_SHARE, Prompt, Source, build_prompt, select_sources
from .search import Paper, Retriever, SearchMode
from .sse import sse_frame

SOURCES_EVENT = "sources"
DELTA_EVENT = "delta"
FALLBACK_EVENT = "fallback"
DONE_EVENT = "done"
ERROR_EVENT = "error"

NO_SOURCES_ANSWER = "No hay documentos en el corpus para responder esta pregunta."
INCOMPLETE_STREAM_MESSAGE = "el modelo de lenguaje cortó la respuesta sin terminarla"
SERVICE_FAILURE_KIND = "ServiceError"
SERVICE_FAILURE_MESSAGE = "error interno del servicio"
MILLISECONDS_PER_SECOND = 1000.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnswerSettings:
    """Cuántos fragmentos se recuperan, cuántos tokens de documentos caben en el prompt, el
    tope de salida de cada respuesta y la constante de RRF de la búsqueda híbrida."""

    chunks: int
    context_tokens: int
    max_tokens: int
    rrf_k: int

    @property
    def passage_tokens(self) -> int:
        """El tope de un pasaje: `PASSAGE_SHARE` veces su parte del contexto."""
        return PASSAGE_SHARE * self.context_tokens // self.chunks


class SourceData(BaseModel):
    model_config = ConfigDict(frozen=True)

    n: int
    chunk_id: str
    paper: Paper
    page_start: int
    page_end: int
    text: str


class SourcesData(BaseModel):
    question: str
    model: str
    sources: list[SourceData]


class DeltaData(BaseModel):
    text: str


class FallbackData(BaseModel):
    from_model: str
    to_model: str


class UsageData(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


class AttemptData(BaseModel):
    model: str
    usage: UsageData
    cost_usd: float | None


class DoneData(BaseModel):
    """El cierre de una respuesta. `complete` es falso si el modelo la cortó (`max_tokens`)
    o la rechazó (`refusal`): ese texto parcial no se muestra como respuesta.
    `answered_from_documents` es falso si no citó ningún fragmento enviado. `cost_usd` es
    `None` si algún modelo que intervino no tiene precio en la tabla."""

    model: str
    stop_reason: StopReason
    complete: bool
    refusal_category: str | None
    answer: str
    citations: list[Citation]
    invalid_citations: list[int]
    answered_from_documents: bool
    usage: UsageData
    attempts: list[AttemptData]
    cost_usd: float | None


class ErrorData(BaseModel):
    error: str
    kind: str
    request_id: str | None


@dataclass(frozen=True, slots=True)
class PreparedAnswer:
    """Una pregunta lista para el modelo: sus fragmentos, su prompt y su reserva de gasto
    (`None` si no hay fragmentos y el modelo no se llama)."""

    question: str
    sources: tuple[Source, ...]
    prompt: Prompt
    reservation: Reservation | None


class Answerer:
    """Responde preguntas con el gestor, el modelo de embeddings y un modelo de lenguaje."""

    def __init__(
        self,
        model: AnswerModel,
        budget: DailyBudget,
        settings: AnswerSettings,
    ) -> None:
        self._model = model
        self._budget = budget
        self._settings = settings

    async def prepare(
        self, question: str, engine: AsyncClient, embedder: Embedder
    ) -> PreparedAnswer:
        """Recupera los fragmentos de `question`, arma el prompt y reserva su gasto.

        Raises:
            BudgetExhaustedError: si el gasto del día no admite una respuesta más.
            minigestor_client.MinigestorError: si el gestor falla al buscar.
        """
        settings = self._settings
        retriever = Retriever(engine, embedder, rrf_k=settings.rrf_k)
        hits = await retriever.search(question, SearchMode.HYBRID, settings.chunks)
        sources = tuple(select_sources(hits, settings.context_tokens, settings.passage_tokens))
        prompt = build_prompt(question, sources)
        reservation = self._reserve(prompt) if sources else None
        return PreparedAnswer(question, sources, prompt, reservation)

    async def events(self, prepared: PreparedAnswer) -> AsyncGenerator[bytes, None]:
        """El cuerpo SSE de la respuesta a `prepared`."""
        yield sse_frame(SOURCES_EVENT, _sources_data(prepared, self._model.name))
        if prepared.reservation is None:
            yield sse_frame(DONE_EVENT, _without_sources())
            return
        started = time.perf_counter()
        reservation = prepared.reservation
        try:
            async for frame in self._generate(prepared, reservation, started):
                yield frame
        finally:
            if not reservation.settled:
                logger.info(
                    "respuesta cortada antes de terminar; queda reservado %.6f USD",
                    reservation.amount,
                )
                reservation.settle(None)

    async def _generate(
        self, prepared: PreparedAnswer, reservation: Reservation, started: float
    ) -> AsyncGenerator[bytes, None]:
        parts: list[str] = []
        completion: Completion | None = None
        try:
            async for event in self._model.stream(prepared.prompt, self._settings.max_tokens):
                if isinstance(event, TextDelta):
                    parts.append(event.text)
                    yield sse_frame(DELTA_EVENT, DeltaData(text=event.text))
                elif isinstance(event, ModelSwitch):
                    yield sse_frame(FALLBACK_EVENT, _fallback_data(event))
                else:
                    completion = event
            if completion is None:
                raise LlmUnavailableError(INCOMPLETE_STREAM_MESSAGE)
        except LlmError as error:
            logger.warning("respuesta fallida: %s: %s", error.kind, error.message)
            yield sse_frame(ERROR_EVENT, _error_data(error.message, error.kind))
            return
        except Exception:
            logger.exception("fallo no previsto generando una respuesta")
            yield sse_frame(ERROR_EVENT, _error_data(SERVICE_FAILURE_MESSAGE, SERVICE_FAILURE_KIND))
            return
        done = _done_data("".join(parts), completion, prepared.sources)
        reservation.settle(done.cost_usd)
        _log_answer(done, len(prepared.sources), started)
        yield sse_frame(DONE_EVENT, done)

    def _reserve(self, prompt: Prompt) -> Reservation:
        return self._budget.reserve(
            worst_case_cost(self._model.name, prompt.estimated_tokens, self._settings.max_tokens)
        )


def _sources_data(prepared: PreparedAnswer, model: str) -> SourcesData:
    return SourcesData(
        question=prepared.question,
        model=model,
        sources=[
            SourceData(
                n=source.n,
                chunk_id=source.hit.chunk_id,
                paper=source.hit.paper,
                page_start=source.hit.page_start,
                page_end=source.hit.page_end,
                text=source.hit.text,
            )
            for source in prepared.sources
        ],
    )


def _without_sources() -> DoneData:
    return DoneData(
        model="",
        stop_reason=StopReason.END_TURN,
        complete=True,
        refusal_category=None,
        answer=NO_SOURCES_ANSWER,
        citations=[],
        invalid_citations=[],
        answered_from_documents=False,
        usage=_usage_data(TokenUsage()),
        attempts=[],
        cost_usd=0.0,
    )


def _done_data(answer: str, completion: Completion, sources: Sequence[Source]) -> DoneData:
    report = check_citations(answer, sources)
    return DoneData(
        model=completion.model,
        stop_reason=completion.stop_reason,
        complete=completion.stop_reason is StopReason.END_TURN,
        refusal_category=completion.refusal_category,
        answer=answer,
        citations=report.citations,
        invalid_citations=report.invalid,
        answered_from_documents=bool(report.citations),
        usage=_usage_data(total_usage(completion.attempts)),
        attempts=[_attempt_data(attempt) for attempt in completion.attempts],
        cost_usd=total_cost(completion.attempts),
    )


def _attempt_data(attempt: Attempt) -> AttemptData:
    return AttemptData(
        model=attempt.model, usage=_usage_data(attempt.usage), cost_usd=attempt_cost(attempt)
    )


def _usage_data(usage: TokenUsage) -> UsageData:
    return UsageData(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_tokens,
        cache_read_input_tokens=usage.cache_read_tokens,
    )


def _fallback_data(switch: ModelSwitch) -> FallbackData:
    return FallbackData(from_model=switch.from_model, to_model=switch.to_model)


def _error_data(message: str, kind: str) -> ErrorData:
    return ErrorData(error=message, kind=kind, request_id=current_request_id())


def _log_answer(done: DoneData, sources: int, started: float) -> None:
    usage = done.usage
    logger.info(
        "respuesta: modelo=%s stop_reason=%s fuentes=%d citas=%d citas_invalidas=%d "
        "entrada=%d salida=%d cache_escrita=%d cache_leida=%d costo_usd=%s duracion_ms=%.0f",
        done.model,
        done.stop_reason.value,
        sources,
        len(done.citations),
        len(done.invalid_citations),
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_creation_input_tokens,
        usage.cache_read_input_tokens,
        "desconocido" if done.cost_usd is None else f"{done.cost_usd:.6f}",
        (time.perf_counter() - started) * MILLISECONDS_PER_SECOND,
    )
