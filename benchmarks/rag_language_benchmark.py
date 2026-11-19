"""La búsqueda híbrida del RAG según el idioma de la pregunta (issue #86, tras la evaluación
de #87): cuánto debe pesar BM25 cuando la pregunta no está en inglés.

    uv run --no-sync python -m benchmarks.rag_language_benchmark \\
        --engine-dir <copia de data/engine-rag> --models data/models/rag \\
        --questions benchmarks/rag/preguntas.jsonl --english benchmarks/rag/preguntas_en.jsonl

Para cada pregunta con respuesta pide una sola vez al gestor las dos listas de la búsqueda
híbrida (`HYBRID_CANDIDATES` fragmentos de BM25 y del HNSW) y después las fusiona fuera de
línea con RRF dando a BM25 cada peso de `--weights` cuando la pregunta no es inglés (las
inglesas y las de idioma desconocido siempre pesan 1). Con peso 1 es la híbrida de antes;
con 0, solo la vectorial. Mide Recall@k para cada `--depths` y MRR@`MRR_DEPTH` con el
fragmento exacto y con sus vecinos (el anterior y el siguiente del mismo paper, que se solapan
con él), por grupo: papers con pregunta en español, documentos del curso (en español) y
papers con pregunta en inglés (`--english`, traducciones de las primeras preguntas). Además,
los tokens estimados del prompt con los primeros k fragmentos de cada modo.

El gestor corre en otro proceso sobre `--engine-dir`, que conviene que sea una copia.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.common import Report, machine_description, results_directory
from benchmarks.engine_process import engine_server
from minigestor_client import AsyncClient
from rag.config import RagSettings, load_embedder
from rag.embeddings import Embedder
from rag.evaluation.metrics import Relevance, first_relevant_rank, summarize
from rag.fusion import RRF_K, reciprocal_rank_fusion
from rag.language import Language, detect_language
from rag.prompt import estimated_tokens
from rag.search import (
    BM25_SQL,
    HYBRID_CANDIDATES,
    VECTOR_SQL,
    lexical_query,
    plain_text_query,
)

REPORT_NAME = "rag_idioma"
UNUSED_ENGINE_KEY = "sin-clave"
# Solo para cargar el modelo con `RagSettings`: el gestor de verdad lo levanta `engine_server`.
UNUSED_ENGINE_URL = "http://gestor.invalid"
DEFAULT_WEIGHTS = (1.0, 0.5, 0.25, 0.1, 0.0)
DEFAULT_DEPTHS = (5, 8, 10, 12)
MRR_DEPTH = 10
COURSE_PREFIX = "curso:"
PAPERS_SPANISH = "papers, pregunta en español"
COURSE_SPANISH = "curso, pregunta en español"
PAPERS_ENGLISH = "papers, pregunta en inglés"
ALL_GROUPS = "todas"
BM25_ONLY = "bm25"
BM25_CONTENT = "bm25 sin palabras vacías del español"
VECTOR_ONLY = "vectorial"
OLD_HYBRID = "híbrida de antes"
NEW_HYBRID = "híbrida"
EXACT = "exacto"
NEIGHBOUR = "vecino"
RELEVANCES = {EXACT: Relevance.EXACT, NEIGHBOUR: Relevance.NEIGHBOUR}


@dataclass(frozen=True, slots=True)
class Question:
    question_id: str
    question: str
    chunk_id: str
    group: str


@dataclass
class Retrieved:
    question: Question
    language: Language
    bm25: list[str]
    bm25_content: list[str]
    vector: list[str]
    texts: dict[str, str] = field(default_factory=dict)


def load_questions(spanish: Path, english: Path | None) -> list[Question]:
    questions = []
    for line in spanish.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("answerable", True):
            course = row["chunk_id"].startswith(COURSE_PREFIX)
            group = COURSE_SPANISH if course else PAPERS_SPANISH
            questions.append(Question(row["question_id"], row["question"], row["chunk_id"], group))
    if english is not None:
        for line in english.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            questions.append(
                Question(row["question_id"], row["question"], row["chunk_id"], PAPERS_ENGLISH)
            )
    return questions


async def retrieve(
    base_url: str, embedder: Embedder, questions: Sequence[Question]
) -> list[Retrieved]:
    retrieved = []
    async with AsyncClient(base_url, UNUSED_ENGINE_KEY) as engine:
        for question in questions:
            language = detect_language(question.question)
            lexical = await engine.fetch_all(
                BM25_SQL, [plain_text_query(question.question), HYBRID_CANDIDATES]
            )
            lexical_content = await engine.fetch_all(
                BM25_SQL, [lexical_query(question.question), HYBRID_CANDIDATES]
            )
            vector = embedder.embed_query(question.question)
            semantic = await engine.fetch_all(VECTOR_SQL, [vector, HYBRID_CANDIDATES])
            item = Retrieved(
                question,
                language,
                [row["chunk_id"] for row in lexical],
                [row["chunk_id"] for row in lexical_content],
                [row["chunk_id"] for row in semantic],
            )
            item.texts = {
                row["chunk_id"]: row["content"] for row in (*lexical, *lexical_content, *semantic)
            }
            retrieved.append(item)
    return retrieved


def ranked(item: Retrieved, mode: str, weight: float) -> list[str]:
    if mode == BM25_ONLY:
        return item.bm25
    if mode == BM25_CONTENT:
        return item.bm25_content
    if mode == VECTOR_ONLY:
        return item.vector
    if mode == OLD_HYBRID:
        return [entry.key for entry in reciprocal_rank_fusion([item.bm25, item.vector], RRF_K)]
    bm25_weight = 1.0 if item.language is not Language.SPANISH else weight
    fused = reciprocal_rank_fusion([item.bm25_content, item.vector], RRF_K, [bm25_weight, 1.0])
    return [entry.key for entry in fused if entry.score > 0]


def scores(
    items: Sequence[Retrieved], mode: str, weight: float, depths: Sequence[int]
) -> dict[str, Any]:
    result: dict[str, Any] = {"preguntas": len(items)}
    for name, relevance in RELEVANCES.items():
        ranks = [
            first_relevant_rank(ranked(item, mode, weight), item.question.chunk_id, relevance)
            for item in items
        ]
        summary = summarize(ranks, depths, MRR_DEPTH)
        for depth, recall in summary.recall.items():
            result[f"recall_{depth}_{name}"] = round(recall, 4)
        result[f"mrr_{name}"] = round(summary.mrr, 4)
    for depth in depths:
        result[f"tokens_{depth}"] = round(
            statistics.median(
                sum(estimated_tokens(item.texts[key]) for key in ranked(item, mode, weight)[:depth])
                for item in items
            )
        )
    return result


def modes(weights: Sequence[float]) -> list[tuple[str, str, float]]:
    return [
        (BM25_ONLY, BM25_ONLY, 0.0),
        (BM25_CONTENT, BM25_CONTENT, 0.0),
        (VECTOR_ONLY, VECTOR_ONLY, 0.0),
        (OLD_HYBRID, OLD_HYBRID, 1.0),
        *(
            (f"híbrida sin palabras vacías, peso BM25 {weight:g} en español", NEW_HYBRID, weight)
            for weight in weights
        ),
    ]


def run(arguments: argparse.Namespace) -> Report:
    questions = load_questions(arguments.questions, arguments.english)
    settings = RagSettings.model_validate(
        {
            "engine_url": UNUSED_ENGINE_URL,
            "engine_key": UNUSED_ENGINE_KEY,
            "models_dir": arguments.models,
        }
    )
    embedder = load_embedder(settings)
    with engine_server(arguments.engine_dir, {}) as base_url:
        retrieved = asyncio.run(retrieve(base_url, embedder, questions))
    report = Report(REPORT_NAME, _parameters(arguments, questions))
    groups = {
        ALL_GROUPS: retrieved,
        **{
            group: [item for item in retrieved if item.question.group == group]
            for group in (PAPERS_SPANISH, COURSE_SPANISH, PAPERS_ENGLISH)
        },
    }
    for label, mode, weight in modes(arguments.weights):
        for group, items in groups.items():
            if items:
                report.add(
                    label, group, len(items), 0.0, **scores(items, mode, weight, arguments.depths)
                )
    return report


def summary(report: Report, depths: Sequence[int]) -> str:
    header = "| Modo | Grupo | n | " + " | ".join(f"R@{depth}" for depth in depths)
    header += " | MRR@10 | R@10 vecino | Tokens@8 |"
    lines = [header, "|" + "---|" * (6 + len(depths))]
    for item in report.measurements:
        extra = item.extra
        recalls = " | ".join(f"{extra[f'recall_{depth}_{EXACT}']:.3f}" for depth in depths)
        lines.append(
            f"| {item.technique} | {item.operation} | {item.dataset_size} | {recalls} "
            f"| {extra[f'mrr_{EXACT}']:.3f} | {extra.get(f'recall_10_{NEIGHBOUR}', 0):.3f} "
            f"| {extra.get('tokens_8', '—')} |"
        )
    return "\n".join(lines)


def _parameters(arguments: argparse.Namespace, questions: Sequence[Question]) -> dict[str, Any]:
    return {
        "preguntas": len(questions),
        "candidatos_por_lista": HYBRID_CANDIDATES,
        "rrf_k": RRF_K,
        "pesos_bm25_no_ingles": list(arguments.weights),
        "profundidades": list(arguments.depths),
        "mrr_hasta": MRR_DEPTH,
        "maquina": machine_description(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine-dir", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--english", type=Path)
    parser.add_argument("--weights", type=float, nargs="+", default=list(DEFAULT_WEIGHTS))
    parser.add_argument("--depths", type=int, nargs="+", default=list(DEFAULT_DEPTHS))
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    report = run(arguments)
    print(summary(report, arguments.depths))
    print(f"resultados en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
