"""El conjunto de preguntas de la evaluación del RAG (#87): la muestra de fragmentos de la
que salen y las preguntas que escribe Claude con la Batch API.

    # los fragmentos de las preguntas escritas a mano (los de benchmarks/rag/preguntas.jsonl)
    uv run --no-sync python -m benchmarks.rag_questions sample --data-dir data/datasets \\
        --output /tmp/fragmentos.txt

    # con la clave: pedir preguntas por la Batch API, esperarlas, filtrarlas y revisarlas
    RAG_ANTHROPIC_API_KEY=… uv run --no-sync python -m benchmarks.rag_questions submit \\
        --data-dir data/datasets --state data/rag-preguntas/lote.json --budget-usd 1
    RAG_ANTHROPIC_API_KEY=… uv run --no-sync python -m benchmarks.rag_questions collect \\
        --data-dir data/datasets --state data/rag-preguntas/lote.json \\
        --candidates data/rag-preguntas/candidatas.jsonl
    # revisar a mano candidatas.jsonl (review: ok | drop) y sumar las ok al conjunto
    uv run --no-sync python -m benchmarks.rag_questions accept --data-dir data/datasets \\
        --candidates data/rag-preguntas/candidatas.jsonl

`submit` imprime la estimación del costo y no envía nada si la cota pasa de `--budget-usd`
(`--dry-run` solo estima). La muestra excluye los fragmentos que ya tienen pregunta. El estado
del lote (su identificador y qué fragmento es cada petición) y las candidatas quedan fuera del
repositorio: las candidatas citan texto del corpus. Lo que se versiona es el conjunto, que
solo guarda la pregunta y los identificadores, y `benchmarks/results/rag_preguntas_lote.json`
con los conteos del filtro y el costo real del lote.
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import anthropic

from benchmarks.common import results_directory
from rag.evaluation.dataset import (
    EvalQuestion,
    Origin,
    SampledChunk,
    load_questions,
    paper_of_chunk,
    sample_chunks,
    write_questions,
)
from rag.evaluation.generation import (
    DEFAULT_POLL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    GENERATION_MODEL,
    Candidate,
    QuestionBatches,
    Review,
    build_requests,
    estimate_cost,
    filter_candidates,
    parse_result,
)
from rag.files import read_json, read_jsonl, write_json, write_jsonl
from rag.pipeline import CorpusPaths
from rag.settings import CORPUS_DIRECTORY

HERE = Path(__file__).parent
DEFAULT_QUESTIONS = HERE / "rag" / "preguntas.jsonl"
API_KEY_VARIABLE = "RAG_ANTHROPIC_API_KEY"
BATCH_REPORT = "rag_preguntas_lote.json"
BATCH_ID_PREFIX = "b"
BATCH_ID_DIGITS = 3
# La muestra de las preguntas escritas a mano: 45 fragmentos repartidos como los papers del
# corpus (cs.DB de 2025 son pocos) y cinco del material del curso, el único en español.
AUTHOR_SEED = 87
AUTHOR_QUOTAS = {"cs.DB:2025": 8, "cs.DB:2026": 16, "cs.IR:2026": 16, "curso": 5}
# La del lote: 80 fragmentos más, con otra semilla; después del filtro y la revisión quedan
# unas 70 preguntas, que con las 45 a mano y las 18 sin respuesta dan unas 130.
GENERATION_SEED = 2
GENERATION_QUOTAS = {"cs.DB:2025": 10, "cs.DB:2026": 32, "cs.IR:2026": 32, "curso": 6}


def corpus_rows(data_dir: Path) -> tuple[list[dict[str, Any]], Path]:
    paths = CorpusPaths(data_dir / CORPUS_DIRECTORY)
    return list(read_jsonl(paths.papers_file)), paths.chunks_file


def sample(
    data_dir: Path, quotas: Mapping[str, int], seed: int, excluded: frozenset[str]
) -> list[SampledChunk]:
    papers, chunks_file = corpus_rows(data_dir)
    return sample_chunks(papers, read_jsonl(chunks_file), quotas, seed, excluded)


def write_sample(chunks: Sequence[SampledChunk], output: Path) -> None:
    blocks = [f"=== {c.chunk_id} | {c.stratum} | {c.title}\n{c.text}\n" for c in chunks]
    output.write_text("\n".join(blocks), encoding="utf-8")


def submit(arguments: argparse.Namespace) -> None:
    known = load_questions(arguments.questions)
    excluded = frozenset(q.chunk_id for q in known if q.chunk_id is not None)
    chunks = sample(arguments.data_dir, GENERATION_QUOTAS, arguments.seed, excluded)
    estimate = estimate_cost(chunks)
    print(
        f"{estimate.requests} peticiones a {GENERATION_MODEL} por la Batch API, "
        f"{estimate.input_tokens} tokens de entrada estimados: {estimate.expected_usd:.4f} USD "
        f"esperados, cota {estimate.bound_usd:.4f} USD (tope {arguments.budget_usd} USD)"
    )
    if estimate.bound_usd > arguments.budget_usd:
        raise SystemExit("la cota pasa del tope: no se envía nada")
    if arguments.dry_run:
        return
    requests, chunk_ids = build_requests(chunks)
    batch_id = QuestionBatches(_client()).submit(requests)
    write_json(
        arguments.state,
        {
            "batch_id": batch_id,
            "model": GENERATION_MODEL,
            "seed": arguments.seed,
            "quotas": GENERATION_QUOTAS,
            "chunk_ids": chunk_ids,
            "estimate_usd": {"expected": estimate.expected_usd, "bound": estimate.bound_usd},
        },
    )
    print(f"lote {batch_id}; estado en {arguments.state}")


def collect(arguments: argparse.Namespace) -> None:
    state = read_json(arguments.state)
    if not isinstance(state, dict):
        raise SystemExit(f"{arguments.state} no es el estado de un lote")
    batches = QuestionBatches(_client())
    batch = batches.wait(state["batch_id"], arguments.poll_seconds, arguments.timeout_seconds)
    chunk_ids: dict[str, str] = state["chunk_ids"]
    parsed = [parse_result(result, chunk_ids) for result in batches.results(batch.id)]
    texts = _chunk_texts(arguments.data_dir, set(chunk_ids.values()))
    known = [question.question for question in load_questions(arguments.questions)]
    accepted, rejected = filter_candidates(parsed, texts, known)
    write_jsonl(arguments.candidates, (candidate.model_dump(mode="json") for candidate in accepted))
    cost = sum(item.cost_usd or 0.0 for item in parsed)
    report = {
        "batch_id": batch.id,
        "model": state["model"],
        "requests": len(chunk_ids),
        "results": len(parsed),
        "accepted_by_filter": len(accepted),
        "rejections": dict(Counter(item.reason for item in rejected)),
        "cost_usd": round(cost, 6),
        "estimate_usd": state["estimate_usd"],
    }
    write_json(arguments.report, report)
    print(f"{len(accepted)} candidatas en {arguments.candidates}; costo real {cost:.4f} USD")


def accept(arguments: argparse.Namespace) -> None:
    candidates = [Candidate.model_validate(row) for row in read_jsonl(arguments.candidates)]
    pending = [c for c in candidates if c.review is Review.PENDING]
    if pending:
        raise SystemExit(f"faltan revisar {len(pending)} candidatas (review: ok o drop)")
    papers, _ = corpus_rows(arguments.data_dir)
    known = load_questions(arguments.questions)
    added = batch_questions([c for c in candidates if c.review is Review.OK], papers, known)
    write_questions(arguments.questions, [*known, *added])
    print(f"{len(added)} preguntas con origin batch en {arguments.questions}")


def batch_questions(
    approved: Sequence[Candidate],
    papers: Sequence[Mapping[str, Any]],
    known: Sequence[EvalQuestion],
) -> list[EvalQuestion]:
    """Las candidatas aprobadas como preguntas `batch`, numeradas después de las que ya hay."""
    by_id = {row["paper_id"]: row for row in papers}
    taken = {q.chunk_id for q in known}
    first = 1 + sum(question.origin is Origin.BATCH for question in known)
    questions: list[EvalQuestion] = []
    for candidate in (c for c in approved if c.chunk_id not in taken):
        paper = by_id[paper_of_chunk(candidate.chunk_id)]
        questions.append(
            EvalQuestion(
                question_id=f"{BATCH_ID_PREFIX}{first + len(questions):0{BATCH_ID_DIGITS}d}",
                origin=Origin.BATCH,
                question=candidate.question,
                answerable=True,
                chunk_id=candidate.chunk_id,
                paper_id=paper["paper_id"],
                category=paper["primary_category"],
                year=paper["year"],
            )
        )
    return questions


def _chunk_texts(data_dir: Path, wanted: set[str]) -> dict[str, str]:
    _, chunks_file = corpus_rows(data_dir)
    return {
        str(row["chunk_id"]): str(row["text"])
        for row in read_jsonl(chunks_file)
        if row["chunk_id"] in wanted
    }


def _client() -> anthropic.Anthropic:
    key = os.environ.get(API_KEY_VARIABLE, "").strip()
    if not key:
        raise SystemExit(f"falta {API_KEY_VARIABLE}")
    return anthropic.Anthropic(api_key=key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    sampled = commands.add_parser("sample", help="la muestra de las preguntas a mano")
    sampled.add_argument("--data-dir", type=Path, required=True)
    sampled.add_argument("--seed", type=int, default=AUTHOR_SEED)
    sampled.add_argument("--output", type=Path, required=True)

    submitted = commands.add_parser("submit", help="pedir preguntas por la Batch API")
    submitted.add_argument("--data-dir", type=Path, required=True)
    submitted.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    submitted.add_argument("--seed", type=int, default=GENERATION_SEED)
    submitted.add_argument("--state", type=Path, required=True)
    submitted.add_argument("--budget-usd", type=float, required=True)
    submitted.add_argument("--dry-run", action="store_true")

    collected = commands.add_parser("collect", help="esperar el lote y filtrar lo que vuelve")
    collected.add_argument("--data-dir", type=Path, required=True)
    collected.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    collected.add_argument("--state", type=Path, required=True)
    collected.add_argument("--candidates", type=Path, required=True)
    collected.add_argument("--report", type=Path, default=results_directory(HERE) / BATCH_REPORT)
    collected.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    collected.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)

    accepted = commands.add_parser("accept", help="sumar al conjunto las candidatas revisadas")
    accepted.add_argument("--data-dir", type=Path, required=True)
    accepted.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    accepted.add_argument("--candidates", type=Path, required=True)

    arguments = parser.parse_args()
    if arguments.command == "sample":
        write_sample(
            sample(arguments.data_dir, AUTHOR_QUOTAS, arguments.seed, frozenset()), arguments.output
        )
        print(f"muestra en {arguments.output}")
    elif arguments.command == "submit":
        submit(arguments)
    elif arguments.command == "collect":
        collect(arguments)
    else:
        accept(arguments)


if __name__ == "__main__":
    main()
