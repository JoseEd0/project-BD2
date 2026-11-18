"""Limpieza del texto que pypdf saca de cada página.

1. Caracteres: se unen los pares sustitutos sueltos, NFKC deshace las ligaduras
   (`ﬁ` → `fi`) y las letras matemáticas en cursiva (U+1D465 → `x`), y se quitan los
   guiones blandos y los caracteres de control.
2. Encabezados y pies: una línea del borde de la página que se repite en muchas páginas
   (con los números igualados, para que «Página 3» y «Página 4» cuenten como la misma)
   se borra. También el sello lateral de arXiv de la primera página.
3. Guiones de fin de línea: `exam-` + `ple` se une en `example` cuando la línea siguiente
   sigue en minúscula. Una palabra compuesta partida justo en su guion (`data-driven`)
   pierde el guion: sin diccionario no se distinguen, y para buscar pesa menos que dejar
   miles de palabras partidas.
4. Espacios: cada página queda en una sola línea con espacios simples; los fragmentos se
   cortan por oraciones, no por líneas.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from typing import Final

from .settings import CleaningSettings

SOFT_HYPHEN = "\N{SOFT HYPHEN}"
CONTROL_CATEGORY = "Cc"
NEWLINE = "\n"
NORMALIZATION_FORM: Final = "NFKC"
UTF16 = "utf-16"
SURROGATE_PASS = "surrogatepass"
REPLACE_INVALID = "replace"
DIGITS = re.compile(r"\d+")
DIGIT_PLACEHOLDER = "#"
HYPHENATED_LINE_END = re.compile(r"[^\W\d_]-$")
ARXIV_STAMP = re.compile(r"^arXiv:\S+\s+\[[\w.-]+\]\s+\d{1,2}\s+\w{3}\s+\d{4}$", re.IGNORECASE)


def clean_pages(raw_pages: Sequence[str], settings: CleaningSettings) -> list[str]:
    """El texto limpio de cada página, en el mismo orden; una página sin texto queda `""`."""
    pages = [page_lines(raw) for raw in raw_pages]
    without_edges = remove_repeated_edges(pages, settings)
    return [
        join_lines([line for line in lines if not ARXIV_STAMP.match(line)])
        for lines in without_edges
    ]


def normalize_characters(text: str) -> str:
    """NFKC, sin guiones blandos y con cada carácter de control (salvo el salto de línea)
    cambiado por un espacio."""
    normalized = unicodedata.normalize(NORMALIZATION_FORM, join_surrogates(text))
    normalized = normalized.replace(SOFT_HYPHEN, "")
    return "".join(
        " "
        if unicodedata.category(character) == CONTROL_CATEGORY and character != NEWLINE
        else character
        for character in normalized
    )


def join_surrogates(text: str) -> str:
    """Une los pares sustitutos que pypdf deja sueltos (las letras matemáticas de un PDF
    llegan partidas en dos) y cambia por U+FFFD los que no tienen pareja, que no se
    pueden escribir en UTF-8."""
    return text.encode(UTF16, SURROGATE_PASS).decode(UTF16, REPLACE_INVALID)


def page_lines(raw_text: str) -> list[str]:
    """Las líneas no vacías de una página, con los espacios normalizados."""
    lines = (" ".join(line.split()) for line in normalize_characters(raw_text).split(NEWLINE))
    return [line for line in lines if line]


def edge_key(line: str) -> str:
    """Cómo se compara una línea del borde: sin mayúsculas y con cada número igualado."""
    return DIGITS.sub(DIGIT_PLACEHOLDER, line.casefold())


def remove_repeated_edges(
    pages: Sequence[Sequence[str]], settings: CleaningSettings
) -> list[list[str]]:
    """Las páginas sin las líneas de borde que se repiten (ver `CleaningSettings`)."""
    if len(pages) < settings.min_repeated_pages:
        return [list(lines) for lines in pages]
    threshold = max(
        settings.min_repeated_pages, math.ceil(settings.repeated_page_share * len(pages))
    )
    counts = Counter(
        key for lines in pages for key in {edge_key(line) for line in _edges(lines, settings)}
    )
    repeated = {key for key, count in counts.items() if count >= threshold}
    return [_drop_repeated(lines, repeated, settings.edge_lines) for lines in pages]


def join_lines(lines: Sequence[str]) -> str:
    """Las líneas en un solo texto, uniendo las palabras partidas con guion al final de
    una línea cuando la siguiente sigue en minúscula."""
    pieces: list[str] = []
    for line in lines:
        if pieces and HYPHENATED_LINE_END.search(pieces[-1]) and line[0].islower():
            pieces[-1] = pieces[-1][:-1] + line
        else:
            pieces.append(line)
    return " ".join(pieces)


def _edges(lines: Sequence[str], settings: CleaningSettings) -> list[str]:
    return [*lines[: settings.edge_lines], *lines[-settings.edge_lines :]]


def _drop_repeated(lines: Sequence[str], repeated: set[str], edge_lines: int) -> list[str]:
    last_edge = len(lines) - edge_lines
    return [
        line
        for index, line in enumerate(lines)
        if not ((index < edge_lines or index >= last_edge) and edge_key(line) in repeated)
    ]
