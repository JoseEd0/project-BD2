"""Guiones de fin de línea: ¿`exam-` + `ple` es `example` o `data-` + `driven` es
`data-driven`?

Sin diccionario, la prueba sale del propio documento. Las palabras que el documento escribe
**enteras** en alguna línea (no partidas entre dos) son la evidencia; ante una línea que
termina en `X-` y una siguiente que empieza con `Y`, se decide en este orden:

1. `X-Y` aparece entera en el documento: se deja el guion (`data-driven`).
2. `XY` aparece entera: se une (`example`).
3. `X` e `Y` aparecen sueltas, ambas de al menos `MIN_EVIDENCE_PART_CHARS` letras, y
   `XY` nunca: se deja el guion (`high-dimensional` en un paper que dice «high» y
   «dimensional» pero nunca «highdimensional»). El mínimo evita que un prefijo corto que
   también es palabra (`in-` + `formation`, `de-` + `sign`) pase por compuesto.
4. Sin evidencia: se deja el guion si `X` es un número o tiene dígitos (`2-dimensional`,
   un rango `10-20`), una sola letra (`k-nearest`), una sigla en mayúsculas (`B-tree`),
   si ya traía un guion (`end-to-` + `end`) o si `Y` no empieza en minúscula; si no, se
   une, que es lo que hace la mayoría de los cortes de palabra de un PDF.

Todo se compara sin mayúsculas, y la decisión es determinista: solo depende del texto.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

HYPHEN = "-"
WORD = re.compile(r"[^\W_]+(?:-[^\W_]+)*")
TRAILING_WORD = re.compile(r"[^\W_]+(?:-[^\W_]+)*$")
LEADING_PART = re.compile(r"[^\W_]+")
HYPHENATED_LINE_END = re.compile(r"[^\W_]-$")
MIN_EVIDENCE_PART_CHARS = 3


class Hyphenation(StrEnum):
    """Qué se hizo con un guion de fin de línea y por qué regla."""

    KEPT_COMPOUND_SEEN = "kept_compound_seen"
    JOINED_WORD_SEEN = "joined_word_seen"
    KEPT_PARTS_SEEN = "kept_parts_seen"
    KEPT_BY_DEFAULT = "kept_by_default"
    JOINED_BY_DEFAULT = "joined_by_default"

    @property
    def keeps_hyphen(self) -> bool:
        return self not in {Hyphenation.JOINED_WORD_SEEN, Hyphenation.JOINED_BY_DEFAULT}


@dataclass(frozen=True, slots=True)
class Evidence:
    """Las palabras que un documento escribe enteras: `forms` con sus guiones internos
    (`data-driven`) y `parts` cada trozo entre guiones (`data`, `driven`), sin mayúsculas."""

    forms: frozenset[str]
    parts: frozenset[str]

    @classmethod
    def from_pages(cls, pages: Iterable[Sequence[str]]) -> Evidence:
        """La evidencia de un documento cuyas páginas son listas de líneas. La última
        palabra de una línea que termina en guion y la primera de la siguiente no cuentan:
        son los trozos que hay que decidir."""
        forms = {word.casefold() for lines in pages for word in _whole_words(lines)}
        parts = {part for form in forms for part in form.split(HYPHEN)}
        return cls(frozenset(forms), frozenset(parts))

    def decide(self, head: str, tail: str) -> Hyphenation:
        """Qué hacer con `head-` al final de una línea seguido de `tail` en la siguiente
        (ver las reglas en el docstring del módulo)."""
        if f"{head}{HYPHEN}{tail}".casefold() in self.forms:
            return Hyphenation.KEPT_COMPOUND_SEEN
        if f"{head}{tail}".casefold() in self.forms:
            return Hyphenation.JOINED_WORD_SEEN
        last = head.rsplit(HYPHEN, 1)[-1]
        if self._are_words(last, tail):
            return Hyphenation.KEPT_PARTS_SEEN
        if _looks_like_compound(head, last, tail):
            return Hyphenation.KEPT_BY_DEFAULT
        return Hyphenation.JOINED_BY_DEFAULT

    def _are_words(self, *candidates: str) -> bool:
        return all(
            len(candidate) >= MIN_EVIDENCE_PART_CHARS and candidate.casefold() in self.parts
            for candidate in candidates
        )


def join_lines(lines: Sequence[str], evidence: Evidence, tally: Counter[str]) -> str:
    """Las líneas en un solo texto separadas por espacios; cada guion de fin de línea se
    deja o se quita según `evidence`, y la regla que lo decidió se cuenta en `tally`."""
    pieces: list[str] = []
    for line in lines:
        tail = LEADING_PART.match(line)
        if not pieces or tail is None or not HYPHENATED_LINE_END.search(pieces[-1]):
            pieces.append(line)
            continue
        head = pieces[-1][:-1]
        head_word = TRAILING_WORD.search(head)
        if head_word is None:
            pieces.append(line)
            continue
        decision = evidence.decide(head_word.group(), tail.group())
        tally[decision] += 1
        pieces[-1] = head + (HYPHEN if decision.keeps_hyphen else "") + line
    return " ".join(pieces)


def _whole_words(lines: Sequence[str]) -> Iterable[str]:
    broken_before = False
    for line in lines:
        words = WORD.findall(line)
        broken_after = bool(HYPHENATED_LINE_END.search(line))
        first = 1 if broken_before else 0
        last = len(words) - 1 if broken_after else len(words)
        yield from words[first:last]
        broken_before = broken_after


def _looks_like_compound(head: str, last: str, tail: str) -> bool:
    return (
        HYPHEN in head
        or len(last) == 1
        or any(character.isdigit() for character in last)
        or last.isupper()
        or not tail[0].islower()
    )
