"""Material propio del curso: el enunciado del proyecto (Markdown) y el informe (PDF).

Un PDF se trata igual que un paper. Un Markdown no tiene páginas: su «página» `n` es su
sección `n`, cortada en cada encabezado, y la marca de Markdown (énfasis, código, enlaces,
tablas) se quita para que quede texto corrido.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import DocumentInformation, PdfReader

from .cleaning import page_lines
from .extraction import MALFORMED_PDF_ERRORS

COURSE_SOURCE = "curso"
COURSE_ID_PREFIX = "curso:"
PDF_SUFFIX = ".pdf"
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
PDF_MEDIA_TYPE = "application/pdf"
MARKDOWN_MEDIA_TYPE = "text/markdown"
ENCODING = "utf-8"
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
MARKUP = re.compile(r"(\*\*|__|`|^>\s?|^\s*[-*+]\s+(?=\S))", re.MULTILINE)
TABLE_RULE = re.compile(r"^\|?[\s:|-]+\|?$")
SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class CourseDocument:
    """Un documento del curso con la metadata que se puede sacar de él mismo."""

    path: Path
    paper_id: str
    title: str
    authors: tuple[str, ...]
    year: int | None
    media_type: str


def is_supported(path: Path) -> bool:
    return path.suffix.lower() == PDF_SUFFIX or path.suffix.lower() in MARKDOWN_SUFFIXES


def describe(path: Path) -> CourseDocument:
    """La metadata de un material del curso. El título es el primer encabezado del
    Markdown, o el del diccionario de información del PDF o su primera línea de texto; si
    no hay ninguno, el nombre del archivo."""
    paper_id = COURSE_ID_PREFIX + slug(path.stem)
    if path.suffix.lower() in MARKDOWN_SUFFIXES:
        heading = _markdown_title(path.read_text(encoding=ENCODING))
        return CourseDocument(path, paper_id, heading or path.stem, (), None, MARKDOWN_MEDIA_TYPE)
    title, authors, year = _pdf_information(path)
    return CourseDocument(path, paper_id, title or path.stem, authors, year, PDF_MEDIA_TYPE)


def markdown_sections(text: str) -> list[str]:
    """El texto corrido de cada sección del Markdown, en orden; lo que está antes del
    primer encabezado es la sección 1."""
    sections: list[list[str]] = [[]]
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading and any(sections[-1]):
            sections.append([])
        if not TABLE_RULE.match(line.strip()):
            sections[-1].append(heading.group(2) if heading else line)
    return [_plain_text(lines) for lines in sections if any(line.strip() for line in lines)]


def slug(text: str) -> str:
    """`Proyecto_Integrador_BD2` → `proyecto-integrador-bd2`."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return SLUG_SEPARATORS.sub("-", ascii_text.casefold()).strip("-")


def _plain_text(lines: list[str]) -> str:
    text = LINK.sub(r"\1", "\n".join(lines))
    text = MARKUP.sub("", text).replace("|", " ")
    return " ".join(page_lines(text))


def _markdown_title(text: str) -> str | None:
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading:
            return " ".join(heading.group(2).split())
    return None


def _pdf_information(path: Path) -> tuple[str | None, tuple[str, ...], int | None]:
    try:
        reader = PdfReader(path)
        information = reader.metadata
        first_page = reader.pages[0].extract_text() if reader.pages else ""
    except MALFORMED_PDF_ERRORS:
        return None, (), None
    lines = page_lines(first_page)
    first_line = lines[0] if lines else None
    if information is None:
        return first_line, (), None
    author = information.author
    title = information.title or first_line
    return title, (author,) if author else (), _pdf_year(information)


def _pdf_year(information: DocumentInformation) -> int | None:
    try:
        created = information.creation_date
    except ValueError:
        return None
    return created.year if created else None
