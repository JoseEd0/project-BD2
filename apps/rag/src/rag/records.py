"""Lo que el corpus deja en disco: una fila por paper, una por fragmento y una por falla.

Es el contrato con la ingesta (#85) y con las citas: el esquema está descrito en el README
del servicio y cambiarlo es cambiar ese contrato.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class Paper:
    """Un documento del corpus: un paper de arXiv o un material del curso.

    `file_path` es relativo a la carpeta del corpus. `pages` cuenta páginas del PDF, o
    secciones si `media_type` es Markdown.
    """

    paper_id: str
    source: str
    title: str
    authors: tuple[str, ...]
    year: int | None
    abstract: str
    primary_category: str
    categories: tuple[str, ...]
    license: str | None
    url: str | None
    file_path: str
    media_type: str
    sha256: str
    bytes: int
    pages: int
    empty_pages: int
    words: int
    chunks: int

    def to_json(self) -> dict[str, object]:
        row = asdict(self)
        row["authors"] = list(self.authors)
        row["categories"] = list(self.categories)
        return row


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """Un fragmento. `page_start` es la página que abre la cita y `page_end` la última que
    toca el fragmento; `sha256` es la huella de `text`, con la que se deduplica."""

    chunk_id: str
    paper_id: str
    ordinal: int
    page_start: int
    page_end: int
    words: int
    text: str
    sha256: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Failure:
    """Un documento que no entró al corpus: en qué paso (`descarga`, `extracción`,
    `duplicado`) y por qué."""

    paper_id: str
    stage: str
    reason: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)
