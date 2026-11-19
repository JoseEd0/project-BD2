"""Cronometrar una respuesta del RAG evento por evento, como la ve quien pregunta: los
experimentos de las respuestas (`rag_answer_benchmark`) y de la evaluación
(`rag_evaluation_benchmark`) leen el mismo SSE que `POST /answers`."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from rag.answering import (
    DELTA_EVENT,
    DONE_EVENT,
    ERROR_EVENT,
    SOURCES_EVENT,
    Answerer,
    PreparedAnswer,
)

MILLISECONDS_PER_SECOND = 1000.0
DATA_PREFIX = "data: "
EVENT_PREFIX = "event: "


@dataclass(frozen=True, slots=True)
class TimedAnswer:
    """Los tiempos desde `started` (ms) hasta el evento `sources`, el primer texto y el
    final, y los datos del evento `done` o del `error`."""

    sources_ms: float | None
    first_text_ms: float | None
    total_ms: float
    done: dict[str, Any] | None
    error: dict[str, Any] | None


async def time_answer(answerer: Answerer, prepared: PreparedAnswer, started: float) -> TimedAnswer:
    """Consume el SSE de `prepared` y mide cada hito desde `started` (`time.perf_counter`)."""
    sources: float | None = None
    first_text: float | None = None
    done: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    async for frame in answerer.events(prepared):
        name, data = parsed_frame(frame)
        if name == SOURCES_EVENT and sources is None:
            sources = time.perf_counter()
        elif name == DELTA_EVENT and first_text is None:
            first_text = time.perf_counter()
        elif name == DONE_EVENT:
            done = data
        elif name == ERROR_EVENT:
            error = data
    total = time.perf_counter()
    return TimedAnswer(
        _since(started, sources), _since(started, first_text), _elapsed(started, total), done, error
    )


def parsed_frame(frame: bytes) -> tuple[str, dict[str, Any]]:
    """El nombre y los datos de un evento SSE de una línea `event:` y una `data:`."""
    name_line, data_line = frame.decode().strip().split("\n")
    return name_line.removeprefix(EVENT_PREFIX), json.loads(data_line.removeprefix(DATA_PREFIX))


def _since(started: float, moment: float | None) -> float | None:
    return None if moment is None else _elapsed(started, moment)


def _elapsed(started: float, moment: float) -> float:
    return (moment - started) * MILLISECONDS_PER_SECOND
