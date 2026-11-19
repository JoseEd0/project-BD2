"""Las respuestas del RAG (issue #86): tokens, costo, latencia y citas por nivel de esfuerzo.

    RAG_ANTHROPIC_API_KEY=… uv run --no-sync python -m benchmarks.rag_answer_benchmark \\
        --engine-dir <copia de data/engine-rag> --models data/models/rag \\
        --efforts low medium --max-tokens 2048 --repetitions 2 --budget-usd 1

Cada pregunta de `benchmarks/rag/questions.py` pasa por el mismo camino que `POST /answers`
(`Answerer`: búsqueda híbrida, selección dentro del presupuesto de tokens, prompt y streaming)
con cada esfuerzo de `--efforts`. Por respuesta se guardan el tiempo de la recuperación, el
que pasa desde que se pide al modelo hasta el primer texto y hasta el final, los tokens que
estimó la selección, el motivo de parada, los tokens de entrada (sin caché, escritos y leídos
de la caché) y de salida, el costo, las citas válidas e inválidas y si citó algún fragmento;
la respuesta entera queda en el JSON para juzgarla a mano. La segunda repetición de una pregunta
muestra la lectura de las instrucciones desde la caché.

El gestor corre en otro proceso sobre `--engine-dir`, que conviene que sea una copia del
sembrado. El gasto del experimento lo acota `--budget-usd` con el mismo tope diario del
servicio. `--simulated` corre todo con el modelo simulado, sin clave ni costo, para probar el
camino antes de gastar.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.common import Report, machine_description, results_directory
from benchmarks.engine_process import engine_server
from benchmarks.rag.questions import QUESTIONS
from benchmarks.rag.streaming import MILLISECONDS_PER_SECOND, TimedAnswer, time_answer
from minigestor_client import AsyncClient
from rag.answering import Answerer, PreparedAnswer
from rag.budget import DailyBudget
from rag.claude import Effort
from rag.config import (
    DEFAULT_ANSWER_MAX_TOKENS,
    DEFAULT_DAILY_BUDGET_USD,
    LlmProvider,
    RagSettings,
    load_embedder,
    open_answer_model,
)
from rag.embeddings import Embedder

REPORT_NAME = "rag_respuestas"
OPERATION = "respuesta"
DEFAULT_EFFORTS: tuple[Effort, ...] = ("low", "medium")
DEFAULT_REPETITIONS = 2
# El gestor del experimento no tiene claves de servicio (ver benchmarks/engine_process.py).
UNUSED_ENGINE_KEY = "sin-clave"
SUMMARY_HEADER = (
    "| Esfuerzo | Respuestas | Costo (USD) | Salida (tokens) | Primer texto (ms) | Total (ms) |"
)
SUMMARY_RULE = "|---|---:|---:|---:|---:|---:|"


@dataclass(frozen=True, slots=True)
class Settings:
    engine_dir: Path
    models_dir: Path
    efforts: tuple[Effort, ...]
    max_tokens: int
    repetitions: int
    budget_usd: float
    simulated: bool


def run(settings: Settings) -> Report:
    report = Report(REPORT_NAME, _parameters(settings))
    with engine_server(settings.engine_dir, {}) as base_url:
        asyncio.run(_measure(report, settings, base_url))
    return report


async def _measure(report: Report, settings: Settings, base_url: str) -> None:
    budget = DailyBudget(settings.budget_usd)
    base = _rag_settings(settings, base_url)
    embedder = load_embedder(base)
    async with AsyncClient(base_url, UNUSED_ENGINE_KEY) as engine:
        for effort in settings.efforts:
            configured = base.model_copy(update={"answer_effort": effort})
            async with open_answer_model(configured) as model:
                if model is None:
                    raise SystemExit("falta RAG_ANTHROPIC_API_KEY (o usa --simulated)")
                answerer = Answerer(model, budget, configured.answering())
                await _answer_all(report, settings, effort, answerer, engine, embedder)


async def _answer_all(
    report: Report,
    settings: Settings,
    effort: Effort,
    answerer: Answerer,
    engine: AsyncClient,
    embedder: Embedder,
) -> None:
    for language, question in QUESTIONS:
        for repetition in range(1, settings.repetitions + 1):
            started = time.perf_counter()
            prepared = await answerer.prepare(question, engine, embedder)
            retrieval_ms = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
            timed = await time_answer(answerer, prepared, time.perf_counter())
            _record(report, effort, (language, question, repetition), prepared, retrieval_ms, timed)


def _record(
    report: Report,
    effort: Effort,
    asked: tuple[str, str, int],
    prepared: PreparedAnswer,
    retrieval_ms: float,
    timed: TimedAnswer,
) -> None:
    language, question, repetition = asked
    done = timed.done or {}
    usage = done.get("usage", {})
    report.add(
        f"esfuerzo {effort}",
        OPERATION,
        len(prepared.sources),
        round(timed.total_ms, 1),
        idioma=language,
        pregunta=question,
        repeticion=repetition,
        prompt_estimado=prepared.prompt.estimated_tokens,
        recuperacion_ms=round(retrieval_ms, 1),
        primer_texto_ms=None if timed.first_text_ms is None else round(timed.first_text_ms, 1),
        modelo=done.get("model"),
        stop_reason=done.get("stop_reason"),
        entrada=usage.get("input_tokens"),
        cache_escrita=usage.get("cache_creation_input_tokens"),
        cache_leida=usage.get("cache_read_input_tokens"),
        salida=usage.get("output_tokens"),
        costo_usd=done.get("cost_usd"),
        citas=len(done.get("citations", [])),
        citas_invalidas=done.get("invalid_citations"),
        respondida_con_documentos=done.get("answered_from_documents"),
        respuesta=done.get("answer"),
        error=timed.error,
    )


def _rag_settings(settings: Settings, base_url: str) -> RagSettings:
    provider = LlmProvider.SIMULATED if settings.simulated else LlmProvider.ANTHROPIC
    return RagSettings.model_validate(
        {
            "engine_url": base_url,
            "engine_key": UNUSED_ENGINE_KEY,
            "models_dir": settings.models_dir,
            "llm_provider": provider,
            "answer_max_tokens": settings.max_tokens,
        }
    )


def summary(report: Report) -> str:
    """Por esfuerzo: mediana del costo, de la salida, del primer texto y del total."""
    lines = [SUMMARY_HEADER, SUMMARY_RULE]
    by_effort: dict[str, list[Any]] = {}
    for measurement in report.measurements:
        by_effort.setdefault(measurement.technique, []).append(measurement)
    for effort, measurements in by_effort.items():
        lines.append(
            f"| {effort} | {len(measurements)} "
            f"| {_median(m.extra.get('costo_usd') for m in measurements)} "
            f"| {_median(m.extra.get('salida') for m in measurements)} "
            f"| {_median(m.extra.get('primer_texto_ms') for m in measurements)} "
            f"| {_median(m.milliseconds for m in measurements)} |"
        )
    return "\n".join(lines)


def _median(values: Any) -> str:
    present = [value for value in values if value is not None]
    return f"{statistics.median(present):.6g}" if present else "—"


def _parameters(settings: Settings) -> dict[str, Any]:
    return {
        "preguntas": len(QUESTIONS),
        "esfuerzos": list(settings.efforts),
        "max_tokens": settings.max_tokens,
        "repeticiones": settings.repetitions,
        "tope_usd": settings.budget_usd,
        "simulado": settings.simulated,
        "maquina": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine-dir", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--efforts", nargs="+", default=list(DEFAULT_EFFORTS))
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_ANSWER_MAX_TOKENS)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_DAILY_BUDGET_USD)
    parser.add_argument("--simulated", action="store_true")
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    settings = Settings(
        engine_dir=arguments.engine_dir,
        models_dir=arguments.models,
        efforts=tuple(arguments.efforts),
        max_tokens=arguments.max_tokens,
        repetitions=arguments.repetitions,
        budget_usd=arguments.budget_usd,
        simulated=arguments.simulated,
    )
    report = run(settings)
    print(summary(report))
    print(f"resultados en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
