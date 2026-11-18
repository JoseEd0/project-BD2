"""Fragmentación por oraciones con solapamiento y página de origen.

El documento se lee como una sola secuencia de oraciones, así que una oración que cruza
el salto de página no se corta: cuenta en la página donde empieza. Cada fragmento suma
oraciones enteras hasta `target_words` (sin pasar de `max_words`) y el siguiente empieza
repitiendo las últimas oraciones del anterior (`overlap_words` a `max_overlap_words`).

Un fragmento recuerda la página donde empieza (`page_start`, la que abre la cita) y la
última que toca (`page_end`). Si el final del documento no llega a `min_words`, el último
fragmento se completa hacia atrás con oraciones del anterior: todos los fragmentos quedan
entre `min_words` y `max_words`, salvo el de un documento entero más corto que el mínimo.

La única excepción a «no cortar oraciones» es una «oración» de más de
`long_sentence_words` palabras: en un PDF eso es una tabla, una fórmula o una lista de
referencias sin puntos, y se parte por palabras (`Chunk.forced_splits` lo cuenta).
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .settings import ChunkSettings

# Fin de oración: un punto, cierre de pregunta o exclamación, quizá seguido de comillas o
# paréntesis de cierre, y espacio. Si es fin de verdad lo decide `_starts_sentence` con
# lo que viene después y `_is_abbreviation` con lo que viene antes.
SENTENCE_END = re.compile(r"[.!?][\"'\u201d\u2019)\]]*\s+")
PAGE_JOINER = " "
SENTENCE_OPENERS = frozenset("\"'\u201c\u2018([¿¡")
ABBREVIATIONS = frozenset(
    {
        "e.g",
        "i.e",
        "al",
        "fig",
        "figs",
        "eq",
        "eqs",
        "sec",
        "secs",
        "vs",
        "cf",
        "ref",
        "refs",
        "no",
        "vol",
        "pp",
        "approx",
        "resp",
        "dr",
        "mr",
        "ms",
        "prof",
        "def",
        "thm",
        "lem",
        "tab",
        "ch",
        "ej",
        "pág",
        "núm",
    }
)


@dataclass(frozen=True, slots=True)
class Sentence:
    """Una oración, la página (desde 1) donde empieza y la página donde termina."""

    text: str
    page: int
    last_page: int

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True, slots=True)
class Chunk:
    """Un fragmento: oraciones enteras, su página de inicio y la última que toca."""

    text: str
    page_start: int
    page_end: int
    words: int
    forced_splits: int


def split_sentences(text: str) -> Iterator[tuple[int, str]]:
    """Las oraciones de `text` con la posición donde empieza cada una."""
    start = 0
    for boundary in SENTENCE_END.finditer(text):
        end = boundary.end()
        if (
            end < len(text)
            and _starts_sentence(text[end])
            and not _is_abbreviation(text[start : boundary.start()])
        ):
            yield start, text[start : boundary.start() + 1 + _closers(boundary)].strip()
            start = end
    if text[start:].strip():
        yield start, text[start:].strip()


def document_sentences(pages: Sequence[str]) -> list[Sentence]:
    """Las oraciones de un documento cuya página `n` está en `pages[n - 1]`."""
    page_starts: list[int] = []
    offset = 0
    for page in pages:
        page_starts.append(offset)
        offset += len(page) + len(PAGE_JOINER)
    return [
        Sentence(
            sentence,
            bisect.bisect_right(page_starts, position),
            bisect.bisect_right(page_starts, position + len(sentence) - 1),
        )
        for position, sentence in split_sentences(PAGE_JOINER.join(pages))
    ]


def chunk_pages(pages: Sequence[str], settings: ChunkSettings) -> list[Chunk]:
    """Los fragmentos de un documento cuya página `n` está en `pages[n - 1]`."""
    return chunk_sentences(document_sentences(pages), settings)


def chunk_sentences(sentences: Sequence[Sentence], settings: ChunkSettings) -> list[Chunk]:
    """Los fragmentos de una secuencia de oraciones (ver el docstring del módulo)."""
    units = [piece for sentence in sentences for piece in _split_long(sentence, settings)]
    windows: list[tuple[int, int]] = []
    start = 0
    while start < len(units):
        end = _window_end(units, start, settings)
        if end == len(units) and windows and _words(units, start, end) < settings.min_words:
            _close_with_tail(units, windows, settings)
            break
        windows.append((start, end))
        if end == len(units):
            break
        start = _overlap_start(units, start, end, settings)
    return [_chunk(units[first:last]) for first, last in windows]


def _starts_sentence(character: str) -> bool:
    return character.isupper() or character.isdigit() or character in SENTENCE_OPENERS


def _is_abbreviation(sentence_so_far: str) -> bool:
    words = sentence_so_far.split()
    if not words:
        return True
    last = words[-1].lstrip("([\"'").casefold()
    is_initial = len(last) == 1 and last.isalpha()
    return is_initial or last in ABBREVIATIONS


def _closers(boundary: re.Match[str]) -> int:
    return len(boundary.group().rstrip()) - 1


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    page: int
    last_page: int
    words: int
    forced: bool


def _split_long(sentence: Sentence, settings: ChunkSettings) -> list[_Unit]:
    words = sentence.text.split()
    limit = settings.long_sentence_words
    if len(words) <= limit:
        return [_Unit(sentence.text, sentence.page, sentence.last_page, len(words), forced=False)]
    pieces = [words[first : first + limit] for first in range(0, len(words), limit)]
    return [
        _Unit(" ".join(piece), sentence.page, sentence.last_page, len(piece), forced=True)
        for piece in pieces
    ]


def _words(units: Sequence[_Unit], start: int, end: int) -> int:
    return sum(unit.words for unit in units[start:end])


def _window_end(units: Sequence[_Unit], start: int, settings: ChunkSettings) -> int:
    end = start
    words = 0
    while end < len(units):
        grown = words + units[end].words
        fits_target = grown <= settings.target_words
        needs_more = words < settings.min_words and grown <= settings.max_words
        if end > start and not (fits_target or needs_more):
            break
        words = grown
        end += 1
    return end


def _overlap_start(units: Sequence[_Unit], start: int, end: int, settings: ChunkSettings) -> int:
    first = end
    words = 0
    while first - 1 > start and words < settings.overlap_words:
        candidate = words + units[first - 1].words
        if candidate > settings.max_overlap_words:
            break
        words = candidate
        first -= 1
    return first


def _close_with_tail(
    units: Sequence[_Unit], windows: list[tuple[int, int]], settings: ChunkSettings
) -> None:
    first = len(units)
    words = 0
    while first > 0 and words < settings.min_words:
        candidate = words + units[first - 1].words
        if candidate > settings.max_words:
            break
        words = candidate
        first -= 1
    while windows and windows[-1][0] >= first:
        windows.pop()
    windows.append((first, len(units)))


def _chunk(units: Sequence[_Unit]) -> Chunk:
    return Chunk(
        text=" ".join(unit.text for unit in units),
        page_start=units[0].page,
        page_end=units[-1].last_page,
        words=sum(unit.words for unit in units),
        forced_splits=sum(unit.forced for unit in units),
    )
