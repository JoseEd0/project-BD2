"""El conjunto de evaluación: su esquema, que escribirlo dé siempre los mismos bytes, el
filtro de prosa, la muestra estratificada con semilla y el archivo versionado."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from rag.evaluation.dataset import (
    EvalQuestion,
    Origin,
    is_prose,
    load_questions,
    sample_chunks,
    stratum_key,
    write_questions,
)

VERSIONED = Path(__file__).parents[3] / "benchmarks" / "rag" / "preguntas.jsonl"
PROSE = (
    "The index keeps every leaf on the same level and splits a node when it overflows, so a "
    "range query walks the linked leaves from the first key to the last one without going "
    "back to the root of the tree."
)
REFERENCES = " ".join(f"[{n}] A. Author. 20{n:02d}. A title. In Proc. VLDB." for n in range(1, 8))


def answerable(question_id: str = "a01", chunk_id: str = "2501.00001#3") -> EvalQuestion:
    return EvalQuestion(
        question_id=question_id,
        origin=Origin.AUTHOR,
        question="¿Cómo responde un B+ a una consulta por rango?",
        answerable=True,
        chunk_id=chunk_id,
        paper_id=chunk_id.split("#")[0],
        category="cs.DB",
        year=2026,
    )


def unanswerable(question_id: str = "u01") -> EvalQuestion:
    return EvalQuestion(
        question_id=question_id,
        origin=Origin.AUTHOR,
        question="¿Cuál es la capital de Mongolia?",
        answerable=False,
    )


def test_an_answerable_question_needs_its_chunk_and_paper():
    with pytest.raises(ValidationError):
        EvalQuestion(question_id="a", origin=Origin.AUTHOR, question="¿?", answerable=True)


def test_the_chunk_has_to_belong_to_the_paper():
    with pytest.raises(ValidationError):
        EvalQuestion.model_validate({**answerable().model_dump(), "paper_id": "2509.99999"})


def test_an_unanswerable_question_has_no_chunk():
    with pytest.raises(ValidationError):
        EvalQuestion.model_validate({**unanswerable().model_dump(), "chunk_id": "2501.00001#1"})


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        EvalQuestion.model_validate({**unanswerable().model_dump(), "text": "corpus"})


def test_the_source_language_follows_the_category():
    course = EvalQuestion.model_validate(
        {**answerable(chunk_id="curso:informe#2").model_dump(), "category": "curso"}
    )
    assert course.source_language == "es"
    assert answerable().source_language == "en"
    assert unanswerable().source_language is None


def test_writing_sorts_by_id_and_gives_the_same_bytes(tmp_path: Path):
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    write_questions(first, [unanswerable(), answerable()])
    write_questions(second, [answerable(), unanswerable()])
    assert first.read_bytes() == second.read_bytes()
    assert [q.question_id for q in load_questions(first)] == ["a01", "u01"]


def test_a_repeated_id_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        write_questions(tmp_path / "q.jsonl", [answerable(), answerable()])


def test_prose_passes_and_reference_lists_and_tables_do_not():
    assert is_prose(PROSE)
    assert not is_prose(REFERENCES)
    assert not is_prose("References " + PROSE)
    assert not is_prose(" ".join(["0.91 0.88 1.2 |"] * 40))
    assert not is_prose("")


def corpus(
    papers_per_stratum: int, chunks_per_paper: int
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    papers: list[dict[str, object]] = []
    chunks: list[dict[str, object]] = []
    for category, year in (("cs.DB", 2026), ("cs.IR", 2026), ("curso", None)):
        for number in range(papers_per_stratum):
            paper_id = f"{category}-{number}"
            papers.append(
                {
                    "paper_id": paper_id,
                    "title": paper_id,
                    "primary_category": category,
                    "year": year,
                }
            )
            for ordinal in range(chunks_per_paper):
                text = PROSE if ordinal != 1 else REFERENCES
                chunks.append(
                    {"paper_id": paper_id, "chunk_id": f"{paper_id}#{ordinal}", "text": text}
                )
    return papers, chunks


def test_the_sample_is_deterministic_and_fills_each_quota():
    papers, chunks = corpus(papers_per_stratum=10, chunks_per_paper=4)
    quotas = {stratum_key("cs.DB", 2026): 3, stratum_key("cs.IR", 2026): 2}
    first = sample_chunks(papers, chunks, quotas, seed=7)
    assert first == sample_chunks(papers, chunks, quotas, seed=7)
    assert [c.stratum for c in first] == ["cs.DB:2026"] * 3 + ["cs.IR:2026"] * 2
    assert len({c.paper_id for c in first}) == 5
    assert all(not c.chunk_id.endswith("#1") for c in first)
    assert first != sample_chunks(papers, chunks, quotas, seed=8)


def test_the_sample_goes_round_when_a_stratum_has_few_papers():
    papers, chunks = corpus(papers_per_stratum=2, chunks_per_paper=4)
    sampled = sample_chunks(papers, chunks, {"curso": 5}, seed=1)
    assert len(sampled) == 5
    assert len({c.chunk_id for c in sampled}) == 5
    assert {c.category for c in sampled} == {"curso"}


def test_the_sample_skips_excluded_chunks_and_stops_when_it_runs_out():
    papers, chunks = corpus(papers_per_stratum=1, chunks_per_paper=3)
    sampled = sample_chunks(
        papers, chunks, {"cs.DB:2026": 5}, seed=1, excluded=frozenset({"cs.DB-0#0"})
    )
    assert [c.chunk_id for c in sampled] == ["cs.DB-0#2"]


def test_the_versioned_set_has_the_promised_questions():
    questions = load_questions(VERSIONED)
    authored = [q for q in questions if q.origin is Origin.AUTHOR and q.answerable]
    without_answer = [q for q in questions if not q.answerable]
    assert len(authored) >= 40
    assert len(without_answer) >= 15
    assert len({q.question for q in questions}) == len(questions)
    assert len({q.chunk_id for q in questions if q.answerable}) == len(questions) - len(
        without_answer
    )
    assert all(q.question.startswith("¿") and q.question.endswith("?") for q in questions)
