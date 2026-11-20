"""Las citas `[n]` de una respuesta, comprobadas contra los fragmentos que se enviaron.

Se reconocen `[2]`, `[1][3]` y `[1, 3]`. Una cita es válida si su número es el de un
fragmento enviado; cualquier otra —un número inventado, o una referencia bibliográfica que el
modelo copió de un pasaje— se informa como inválida, y la respuesta no se reescribe.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from .prompt import Source

CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
NUMBER_SEPARATOR = re.compile(r"\s*,\s*")


class Citation(BaseModel):
    """Un fragmento citado: su número, de dónde sale y cuántas veces se citó."""

    model_config = ConfigDict(frozen=True)

    n: int
    chunk_id: str
    paper_id: str
    title: str
    page_start: int
    page_end: int
    count: int


class CitationReport(BaseModel):
    """Las citas válidas en orden de primera aparición y los números que no corresponden a
    ningún fragmento enviado, sin repetir."""

    model_config = ConfigDict(frozen=True)

    citations: list[Citation]
    invalid: list[int]


def cited_numbers(text: str) -> list[int]:
    """Cada número citado en `text`, en orden y con repeticiones."""
    return [
        int(number)
        for match in CITATION.finditer(text)
        for number in NUMBER_SEPARATOR.split(match.group(1))
    ]


def check_citations(text: str, sources: Sequence[Source]) -> CitationReport:
    """Las citas de `text` contra `sources`."""
    numbers = cited_numbers(text)
    by_number = {source.n: source for source in sources}
    counts = Counter(numbers)
    first_seen = list(dict.fromkeys(numbers))
    return CitationReport(
        citations=[
            _citation(by_number[number], counts[number])
            for number in first_seen
            if number in by_number
        ],
        invalid=[number for number in first_seen if number not in by_number],
    )


def _citation(source: Source, count: int) -> Citation:
    hit = source.hit
    return Citation(
        n=source.n,
        chunk_id=hit.chunk_id,
        paper_id=hit.paper.paper_id,
        title=hit.paper.title,
        page_start=hit.page_start,
        page_end=hit.page_end,
        count=count,
    )
