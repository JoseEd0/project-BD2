"""Cosecha de la metadata de arXiv por OAI-PMH y elección de los papers del corpus.

El API Atom de arXiv (`export.arxiv.org/api/query`) no dice la licencia de un paper; el
formato `arXiv` de OAI-PMH sí (`<license>`), junto con autores, resumen, fecha de envío y
categorías, la primera de las cuales es la principal. Cada categoría es un conjunto OAI
(`cs.DB` → `cs:cs:DB`) que se recorre con `resumptionToken` hasta el final.
"""

from __future__ import annotations

import logging
import urllib.parse
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from .errors import HarvestError
from .fetch import PoliteClient
from .licenses import is_redistributable
from .settings import HarvestSettings

logger = logging.getLogger(__name__)

OAI_NAMESPACE = "{http://www.openarchives.org/OAI/2.0/}"
ARXIV_NAMESPACE = "{http://arxiv.org/OAI/arXiv/}"
NO_RECORDS_CODE = "noRecordsMatch"
DELETED_STATUS = "deleted"
YEAR_DIGITS = 4


@dataclass(frozen=True, slots=True)
class ArxivRecord:
    """La metadata de un paper tal como la publica arXiv."""

    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    created: str
    abstract: str
    categories: tuple[str, ...]
    license: str | None

    @property
    def primary_category(self) -> str:
        return self.categories[0]

    @property
    def year(self) -> int:
        return int(self.created[:YEAR_DIGITS])

    @property
    def file_stem(self) -> str:
        """Nombre de archivo del paper: los identificadores antiguos (`cs/0101001`) llevan
        una barra."""
        return self.arxiv_id.replace("/", "_")

    def to_json(self) -> dict[str, object]:
        return {
            "arxiv_id": self.arxiv_id,
            "title": self.title,
            "authors": list(self.authors),
            "created": self.created,
            "abstract": self.abstract,
            "categories": list(self.categories),
            "license": self.license,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> ArxivRecord:
        license_url = data["license"]
        return cls(
            arxiv_id=str(data["arxiv_id"]),
            title=str(data["title"]),
            authors=tuple(str(author) for author in _as_list(data["authors"])),
            created=str(data["created"]),
            abstract=str(data["abstract"]),
            categories=tuple(str(category) for category in _as_list(data["categories"])),
            license=None if license_url is None else str(license_url),
        )


@dataclass(frozen=True, slots=True)
class RecordsPage:
    """Una respuesta de `ListRecords`: sus registros y el token de la siguiente, si hay."""

    records: tuple[ArxivRecord, ...]
    resumption_token: str | None


def oai_set(category: str) -> str:
    """El conjunto OAI de una categoría de arXiv: `cs.DB` → `cs:cs:DB`."""
    archive, _, subject = category.partition(".")
    if not subject:
        raise HarvestError(f"categoría de arXiv sin área: {category!r}")
    return f"{archive}:{archive}:{subject}"


def list_records_url(settings: HarvestSettings, category: str) -> str:
    """La primera petición de `ListRecords` de una categoría."""
    query = {
        "verb": "ListRecords",
        "metadataPrefix": settings.metadata_prefix,
        "set": oai_set(category),
        "from": settings.from_date,
    }
    return f"{settings.endpoint}?{urllib.parse.urlencode(query)}"


def resumption_url(settings: HarvestSettings, token: str) -> str:
    """La petición que sigue a una respuesta con `resumptionToken`: el token es opaco y
    viaja codificado."""
    query = {"verb": "ListRecords", "resumptionToken": token}
    return f"{settings.endpoint}?{urllib.parse.urlencode(query)}"


def parse_records_page(document: bytes) -> RecordsPage:
    """Los registros de una respuesta OAI-PMH en formato `arXiv`. Los registros borrados se
    omiten; `noRecordsMatch` es una página vacía.

    Raises:
        HarvestError: si el XML no se puede leer o el servidor respondió otro error OAI.
    """
    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as error:
        raise HarvestError(f"respuesta OAI-PMH ilegible: {error}") from error
    error_element = root.find(f"{OAI_NAMESPACE}error")
    if error_element is not None:
        if error_element.get("code") == NO_RECORDS_CODE:
            return RecordsPage((), None)
        raise HarvestError(f"error OAI-PMH {error_element.get('code')}: {error_element.text}")
    listing = root.find(f"{OAI_NAMESPACE}ListRecords")
    if listing is None:
        raise HarvestError("la respuesta OAI-PMH no trae ListRecords")
    records = tuple(
        _parse_record(element)
        for element in listing.iter(f"{OAI_NAMESPACE}record")
        if not _is_deleted(element)
    )
    token = (listing.findtext(f"{OAI_NAMESPACE}resumptionToken") or "").strip()
    return RecordsPage(records, token or None)


def harvest(client: PoliteClient, settings: HarvestSettings) -> list[ArxivRecord]:
    """Todos los registros de las categorías de `settings`, sin repetir el que está en
    varias, en el orden en que llegan.

    Raises:
        HarvestError, DownloadError: si alguna página no llega o no se entiende.
    """
    unique: dict[str, ArxivRecord] = {}
    for category in settings.categories:
        for record in _harvest_category(client, settings, category):
            unique.setdefault(record.arxiv_id, record)
    return list(unique.values())


def in_scope(records: Iterable[ArxivRecord], categories: Sequence[str]) -> list[ArxivRecord]:
    """Los registros cuya categoría principal es una de `categories`: un paper de
    aprendizaje automático que solo se lista también en cs.IR no entra."""
    wanted = set(categories)
    return [record for record in records if record.primary_category in wanted]


def select_papers(
    records: Iterable[ArxivRecord], categories: Sequence[str], limit: int
) -> list[ArxivRecord]:
    """Hasta `limit` papers con licencia que permite redistribuir, alternando entre las
    `categories` y del más reciente al más antiguo dentro de cada una. Si una categoría se
    agota, las demás completan el cupo. El orden es determinista."""
    queues: dict[str, list[ArxivRecord]] = {category: [] for category in categories}
    for record in records:
        if record.primary_category in queues and is_redistributable(record.license):
            queues[record.primary_category].append(record)
    for queue in queues.values():
        queue.sort(key=lambda record: (record.created, record.arxiv_id), reverse=True)
    return list(_round_robin(list(queues.values()), limit))


def pdf_url(settings: HarvestSettings, record: ArxivRecord) -> str:
    return settings.pdf_url_template.format(arxiv_id=record.arxiv_id)


def _harvest_category(
    client: PoliteClient, settings: HarvestSettings, category: str
) -> Iterator[ArxivRecord]:
    url: str | None = list_records_url(settings, category)
    pages = 0
    while url is not None:
        page = parse_records_page(client.get(url))
        pages += 1
        logger.info("%s: página %d, %d registros", category, pages, len(page.records))
        yield from page.records
        url = resumption_url(settings, page.resumption_token) if page.resumption_token else None


def _round_robin(queues: list[list[ArxivRecord]], limit: int) -> Iterator[ArxivRecord]:
    taken = 0
    position = 0
    while taken < limit and any(position < len(queue) for queue in queues):
        for queue in queues:
            if taken < limit and position < len(queue):
                yield queue[position]
                taken += 1
        position += 1


def _is_deleted(record: ElementTree.Element) -> bool:
    header = record.find(f"{OAI_NAMESPACE}header")
    return header is not None and header.get("status") == DELETED_STATUS


def _parse_record(record: ElementTree.Element) -> ArxivRecord:
    metadata = record.find(f"{OAI_NAMESPACE}metadata/{ARXIV_NAMESPACE}arXiv")
    if metadata is None:
        raise HarvestError("un registro OAI-PMH no trae metadata en formato arXiv")
    categories = tuple(_text(metadata, "categories").split())
    if not categories:
        raise HarvestError(f"el registro {_text(metadata, 'id')} no tiene categorías")
    license_url = _text(metadata, "license")
    return ArxivRecord(
        arxiv_id=_text(metadata, "id"),
        title=_text(metadata, "title"),
        authors=tuple(_author_name(author) for author in metadata.iter(f"{ARXIV_NAMESPACE}author")),
        created=_text(metadata, "created"),
        abstract=_text(metadata, "abstract"),
        categories=categories,
        license=license_url or None,
    )


def _author_name(author: ElementTree.Element) -> str:
    parts = (_text(author, "forenames"), _text(author, "keyname"), _text(author, "suffix"))
    return " ".join(part for part in parts if part)


def _text(element: ElementTree.Element, tag: str) -> str:
    return " ".join((element.findtext(f"{ARXIV_NAMESPACE}{tag}") or "").split())


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise HarvestError(f"se esperaba una lista y llegó {type(value).__name__}")
    return value
