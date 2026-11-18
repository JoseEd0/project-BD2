"""Texto por página de un PDF con pypdf, limpio y listo para fragmentar.

Un PDF dañado lanza `UnreadablePdfError` y uno sin capa de texto (escaneado) lanza
`TextlessPdfError`; quien llama los registra y sigue con el siguiente. Una página suelta
que pypdf no puede leer queda vacía sin tirar el resto del documento.
"""

from __future__ import annotations

import logging
import zlib
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .cleaning import clean_pages
from .errors import TextlessPdfError, UnreadablePdfError
from .settings import CleaningSettings

logger = logging.getLogger(__name__)

# pypdf no envuelve todos sus fallos en `PyPdfError`: una tabla de referencias cruzada rota o
# un flujo mal comprimido llegan como el error de Python que salte al recorrerlos.
MALFORMED_PDF_ERRORS = (
    PyPdfError,
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    AttributeError,
    RecursionError,
    zlib.error,
)


def read_raw_pages(path: Path) -> list[str]:
    """El texto crudo de cada página, en orden.

    Raises:
        UnreadablePdfError: si pypdf no puede abrir el documento ni contar sus páginas.
    """
    try:
        reader = PdfReader(path)
        page_count = len(reader.pages)
    except MALFORMED_PDF_ERRORS as error:
        raise UnreadablePdfError(f"{path.name}: {type(error).__name__}: {error}") from error
    return [_page_text(reader, index, path) for index in range(page_count)]


def extract_pages(path: Path, settings: CleaningSettings) -> list[str]:
    """El texto limpio de cada página de `path` (la página `n` en la posición `n - 1`).

    Raises:
        UnreadablePdfError: si el PDF está dañado.
        TextlessPdfError: si tiene menos de `min_words_per_page` palabras por página.
    """
    raw_pages = read_raw_pages(path)
    pages = clean_pages(raw_pages, settings)
    words = sum(len(page.split()) for page in pages)
    if not pages or words < settings.min_words_per_page * len(pages):
        raise TextlessPdfError(f"{path.name}: {words} palabras en {len(pages)} páginas")
    return pages


def _page_text(reader: PdfReader, index: int, path: Path) -> str:
    try:
        return reader.pages[index].extract_text()
    except MALFORMED_PDF_ERRORS as error:
        logger.warning("%s, página %d ilegible: %s", path.name, index + 1, error)
        return ""
