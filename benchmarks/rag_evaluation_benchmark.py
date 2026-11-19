"""La evaluación del RAG (issue #87): recuperación de las preguntas del conjunto y, con un
modelo, la calidad, el costo y la latencia de sus respuestas.

    uv run --no-sync python -m benchmarks.rag_evaluation_benchmark \\
        --engine-dir <copia de data/engine-rag> --models data/models/rag

    RAG_ANTHROPIC_API_KEY=… uv run --no-sync python -m benchmarks.rag_evaluation_benchmark \\
        --engine-dir <copia> --models data/models/rag --answers claude --judge --budget-usd 5

**Recuperación** (siempre): cada pregunta con respuesta de `benchmarks/rag/preguntas.jsonl`
se busca en los tres modos de `POST /search` (BM25, vectorial e híbrido, los `k` primeros) y
se anota el puesto del fragmento correcto con tres criterios (el fragmento exacto, también
sus vecinos y cualquiera del mismo paper; ver `rag.evaluation.metrics`). Se guardan
Recall@k y MRR por modo, para todas las preguntas y por grupo (categoría del paper, idioma
del documento y origen de la pregunta), y el detalle por pregunta, en `rag_evaluacion.json`.

**Respuestas** (`--answers simulated | claude`): cada pregunta, también las que el corpus no
responde, pasa por el mismo camino que `POST /answers` (`Answerer`). Por respuesta: el tiempo
de la recuperación, hasta el evento `sources` y hasta el primer texto (los dos desde que se
pregunta) y el total, las citas válidas e inválidas, si citó el fragmento correcto, si dijo
que no estaba en los documentos, los tokens y el costo. Con `--judge`, Claude Haiku 5.5
juzga cada respuesta afirmación por afirmación (`rag.evaluation.judge`). Todo queda en
`rag_evaluacion_respuestas.json`. Con `simulated` no hay clave ni costo: sirve para probar el
camino y medir la latencia hasta el primer evento, **no** la calidad de las respuestas.

Antes de llamar al modelo se imprime la estimación del costo (tokens estimados del prompt
real de cada pregunta por el precio de `rag.pricing`); `--budget-usd` es un tope duro para
respuestas y juez juntos, el mismo tope diario del servicio.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

from benchmarks.common import Report, load_average, machine_description, results_directory
from benchmarks.engine_process import engine_server
from benchmarks.rag.evaluation_names import (
    ALL_GROUP,
    ANSWER_OPERATION,
    ANSWERS_REPORT,
    AUTHOR_GROUP,
    BATCH_GROUP,
    COURSE_GROUP,
    DB_GROUP,
    IR_GROUP,
    MRR_FIELD,
    NEIGHBOUR_SUFFIX,
    PAPER_SUFFIX,
    PER_QUESTION_OPERATION,
    RECALL_FIELD,
    RETRIEVAL_REPORT,
)
from benchmarks.rag.streaming import MILLISECONDS_PER_SECOND, TimedAnswer, time_answer
from minigestor_client import AsyncClient
from rag.answering import Answerer, PreparedAnswer
from rag.budget import BudgetExhaustedError, DailyBudget
from rag.config import (
    DEFAULT_DAILY_BUDGET_USD,
    LlmProvider,
    RagSettings,
    load_embedder,
    open_answer_model,
)
from rag.embeddings import Embedder
from rag.evaluation.dataset import COURSE_CATEGORY, EvalQuestion, Origin, load_questions
from rag.evaluation.judge import (
    JUDGE_MAX_TOKENS,
    JUDGE_MODEL,
    ClaudeJudge,
    Judgement,
    estimated_judge_tokens,
)
from rag.evaluation.metrics import Relevance, first_relevant_rank, summarize
from rag.pricing import PRICES, TOKENS_PER_PRICE_UNIT, worst_case_cost
from rag.prompt import CHARACTERS_PER_TOKEN
from rag.schema import CHUNKS_TABLE
from rag.search import Retriever, SearchMode

HERE = Path(__file__).parent
DEFAULT_QUESTIONS = HERE / "rag" / "preguntas.jsonl"
RECALL_DEPTHS = (5, 10)
MRR_DEPTH = 10
# El gestor del experimento no tiene claves de servicio (ver benchmarks/engine_process.py).
UNUSED_ENGINE_KEY = "sin-clave"
COUNT_SQL = f"SELECT COUNT(*) AS filas FROM {CHUNKS_TABLE}"
WARM_UP_QUESTION = "warm up"
PERCENTILE_95 = 95
PERCENTILE_CUTS = 100
# Cuánto se espera que escriba cada respuesta y cada veredicto, para la estimación del costo
# esperado (la cota usa `max_tokens`). Supuestos hasta medirlos con la clave.
EXPECTED_ANSWER_OUTPUT_TOKENS = 600
EXPECTED_ANSWER_CHARACTERS = int(EXPECTED_ANSWER_OUTPUT_TOKENS * CHARACTERS_PER_TOKEN)
EXPECTED_JUDGE_OUTPUT_TOKENS = 800
SUMMARY_HEADER = "| Modo | Preguntas | Recall@5 | Recall@10 | MRR@10 | Mediana (ms) |"
SUMMARY_RULE = "|---|---:|---:|---:|---:|---:|"
MODES = (SearchMode.BM25, SearchMode.VECTOR, SearchMode.HYBRID)
RELEVANCE_SUFFIXES = {
    Relevance.EXACT: "",
    Relevance.NEIGHBOUR: NEIGHBOUR_SUFFIX,
    Relevance.PAPER: PAPER_SUFFIX,
}
GROUPS: tuple[tuple[str, Callable[[EvalQuestion], bool]], ...] = (
    (ALL_GROUP, lambda question: True),
    (DB_GROUP, lambda question: question.category == DB_GROUP),
    (IR_GROUP, lambda question: question.category == IR_GROUP),
    (COURSE_GROUP, lambda question: question.category == COURSE_CATEGORY),
    (AUTHOR_GROUP, lambda question: question.origin is Origin.AUTHOR),
    (BATCH_GROUP, lambda question: question.origin is Origin.BATCH),
)


@dataclass(frozen=True, slots=True)
class Settings:
    engine_dir: Path
    models_dir: Path
    questions_file: Path
    answers: LlmProvider | None
    judge: bool
    budget_usd: float
    output: Path


@dataclass(frozen=True, slots=True)
class Retrieved:
    question: EvalQuestion
    mode: SearchMode
    ranked: list[str]
    milliseconds: float


def run(settings: Settings) -> list[Report]:
    questions = load_questions(settings.questions_file)
    with engine_server(settings.engine_dir, {}) as base_url:
        return asyncio.run(_evaluate(settings, questions, base_url))


async def _evaluate(
    settings: Settings, questions: list[EvalQuestion], base_url: str
) -> list[Report]:
    rag_settings = _rag_settings(settings, base_url)
    embedder = load_embedder(rag_settings)
    async with AsyncClient(base_url, UNUSED_ENGINE_KEY) as engine:
        counted = await engine.fetch_one(COUNT_SQL)
        chunks = int(counted["filas"]) if counted is not None else 0
        retrieval = await _retrieval_report(settings, questions, engine, embedder, chunks)
        reports = [retrieval]
        if settings.answers is not None:
            reports.append(
                await _answers_report(settings, rag_settings, questions, engine, embedder, chunks)
            )
    return reports


async def _retrieval_report(
    settings: Settings,
    questions: Sequence[EvalQuestion],
    engine: AsyncClient,
    embedder: Embedder,
    chunks: int,
) -> Report:
    report = Report(RETRIEVAL_REPORT, _retrieval_parameters(settings, questions))
    retriever = Retriever(engine, embedder)
    for mode in MODES:
        await retriever.search(WARM_UP_QUESTION, mode, max(RECALL_DEPTHS))
    answerable = [question for question in questions if question.answerable]
    retrieved = [
        await _retrieve(retriever, question, mode) for question in answerable for mode in MODES
    ]
    for item in retrieved:
        _record_question(report, item, chunks)
    for mode in MODES:
        for group, belongs in GROUPS:
            members = [item for item in retrieved if item.mode is mode and belongs(item.question)]
            if members:
                _record_group(report, mode, group, members, chunks)
    report.parameters["carga_media_al_terminar"] = load_average()
    return report


async def _retrieve(retriever: Retriever, question: EvalQuestion, mode: SearchMode) -> Retrieved:
    started = time.perf_counter()
    hits = await retriever.search(question.question, mode, max(RECALL_DEPTHS))
    elapsed = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    return Retrieved(question, mode, [hit.chunk_id for hit in hits], elapsed)


def _gold(item: Retrieved) -> str:
    gold = item.question.chunk_id
    if gold is None:
        raise ValueError(f"{item.question.question_id} no tiene fragmento correcto")
    return gold


def _record_question(report: Report, item: Retrieved, chunks: int) -> None:
    gold = _gold(item)
    ranks = {
        f"puesto{suffix}": first_relevant_rank(item.ranked, gold, relevance)
        for relevance, suffix in RELEVANCE_SUFFIXES.items()
    }
    report.add(
        item.mode.value,
        PER_QUESTION_OPERATION,
        chunks,
        round(item.milliseconds, 2),
        pregunta_id=item.question.question_id,
        origen=item.question.origin.value,
        categoria=item.question.category,
        idioma_documento=item.question.source_language,
        correcto=gold,
        recuperados=item.ranked,
        **ranks,
    )


def _record_group(
    report: Report, mode: SearchMode, group: str, members: Sequence[Retrieved], chunks: int
) -> None:
    scores: dict[str, Any] = {}
    for relevance, suffix in RELEVANCE_SUFFIXES.items():
        ranks = [first_relevant_rank(item.ranked, _gold(item), relevance) for item in members]
        summary = summarize(ranks, RECALL_DEPTHS, MRR_DEPTH)
        for k, value in summary.recall.items():
            scores[RECALL_FIELD.format(k=k) + suffix] = round(value, 4)
        scores[MRR_FIELD + suffix] = round(summary.mrr, 4)
    latencies = [item.milliseconds for item in members]
    report.add(
        mode.value,
        group,
        chunks,
        round(statistics.median(latencies), 2),
        preguntas=len(members),
        p95_ms=round(_percentile_95(latencies), 2),
        **scores,
    )


async def _answers_report(
    settings: Settings,
    rag_settings: RagSettings,
    questions: Sequence[EvalQuestion],
    engine: AsyncClient,
    embedder: Embedder,
    chunks: int,
) -> Report:
    report = Report(ANSWERS_REPORT, _answer_parameters(settings, questions))
    budget = DailyBudget(settings.budget_usd)
    async with open_answer_model(rag_settings) as model:
        if model is None:
            raise SystemExit("falta RAG_ANTHROPIC_API_KEY (o usa --answers simulated)")
        dry_run = Answerer(model, DailyBudget(math.inf), rag_settings.answering())
        prompts = [await dry_run.prepare(q.question, engine, embedder) for q in questions]
        estimate = estimate_cost(rag_settings, prompts, settings.judge)
        report.parameters["estimacion_usd"] = estimate
        print(estimate_text(estimate, settings.budget_usd))
        answerer = Answerer(model, budget, rag_settings.answering())
        async with _judge(settings, rag_settings, budget) as judge:
            for question in questions:
                try:
                    await _answer(report, (answerer, judge), question, (engine, embedder), chunks)
                except BudgetExhaustedError:
                    report.parameters["cortada_por_el_tope_en"] = question.question_id
                    break
    report.parameters["gasto_usd"] = round(budget.spent, 6)
    report.parameters["carga_media_al_terminar"] = load_average()
    return report


@asynccontextmanager
async def _judge(
    settings: Settings, rag_settings: RagSettings, budget: DailyBudget
) -> AsyncIterator[ClaudeJudge | None]:
    if not settings.judge:
        yield None
        return
    key = rag_settings.anthropic_api_key
    if key is None:
        raise SystemExit("--judge necesita RAG_ANTHROPIC_API_KEY")
    async with anthropic.AsyncAnthropic(
        api_key=key.get_secret_value(),
        timeout=rag_settings.anthropic_timeout_seconds,
        max_retries=rag_settings.anthropic_max_retries,
    ) as client:
        yield ClaudeJudge(client, budget)


async def _answer(
    report: Report,
    models: tuple[Answerer, ClaudeJudge | None],
    question: EvalQuestion,
    search: tuple[AsyncClient, Embedder],
    chunks: int,
) -> None:
    answerer, judge = models
    started = time.perf_counter()
    prepared = await answerer.prepare(question.question, *search)
    retrieval_ms = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    timed = await time_answer(answerer, prepared, started)
    answer = (timed.done or {}).get("answer")
    judgement = None
    if judge is not None and answer:
        judgement = await judge.judge(question.question, prepared.sources, answer)
    report.add(
        prepared_model(timed),
        ANSWER_OPERATION,
        chunks,
        round(timed.total_ms, 1),
        **answer_fields(question, prepared, retrieval_ms, timed),
        juez=None if judgement is None else judgement_fields(judgement),
    )


def prepared_model(timed: TimedAnswer) -> str:
    return str((timed.done or {}).get("model") or "sin modelo")


def answer_fields(
    question: EvalQuestion, prepared: PreparedAnswer, retrieval_ms: float, timed: TimedAnswer
) -> dict[str, Any]:
    """Lo que se guarda de una respuesta: tiempos, citas, «no está», tokens y costo."""
    done = timed.done or {}
    usage = done.get("usage", {})
    sent = [source.hit.chunk_id for source in prepared.sources]
    cited = [citation["chunk_id"] for citation in done.get("citations", [])]
    return {
        "pregunta_id": question.question_id,
        "origen": question.origin.value,
        "con_respuesta": question.answerable,
        "categoria": question.category,
        "recuperacion_ms": round(retrieval_ms, 1),
        "fuentes_ms": _rounded(timed.sources_ms),
        "primer_texto_ms": _rounded(timed.first_text_ms),
        "prompt_estimado": prepared.prompt.estimated_tokens,
        "fragmentos_enviados": sent,
        "correcto_enviado": question.chunk_id in sent if question.answerable else None,
        "correcto_citado": question.chunk_id in cited if question.answerable else None,
        "stop_reason": done.get("stop_reason"),
        "completa": done.get("complete"),
        "citas": cited,
        "citas_invalidas": done.get("invalid_citations"),
        "respondida_con_documentos": done.get("answered_from_documents"),
        "entrada": usage.get("input_tokens"),
        "cache_escrita": usage.get("cache_creation_input_tokens"),
        "cache_leida": usage.get("cache_read_input_tokens"),
        "salida": usage.get("output_tokens"),
        "costo_usd": done.get("cost_usd"),
        "respuesta": done.get("answer"),
        "error": timed.error,
    }


def judgement_fields(judgement: Judgement) -> dict[str, Any]:
    verdict = judgement.verdict
    return {
        "fiel": None if verdict is None else verdict.faithful,
        "afirmaciones": None if verdict is None else len(verdict.claims),
        "apoyadas": None if verdict is None else verdict.supported_share,
        "dice_que_no_esta": None if verdict is None else verdict.says_not_in_documents,
        "detalle": None if verdict is None else verdict.model_dump()["claims"],
        "entrada": judgement.usage.input_tokens,
        "salida": judgement.usage.output_tokens,
        "costo_usd": judgement.cost_usd,
        "error": judgement.error,
    }


def estimate_cost(
    rag_settings: RagSettings, prepared: Sequence[PreparedAnswer], judge: bool
) -> dict[str, Any]:
    """El costo de responder (y juzgar) `prepared` con el modelo de `rag_settings`, sin
    importar el proveedor con que se corra: la cota (toda la entrada al precio más caro y la
    salida entera) y lo esperado (entrada a precio normal y `EXPECTED_*_OUTPUT_TOKENS`)."""
    model = rag_settings.answer_model
    price, judge_price = PRICES[model], PRICES[JUDGE_MODEL]
    asked = [item for item in prepared if item.sources]
    prompt_tokens = sum(item.prompt.estimated_tokens for item in asked)
    answers_bound = sum(
        worst_case_cost(model, item.prompt.estimated_tokens, rag_settings.answer_max_tokens)
        for item in asked
    )
    answers_expected = _dollars(
        prompt_tokens * price.input + len(asked) * EXPECTED_ANSWER_OUTPUT_TOKENS * price.output
    )
    judge_tokens = [
        estimated_judge_tokens(item.question, item.sources, " " * EXPECTED_ANSWER_CHARACTERS)
        for item in asked
    ]
    judge_bound = sum(worst_case_cost(JUDGE_MODEL, t, JUDGE_MAX_TOKENS) for t in judge_tokens)
    judge_expected = _dollars(
        sum(judge_tokens) * judge_price.input
        + len(asked) * EXPECTED_JUDGE_OUTPUT_TOKENS * judge_price.output
    )
    return {
        "modelo": model,
        "preguntas_con_fragmentos": len(asked),
        "tokens_de_prompt_estimados": prompt_tokens,
        "respuestas_cota": round(answers_bound, 4),
        "respuestas_esperado": round(answers_expected, 4),
        "juez": JUDGE_MODEL,
        "juez_tokens_estimados": sum(judge_tokens),
        "juez_cota": round(judge_bound if judge else 0.0, 4),
        "juez_esperado": round(judge_expected if judge else 0.0, 4),
        "juez_esperado_si_se_pide": round(judge_expected, 4),
    }


def estimate_text(estimate: dict[str, Any], budget_usd: float) -> str:
    return (
        f"costo estimado con {estimate['modelo']}: respuestas {estimate['respuestas_esperado']} "
        f"USD (cota {estimate['respuestas_cota']}), juez {estimate['juez_esperado']} USD "
        f"(cota {estimate['juez_cota']}); tope {budget_usd} USD"
    )


def summary(reports: Sequence[Report]) -> str:
    """Recall@k y MRR por modo para todas las preguntas, y lo esencial de las respuestas."""
    lines = [
        "| Modo | Preguntas | Recall@5 | Recall@10 | MRR@10 | Mediana (ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in reports[0].measurements:
        if item.operation == ALL_GROUP:
            extra = item.extra
            lines.append(
                f"| {item.technique} | {extra['preguntas']} | {extra['recall_5']:.3f} "
                f"| {extra['recall_10']:.3f} | {extra['mrr']:.3f} | {item.milliseconds:.1f} |"
            )
    for report in reports[1:]:
        lines.append(
            f"respuestas: {len(report.measurements)}, gasto {report.parameters['gasto_usd']} USD"
        )
    return "\n".join(lines)


def _dollars(price_times_tokens: float) -> float:
    return price_times_tokens / TOKENS_PER_PRICE_UNIT


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _percentile_95(values: Sequence[float]) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=PERCENTILE_CUTS, method="inclusive")[PERCENTILE_95 - 1]


def _rag_settings(settings: Settings, base_url: str) -> RagSettings:
    provider = settings.answers or LlmProvider.SIMULATED
    return RagSettings.model_validate(
        {
            "engine_url": base_url,
            "engine_key": UNUSED_ENGINE_KEY,
            "models_dir": settings.models_dir,
            "llm_provider": provider,
        }
    )


def _retrieval_parameters(settings: Settings, questions: Sequence[EvalQuestion]) -> dict[str, Any]:
    return {
        "preguntas": settings.questions_file.name,
        "con_respuesta": sum(question.answerable for question in questions),
        "sin_respuesta": sum(not question.answerable for question in questions),
        "k": list(RECALL_DEPTHS),
        "mrr_hasta": MRR_DEPTH,
        "modos": [mode.value for mode in MODES],
        "maquina": machine_description(),
    }


def _answer_parameters(settings: Settings, questions: Sequence[EvalQuestion]) -> dict[str, Any]:
    return {
        "preguntas": len(questions),
        "proveedor": None if settings.answers is None else settings.answers.value,
        "simulado": settings.answers is LlmProvider.SIMULATED,
        "juez": JUDGE_MODEL if settings.judge else None,
        "tope_usd": settings.budget_usd,
        "maquina": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine-dir", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--answers", type=LlmProvider, choices=list(LlmProvider))
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_DAILY_BUDGET_USD)
    parser.add_argument("--output", type=Path, default=results_directory(HERE))
    arguments = parser.parse_args()
    if arguments.judge and arguments.answers is not LlmProvider.ANTHROPIC:
        parser.error("--judge juzga respuestas de Claude: pide también --answers anthropic")
    settings = Settings(
        engine_dir=arguments.engine_dir,
        models_dir=arguments.models,
        questions_file=arguments.questions,
        answers=arguments.answers,
        judge=arguments.judge,
        budget_usd=arguments.budget_usd,
        output=arguments.output,
    )
    reports = run(settings)
    print(summary(reports))
    for report in reports:
        print(f"resultados en {report.save(settings.output)}")


if __name__ == "__main__":
    main()
