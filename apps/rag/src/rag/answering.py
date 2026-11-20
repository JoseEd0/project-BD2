"""Responder una pregunta con los fragmentos del corpus: recuperar, armar el prompt, pedir la
respuesta en streaming y comprobar sus citas.

`Answerer.prepare` hace lo que puede fallar antes de responder —la reescritura de la consulta
(`rag.rewriting`, que vuelve a la pregunta si falla), la búsqueda híbrida en el gestor y la
reserva del gasto del día— y lanza sus errores como cualquier ruta (JSON con su
estado). `Answerer.events` es el cuerpo SSE; una vez empezado, un fallo del modelo llega como
evento `error` y el stream se cierra limpio. Los eventos, en orden:

- `sources`, primero: la pregunta, lo que se buscó (`search_query`), el modelo y los
  fragmentos numerados (`n`, paper, páginas y texto);
- `delta`, por cada trozo de texto: `{"text": "…"}`;
- `fallback`, si la API pasó la respuesta a otro modelo: `{"from_model", "to_model"}`;
- `done`, al terminar: la respuesta, `stop_reason`, si está completa, las citas válidas e
  inválidas, los tokens de cada llamada al modelo (la reescritura incluida) y el costo
  (`DoneData`);
- `error`, si el modelo falla: `{"error", "kind", "request_id"}`, como los errores del kit.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from minigestor_client import current_request_id

from .budget import DailyBudget, Reservation
from .citations import Citation, check_citations
from .conversation import Exchange, bounded_history, is_small_talk, retrieval_query
from .embeddings import Embedder
from .engine_reader import EngineReader
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
from .prompt import (
    PASSAGE_SHARE,
    Prompt,
    Source,
    build_conversational_prompt,
    build_prompt,
    select_sources,
)
from .rewriting import QueryRewriter, estimated_rewrite_tokens
from .search import Paper, Retriever, SearchFilters, SearchMode
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
# Seis turnos de unos 2 000 caracteres son unos 4 000 tokens más por pregunta (~0,01 USD sin
# caché): bastan para seguir un hilo sin que una conversación larga encarezca cada pregunta.
DEFAULT_HISTORY_TURNS = 6
DEFAULT_HISTORY_CHARACTERS = 2000
# «¿Y cuál es más rápido?» o «explícalo más simple» no nombran su tema; una pregunta de ocho
# palabras o más ya suele nombrarlo.
DEFAULT_FOLLOW_UP_WORDS = 8
# Haiku 5.5 escribe una consulta de 30 palabras en menos de un segundo; pasado el plazo se
# busca con la pregunta tal cual: la reescritura nunca demora la respuesta más que esto.
DEFAULT_REWRITE_TIMEOUT_SECONDS = 2.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnswerSettings:
    """Cuántos fragmentos se recuperan, cuántos tokens de documentos caben en el prompt, el
    tope de salida de cada respuesta, la constante de RRF de la búsqueda híbrida y cuánto de
    la conversación se reenvía: los últimos `history_turns` turnos, cada respuesta cortada en
    `history_characters`, y la pregunta anterior junto a una de menos de `follow_up_words`
    palabras al buscar."""

    chunks: int
    context_tokens: int
    max_tokens: int
    rrf_k: int
    history_turns: int = DEFAULT_HISTORY_TURNS
    history_characters: int = DEFAULT_HISTORY_CHARACTERS
    follow_up_words: int = DEFAULT_FOLLOW_UP_WORDS
    rewrite_timeout_seconds: float = DEFAULT_REWRITE_TIMEOUT_SECONDS

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
    """`search_query`: lo que se buscó en el corpus (la consulta reescrita, o la pregunta)."""

    question: str
    search_query: str
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
    `answered_from_documents` es falso si no citó ningún fragmento enviado; `conversational`,
    verdadero si el mensaje era un saludo o una pregunta sobre la app y no se buscó nada en el
    corpus. `cost_usd` es
    `None` si algún modelo que intervino no tiene precio en la tabla."""

    model: str
    stop_reason: StopReason
    complete: bool
    refusal_category: str | None
    answer: str
    citations: list[Citation]
    invalid_citations: list[int]
    answered_from_documents: bool
    conversational: bool = False
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
    (`None` si no hay fragmentos ni es una charla, y el modelo no se llama); lo que se buscó
    y, si se reescribió la consulta, lo que costó."""

    question: str
    sources: tuple[Source, ...]
    prompt: Prompt
    reservation: Reservation | None
    search_query: str = ""
    rewrite_attempts: tuple[Attempt, ...] = ()


class Answerer:
    """Responde preguntas con el gestor, el modelo de embeddings y un modelo de lenguaje."""

    def __init__(
        self,
        model: AnswerModel,
        budget: DailyBudget,
        settings: AnswerSettings,
        rewriter: QueryRewriter | None = None,
    ) -> None:
        self._model = model
        self._budget = budget
        self._settings = settings
        self._rewriter = rewriter

    async def prepare(
        self,
        question: str,
        engine: EngineReader,
        embedder: Embedder,
        filters: SearchFilters | None = None,
        history: Sequence[Exchange] = (),
    ) -> PreparedAnswer:
        """Recupera los fragmentos de `question` (solo de los papers que cumplen `filters`),
        arma el prompt después de los turnos de `history` y reserva su gasto. Un saludo o una
        pregunta sobre la app no se busca: va al modelo sin documentos. Con un `rewriter`, se
        busca la consulta que escribe; si falla o pasa su plazo, la pregunta de siempre.

        Raises:
            BudgetExhaustedError: si el gasto del día no admite una respuesta más.
            minigestor_client.MinigestorError: si el gestor falla al buscar.
        """
        settings = self._settings
        earlier = bounded_history(history, settings.history_turns, settings.history_characters)
        if is_small_talk(question):
            prompt = build_conversational_prompt(question, earlier)
            return PreparedAnswer(question, (), prompt, self._reserve(prompt), question)
        fallback = retrieval_query(question, earlier, settings.follow_up_words)
        query, rewrite_attempts = await self._search_query(question, earlier, fallback)
        retriever = Retriever(engine, embedder, rrf_k=settings.rrf_k)
        hits = await retriever.search(query, SearchMode.HYBRID, settings.chunks, filters)
        sources = tuple(select_sources(hits, settings.context_tokens, settings.passage_tokens))
        prompt = build_prompt(question, sources, earlier)
        reservation = self._reserve(prompt) if sources else None
        return PreparedAnswer(question, sources, prompt, reservation, query, rewrite_attempts)

    async def _search_query(
        self, question: str, earlier: Sequence[Exchange], fallback: str
    ) -> tuple[str, tuple[Attempt, ...]]:
        """La consulta reescrita y su intento, o `fallback` sin intentos si no hay quien
        reescriba, si falla o si no termina en el plazo. Una reescritura cortada se queda con
        su reserva: lo que alcanzó a gastar no se conoce.

        Raises:
            BudgetExhaustedError: si el tope del día no admite ni la reescritura.
        """
        rewriter = self._rewriter
        if rewriter is None:
            return fallback, ()
        bound = worst_case_cost(
            rewriter.name, estimated_rewrite_tokens(question, earlier), rewriter.max_tokens
        )
        reservation = self._budget.reserve(bound)
        started = time.perf_counter()
        try:
            rewrite = await asyncio.wait_for(
                rewriter.rewrite(question, earlier), self._settings.rewrite_timeout_seconds
            )
        except TimeoutError:
            logger.warning("la reescritura pasó su plazo; se busca la pregunta tal cual")
            reservation.settle(None)
            return fallback, ()
        except LlmError as error:
            logger.warning("la reescritura falló (%s); se busca la pregunta tal cual", error.kind)
            reservation.settle(None)
            return fallback, ()
        reservation.settle(attempt_cost(rewrite.attempt))
        logger.info(
            "consulta reescrita en %.0f ms: %r",
            (time.perf_counter() - started) * MILLISECONDS_PER_SECOND,
            rewrite.query,
        )
        return rewrite.query, (rewrite.attempt,)

    async def events(self, prepared: PreparedAnswer) -> AsyncGenerator[bytes, None]:
        """El cuerpo SSE de la respuesta a `prepared`."""
        yield sse_frame(SOURCES_EVENT, _sources_data(prepared, self._model.name))
        if prepared.reservation is None:
            yield sse_frame(DONE_EVENT, _without_sources(prepared))
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
        done = _done_data("".join(parts), completion, prepared)
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
        search_query=prepared.search_query or prepared.question,
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


def _without_sources(prepared: PreparedAnswer) -> DoneData:
    attempts = prepared.rewrite_attempts
    return DoneData(
        model="",
        stop_reason=StopReason.END_TURN,
        complete=True,
        refusal_category=None,
        answer=NO_SOURCES_ANSWER,
        citations=[],
        invalid_citations=[],
        answered_from_documents=False,
        usage=_usage_data(total_usage(attempts)),
        attempts=[_attempt_data(attempt) for attempt in attempts],
        cost_usd=total_cost(attempts),
    )


def _done_data(answer: str, completion: Completion, prepared: PreparedAnswer) -> DoneData:
    report = check_citations(answer, prepared.sources)
    attempts = prepared.rewrite_attempts + completion.attempts
    return DoneData(
        model=completion.model,
        stop_reason=completion.stop_reason,
        complete=completion.stop_reason is StopReason.END_TURN,
        refusal_category=completion.refusal_category,
        answer=answer,
        citations=report.citations,
        invalid_citations=report.invalid,
        answered_from_documents=bool(report.citations),
        conversational=prepared.prompt.conversational,
        usage=_usage_data(total_usage(attempts)),
        attempts=[_attempt_data(attempt) for attempt in attempts],
        cost_usd=total_cost(attempts),
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
