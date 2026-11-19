"""El conjunto de evaluación: preguntas en español y el fragmento del corpus que responde
cada una.

Cada pregunta dice de dónde salió (`origin`): `author`, escrita a mano por el autor del
proyecto después de leer el fragmento; `batch`, escrita por Claude con la Batch API a partir
del fragmento, filtrada y revisada a mano (`rag.evaluation.generation`). Una pregunta que el
corpus no responde (`answerable` falso) no tiene fragmento: mide si el servicio dice que no
está en los documentos.

El archivo es JSONL ordenado por `question_id` y no guarda texto del corpus, solo los
identificadores del fragmento y del paper.

Los fragmentos de los que salen las preguntas se eligen con `sample_chunks`: por estrato
(categoría principal y año del paper, más el material del curso), con una semilla, un
fragmento por paper mientras alcancen los papers y solo fragmentos de prosa (sin listas de
referencias ni tablas), que son los que admiten una pregunta.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rag.files import read_jsonl, write_jsonl

QUESTION_MAX_CHARACTERS = 1000
CHUNK_ORDINAL_SEPARATOR = "#"
COURSE_CATEGORY = "curso"
STRATUM_SEPARATOR = ":"
# Los papers de arXiv del corpus están en inglés; el material del curso, en español.
PAPER_LANGUAGE = "en"
COURSE_LANGUAGE = "es"
# Papers que se leen por cada pregunta pedida a un estrato: los que no tienen un fragmento
# de prosa libre se reemplazan por los siguientes.
PAPERS_READ_PER_QUOTA = 3
# Un fragmento de prosa: al menos el 70 % de sus palabras son de letras, a lo sumo 8 citas
# entre corchetes, a lo sumo 4 años seguidos de punto (`2021. Título`, la forma de una
# entrada de bibliografía) y ningún encabezado de bibliografía. Lo demás (tablas, listas de
# referencias, fórmulas) no admite una pregunta que lo tenga como respuesta.
MIN_ALPHABETIC_SHARE = 0.7
MAX_BRACKET_CITATIONS = 8
MAX_REFERENCE_YEARS = 4
ALPHABETIC_WORD = re.compile(r"^[^\W\d_]{2,}[.,;:)]?$")
BRACKET_CITATION = re.compile(r"\[\d+(?:\s*[,\u2013-]\s*\d+)*\]")
REFERENCE_YEAR = re.compile(r"\b(?:19|20)\d{2}[a-z]?\.\s")
BIBLIOGRAPHY_HEADING = re.compile(r"\b(?:References|REFERENCES|Bibliography|BIBLIOGRAPHY)\b")


class Origin(StrEnum):
    AUTHOR = "author"
    BATCH = "batch"


class EvalQuestion(BaseModel):
    """Una pregunta del conjunto. Si `answerable`, `chunk_id` es el fragmento que la
    responde y `paper_id`, `category` y `year` son los de su paper; si no, los cuatro son
    `None`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str = Field(min_length=1)
    origin: Origin
    question: str = Field(min_length=1, max_length=QUESTION_MAX_CHARACTERS)
    answerable: bool
    chunk_id: str | None = None
    paper_id: str | None = None
    category: str | None = None
    year: int | None = None

    @model_validator(mode="after")
    def gold_matches_answerable(self) -> EvalQuestion:
        if not self.answerable:
            if self.chunk_id is not None or self.paper_id is not None:
                raise ValueError(f"{self.question_id}: sin respuesta no lleva fragmento")
            return self
        if self.chunk_id is None or self.paper_id is None or self.category is None:
            raise ValueError(f"{self.question_id}: le falta el fragmento, el paper o la categoría")
        if paper_of_chunk(self.chunk_id) != self.paper_id:
            raise ValueError(f"{self.question_id}: {self.chunk_id} no es de {self.paper_id}")
        return self

    @property
    def source_language(self) -> str | None:
        """El idioma del documento que responde: `es` el material del curso, `en` un paper."""
        if self.category is None:
            return None
        return COURSE_LANGUAGE if self.category == COURSE_CATEGORY else PAPER_LANGUAGE


@dataclass(frozen=True, slots=True)
class SampledChunk:
    """Un fragmento elegido para escribir una pregunta, con lo que necesita quien la escribe."""

    chunk_id: str
    paper_id: str
    title: str
    category: str
    year: int | None
    text: str

    @property
    def stratum(self) -> str:
        return stratum_key(self.category, self.year)


def paper_of_chunk(chunk_id: str) -> str:
    return chunk_id.rsplit(CHUNK_ORDINAL_SEPARATOR, 1)[0]


def ordinal_of_chunk(chunk_id: str) -> int:
    return int(chunk_id.rsplit(CHUNK_ORDINAL_SEPARATOR, 1)[1])


def stratum_key(category: str, year: int | None) -> str:
    """`cs.DB:2026`; el material del curso es un solo estrato, sin año."""
    if category == COURSE_CATEGORY:
        return COURSE_CATEGORY
    return f"{category}{STRATUM_SEPARATOR}{year}"


def load_questions(path: Path) -> list[EvalQuestion]:
    """Las preguntas de `path`, en su orden.

    Raises:
        ValueError: si una línea no cumple el esquema o se repite un `question_id`.
    """
    questions = [EvalQuestion.model_validate(row) for row in read_jsonl(path)]
    _check_unique_ids(questions)
    return questions


def write_questions(path: Path, questions: Iterable[EvalQuestion]) -> None:
    """Escribe `questions` ordenadas por `question_id`: el mismo conjunto da los mismos bytes.

    Raises:
        ValueError: si se repite un `question_id`.
    """
    ordered = sorted(questions, key=lambda question: question.question_id)
    _check_unique_ids(ordered)
    write_jsonl(path, (question.model_dump(mode="json") for question in ordered))


def _check_unique_ids(questions: Sequence[EvalQuestion]) -> None:
    seen: set[str] = set()
    for question in questions:
        if question.question_id in seen:
            raise ValueError(f"question_id repetido: {question.question_id}")
        seen.add(question.question_id)


def is_prose(text: str) -> bool:
    """Si `text` es prosa que admite una pregunta (ver `MIN_ALPHABETIC_SHARE`)."""
    words = text.split()
    if not words or BIBLIOGRAPHY_HEADING.search(text):
        return False
    if len(BRACKET_CITATION.findall(text)) > MAX_BRACKET_CITATIONS:
        return False
    if len(REFERENCE_YEAR.findall(text)) > MAX_REFERENCE_YEARS:
        return False
    alphabetic = sum(1 for word in words if ALPHABETIC_WORD.match(word))
    return alphabetic / len(words) >= MIN_ALPHABETIC_SHARE


def sample_chunks(
    papers: Sequence[Mapping[str, object]],
    chunks: Iterable[Mapping[str, object]],
    quotas: Mapping[str, int],
    seed: int,
    excluded: frozenset[str] = frozenset(),
) -> list[SampledChunk]:
    """Hasta `quotas[estrato]` fragmentos de prosa de cada estrato, sin los de `excluded`.

    `papers` y `chunks` son las filas de `papers.jsonl` y `fragmentos.jsonl`. Cada estrato
    baraja sus papers con `seed` y toma un fragmento al azar de cada uno, en ese orden; si no
    alcanzan los papers, da otra vuelta. Lee `chunks` una sola vez y solo guarda los de los
    papers candidatos. El resultado va ordenado por estrato y `chunk_id`.
    """
    generator = random.Random(seed)
    candidates = _candidate_papers(papers, quotas, generator)
    wanted = {paper_id for chosen in candidates.values() for paper_id in chosen}
    by_paper = _prose_chunks(chunks, wanted, excluded)
    titles = {str(row["paper_id"]): str(row["title"]) for row in papers}
    sampled: list[SampledChunk] = []
    for stratum, paper_ids in candidates.items():
        picked = _round_robin(paper_ids, by_paper, quotas[stratum], generator)
        sampled.extend(
            SampledChunk(chunk_id, paper_of_chunk(chunk_id), titles[paper_of_chunk(chunk_id)],
                         *_stratum_parts(stratum), text)
            for chunk_id, text in picked
        )  # fmt: skip
    return sorted(sampled, key=lambda chunk: (chunk.stratum, chunk.chunk_id))


def _candidate_papers(
    papers: Iterable[Mapping[str, object]], quotas: Mapping[str, int], generator: random.Random
) -> dict[str, list[str]]:
    by_stratum: dict[str, list[str]] = {}
    for row in papers:
        stratum = stratum_key(str(row["primary_category"]), _year(row))
        if stratum in quotas:
            by_stratum.setdefault(stratum, []).append(str(row["paper_id"]))
    candidates: dict[str, list[str]] = {}
    for stratum in sorted(by_stratum):
        paper_ids = sorted(by_stratum[stratum])
        generator.shuffle(paper_ids)
        candidates[stratum] = paper_ids[: quotas[stratum] * PAPERS_READ_PER_QUOTA]
    return candidates


def _prose_chunks(
    chunks: Iterable[Mapping[str, object]], wanted: set[str], excluded: frozenset[str]
) -> dict[str, list[tuple[str, str]]]:
    by_paper: dict[str, list[tuple[str, str]]] = {}
    for row in chunks:
        paper_id, chunk_id, text = str(row["paper_id"]), str(row["chunk_id"]), str(row["text"])
        if paper_id in wanted and chunk_id not in excluded and is_prose(text):
            by_paper.setdefault(paper_id, []).append((chunk_id, text))
    for found in by_paper.values():
        found.sort(key=lambda chunk: ordinal_of_chunk(chunk[0]))
    return by_paper


def _round_robin(
    paper_ids: Sequence[str],
    by_paper: Mapping[str, list[tuple[str, str]]],
    quota: int,
    generator: random.Random,
) -> list[tuple[str, str]]:
    remaining = {paper_id: list(by_paper.get(paper_id, [])) for paper_id in paper_ids}
    picked: list[tuple[str, str]] = []
    while len(picked) < quota and any(remaining.values()):
        for paper_id in paper_ids:
            available = remaining[paper_id]
            if not available or len(picked) == quota:
                continue
            picked.append(available.pop(generator.randrange(len(available))))
    return picked


def _stratum_parts(stratum: str) -> tuple[str, int | None]:
    if stratum == COURSE_CATEGORY:
        return COURSE_CATEGORY, None
    category, year = stratum.split(STRATUM_SEPARATOR)
    return category, int(year)


def _year(row: Mapping[str, object]) -> int | None:
    value = row.get("year")
    return value if isinstance(value, int) else None
