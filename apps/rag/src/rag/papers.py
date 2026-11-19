"""Las páginas citadas: lo que dice el gestor de una página y el archivo del documento.

Una cita abre el paper en su página. `GET /papers/{id}/pages/{n}` responde en JSON con el
paper, cuántas páginas tiene, los pasajes del corpus que caen en esa página (el texto que se
puede resaltar junto al visor) y la URL del archivo con `#page=n`, el fragmento que entienden
los visores de PDF del navegador y pdf.js. `GET /papers/{id}/file` sirve el archivo tal cual
desde el corpus montado de solo lectura, con `Range`: el visor pide solo los bytes de las
páginas que muestra en lugar de bajar un PDF de 250 páginas entero. El JSON sale del gestor y
funciona aunque el corpus no esté montado; el archivo, no.

El archivo se busca con la ruta que guarda `rag_papers`, nunca con una que llegue en la
petición, y se comprueba que quede dentro del corpus.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from appkit import ResourceNotFoundError, ServiceError, timed
from minigestor_client import AsyncClient

from .pipeline import CHUNK_ID_SEPARATOR
from .schema import CHUNKS_TABLE, PAPERS_TABLE
from .search import PAPER_COLUMNS, Paper, paper_of

PAPER_STAGE = "paper"
PASSAGES_STAGE = "passages"
PAPER_SQL = f"SELECT {PAPER_COLUMNS}, pages FROM {PAPERS_TABLE} WHERE paper_id = $1"
# Los fragmentos de un documento se buscan por el rango de su clave, `<paper_id>#<ordinal>`:
# el gestor lo resuelve en el B+ de la clave primaria (unos 5 ms sobre los 98 585 fragmentos)
# en vez de recorrer la tabla entera por `paper_id`, que no tiene índice (1,5 s).
PASSAGES_SQL = (
    f"SELECT chunk_id, ordinal, page_start, page_end, content FROM {CHUNKS_TABLE} "
    "WHERE chunk_id >= $1 AND chunk_id < $2 AND paper_id = $3 "
    "AND page_start <= $4 AND page_end >= $4 ORDER BY ordinal"
)
PAGE_FRAGMENT = "page"


class PapersFilesUnavailableError(ServiceError):
    """El servicio no tiene el corpus montado y no puede servir archivos."""

    status = HTTPStatus.SERVICE_UNAVAILABLE


class Passage(BaseModel):
    """Un fragmento del corpus que empieza, termina o pasa por la página pedida."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    ordinal: int
    page_start: int
    page_end: int
    text: str


class PageResponse(BaseModel):
    """Una página de un documento: el paper, el total de páginas (secciones en un Markdown),
    el archivo y la URL para abrirlo en esa página (relativas a la raíz del API) y los
    pasajes que caen en ella."""

    paper: Paper
    page: int
    pages: int
    file_url: str
    viewer_url: str
    passages: list[Passage]


class StoredPaper(BaseModel):
    paper: Paper
    pages: int


async def stored_paper(engine: AsyncClient, paper_id: str) -> StoredPaper:
    """El documento `paper_id` con su número de páginas.

    Raises:
        ResourceNotFoundError: si no está en `rag_papers`.
    """
    with timed(PAPER_STAGE):
        row = await engine.fetch_one(PAPER_SQL, [paper_id])
    if row is None:
        raise ResourceNotFoundError(f"no hay un documento '{paper_id}'")
    return StoredPaper(paper=paper_of(row), pages=row["pages"])


async def page_of(engine: AsyncClient, paper_id: str, page: int) -> PageResponse:
    """La página `page` (desde 1) del documento `paper_id`.

    Raises:
        ResourceNotFoundError: si el documento no existe o no tiene esa página.
    """
    stored = await stored_paper(engine, paper_id)
    if page > stored.pages:
        raise ResourceNotFoundError(
            f"'{paper_id}' tiene {stored.pages} páginas; no hay una página {page}"
        )
    with timed(PASSAGES_STAGE):
        rows = await engine.fetch_all(PASSAGES_SQL, [*chunk_key_range(paper_id), paper_id, page])
    file_url = file_url_of(paper_id)
    return PageResponse(
        paper=stored.paper,
        page=page,
        pages=stored.pages,
        file_url=file_url,
        viewer_url=f"{file_url}#{PAGE_FRAGMENT}={page}",
        passages=[
            Passage(
                chunk_id=row["chunk_id"],
                ordinal=row["ordinal"],
                page_start=row["page_start"],
                page_end=row["page_end"],
                text=row["content"],
            )
            for row in rows
        ],
    )


def chunk_key_range(paper_id: str) -> tuple[str, str]:
    """Las cotas `[desde, hasta)` de las claves de los fragmentos de `paper_id`: todas
    empiezan con `<paper_id>#`, y el carácter siguiente a `#` las cierra por arriba."""
    first = paper_id + CHUNK_ID_SEPARATOR
    return first, paper_id + chr(ord(CHUNK_ID_SEPARATOR) + 1)


def file_url_of(paper_id: str) -> str:
    return f"/papers/{quote(paper_id, safe=':')}/file"


def corpus_file(corpus_root: Path | None, paper: Paper) -> Path:
    """El archivo de `paper` dentro de `corpus_root`.

    Raises:
        PapersFilesUnavailableError: si el servicio no tiene el corpus montado.
        ResourceNotFoundError: si el archivo no está o su ruta sale del corpus.
    """
    if corpus_root is None:
        raise PapersFilesUnavailableError("el servicio no tiene montado el corpus de papers")
    root = corpus_root.resolve()
    path = (root / paper.file_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ResourceNotFoundError(f"el archivo de '{paper.paper_id}' no está en el corpus")
    return path
