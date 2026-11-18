"""Construcción del corpus de punta a punta: cosecha, descarga, extracción y fragmentos.

Cada paso deja su resultado en disco y la corrida siguiente lo reutiliza, así que cortar y
volver a correr sigue desde donde quedó:

- la cosecha de arXiv se guarda con la consulta que la produjo y solo se repite si la
  consulta cambia (o si se borra `cosecha.json`);
- un PDF que ya está en disco no se vuelve a pedir;
- el texto de cada documento se guarda con la huella SHA-256 del archivo y los parámetros
  de limpieza, y solo se vuelve a extraer si alguno de los dos cambia.

Los fragmentos y la metadata se reescriben enteros en cada corrida, deduplicados por la
huella de su contenido: volver a correr no duplica ni papers ni fragmentos.
"""

from __future__ import annotations

import logging
import shutil
import time
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from appkit.download import DownloadError, sha256_of

from .arxiv import ArxivRecord, harvest, in_scope, oai_set, pdf_url, select_papers
from .chunking import chunk_pages
from .course import (
    COURSE_SOURCE,
    MARKDOWN_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    CourseDocument,
    describe,
    is_supported,
    markdown_sections,
)
from .errors import CorpusError, TextlessPdfError, UnreadablePdfError
from .extraction import extract_pages
from .fetch import PoliteClient
from .files import (
    ENCODING,
    read_json,
    sha256_of_text,
    write_bytes_atomically,
    write_json,
    write_jsonl,
)
from .licenses import is_redistributable, license_family
from .records import ChunkRecord, Failure, Paper
from .settings import CleaningSettings, CorpusSettings
from .stats import length_distribution

logger = logging.getLogger(__name__)

ARXIV_SOURCE = "arxiv"
ABSTRACT_URL_TEMPLATE = "https://arxiv.org/abs/{arxiv_id}"
PDF_SIGNATURE = b"%PDF-"
PROGRESS_EVERY = 50
DOWNLOAD_STAGE = "descarga"
EXTRACTION_STAGE = "extracción"
DUPLICATE_STAGE = "duplicado"
UNREADABLE_KIND = "unreadable"
UNREADABLE_REASON = "PDF dañado"
TEXTLESS_KIND = "textless"
TEXTLESS_REASON = "PDF sin texto (escaneado)"
CHUNK_ID_SEPARATOR = "#"
# Se guarda con el texto de cada documento: subirla al cambiar la extracción o la limpieza
# obliga a volver a extraer lo que quedó en `paginas/` con la versión anterior.
EXTRACTION_VERSION = 1

# Conteos que el manifiesto trae siempre, aunque valgan cero.
REPORTED_COUNTS = (
    "already_on_disk",
    "course_materials",
    "download_failed",
    "downloaded",
    "duplicate_chunks",
    "duplicate_papers",
    "extracted_from_cache",
    "extracted_now",
    "forced_splits",
    "harvested",
    "in_scope",
    "redistributable",
    "selected",
    UNREADABLE_KIND,
    TEXTLESS_KIND,
)

Result = TypeVar("Result")


@dataclass(frozen=True, slots=True)
class CorpusPaths:
    """Dónde deja cada cosa el corpus, dentro de `root`."""

    root: Path

    @property
    def pdf_directory(self) -> Path:
        return self.root / "pdf"

    @property
    def course_directory(self) -> Path:
        return self.root / "curso"

    @property
    def pages_directory(self) -> Path:
        return self.root / "paginas"

    @property
    def harvest_file(self) -> Path:
        return self.root / "cosecha.json"

    @property
    def papers_file(self) -> Path:
        return self.root / "papers.jsonl"

    @property
    def chunks_file(self) -> Path:
        return self.root / "fragmentos.jsonl"

    @property
    def failures_file(self) -> Path:
        return self.root / "fallas.jsonl"

    @property
    def manifest_file(self) -> Path:
        return self.root / "manifest.json"


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """Un archivo del corpus listo para extraer, con la metadata que irá a `papers.jsonl`."""

    paper_id: str
    file_stem: str
    path: Path
    media_type: str
    source: str
    title: str
    authors: tuple[str, ...]
    year: int | None
    abstract: str = ""
    primary_category: str = COURSE_SOURCE
    categories: tuple[str, ...] = ()
    license: str | None = None
    url: str | None = None


@dataclass(slots=True)
class BuildReport:
    """Conteos y tiempos de una corrida: lo que termina en `manifest.json`."""

    counts: Counter[str] = field(default_factory=Counter)
    licenses: Counter[str] = field(default_factory=Counter)
    durations: dict[str, float] = field(default_factory=dict)
    failures: list[Failure] = field(default_factory=list)

    def fail(self, paper_id: str, stage: str, reason: str) -> None:
        logger.warning("%s (%s): %s", paper_id, stage, reason)
        self.failures.append(Failure(paper_id, stage, reason))


def build_corpus(
    paths: CorpusPaths,
    settings: CorpusSettings,
    materials: Sequence[Path],
    harvest_client: PoliteClient,
    pdf_client: PoliteClient,
) -> dict[str, object]:
    """Construye (o completa) el corpus en `paths.root` y devuelve su manifiesto.

    Raises:
        HarvestError, DownloadError: si la cosecha de arXiv no se puede completar.
        CorpusError: si un material del curso no es PDF ni Markdown.
    """
    report = BuildReport()
    started = time.perf_counter()
    records = _timed(report, "cosecha", lambda: _load_or_harvest(paths, settings, harvest_client))
    selected = _select(records, settings, report)
    documents = _timed(
        report, "descarga", lambda: _download_pdfs(selected, paths, settings, pdf_client, report)
    )
    documents += _copy_materials(materials, paths)
    report.counts["course_materials"] = len(materials)
    pages = _timed(report, "extracción", lambda: _extract_all(documents, paths, settings, report))
    papers, chunks = _timed(
        report, "fragmentos", lambda: _chunk_all(documents, pages, paths, settings, report)
    )
    write_jsonl(paths.papers_file, (paper.to_json() for paper in papers))
    write_jsonl(paths.chunks_file, (chunk.to_json() for chunk in chunks))
    write_jsonl(paths.failures_file, (failure.to_json() for failure in report.failures))
    report.durations["total"] = round(time.perf_counter() - started, 1)
    manifest = _manifest(paths, settings, papers, chunks, report)
    write_json(paths.manifest_file, manifest)
    return manifest


def harvest_query(settings: CorpusSettings) -> dict[str, object]:
    """La consulta que identifica una cosecha: si cambia, la cosecha guardada no sirve."""
    harvest_settings = settings.harvest
    return {
        "endpoint": harvest_settings.endpoint,
        "metadata_prefix": harvest_settings.metadata_prefix,
        "sets": [oai_set(category) for category in harvest_settings.categories],
        "from": harvest_settings.from_date,
    }


def extract_document(path: Path, media_type: str, settings: CleaningSettings) -> list[str]:
    """El texto limpio de cada página (o sección, en Markdown) de un documento.

    Raises:
        UnreadablePdfError, TextlessPdfError: ver `extraction.extract_pages`.
    """
    if media_type == MARKDOWN_MEDIA_TYPE:
        return markdown_sections(path.read_text(encoding=ENCODING))
    return extract_pages(path, settings)


def _timed(report: BuildReport, step: str, action: Callable[[], Result]) -> Result:
    started = time.perf_counter()
    result = action()
    report.durations[step] = round(time.perf_counter() - started, 1)
    return result


def _load_or_harvest(
    paths: CorpusPaths, settings: CorpusSettings, client: PoliteClient
) -> list[ArxivRecord]:
    query = harvest_query(settings)
    if paths.harvest_file.exists():
        cached = read_json(paths.harvest_file)
        if isinstance(cached, dict) and cached.get("query") == query:
            logger.info("cosecha guardada de %s", cached.get("harvested_at"))
            return [ArxivRecord.from_json(row) for row in cached["records"]]
    records = harvest(client, settings.harvest)
    write_json(
        paths.harvest_file,
        {
            "query": query,
            "harvested_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "records": [record.to_json() for record in records],
        },
    )
    return records


def _select(
    records: Sequence[ArxivRecord], settings: CorpusSettings, report: BuildReport
) -> list[ArxivRecord]:
    categories = settings.harvest.categories
    scoped = in_scope(records, categories)
    report.counts["harvested"] = len(records)
    report.counts["in_scope"] = len(scoped)
    report.licenses.update(license_family(record.license) for record in scoped)
    report.counts["redistributable"] = sum(is_redistributable(record.license) for record in scoped)
    selected = select_papers(scoped, categories, settings.harvest.paper_limit)
    report.counts["selected"] = len(selected)
    for category, count in Counter(record.primary_category for record in selected).items():
        report.counts[f"selected_{category}"] = count
    return selected


def _download_pdfs(
    records: Sequence[ArxivRecord],
    paths: CorpusPaths,
    settings: CorpusSettings,
    client: PoliteClient,
    report: BuildReport,
) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for position, record in enumerate(records, start=1):
        target = paths.pdf_directory / f"{record.file_stem}.pdf"
        if target.exists():
            report.counts["already_on_disk"] += 1
        elif not _download(record, target, settings, client, report):
            continue
        documents.append(_arxiv_document(record, target))
        if position % PROGRESS_EVERY == 0:
            logger.info("PDF %d de %d", position, len(records))
    return documents


def _download(
    record: ArxivRecord,
    target: Path,
    settings: CorpusSettings,
    client: PoliteClient,
    report: BuildReport,
) -> bool:
    try:
        content = client.get(pdf_url(settings.harvest, record))
    except DownloadError as error:
        report.counts["download_failed"] += 1
        report.fail(record.arxiv_id, DOWNLOAD_STAGE, str(error))
        return False
    if not content.startswith(PDF_SIGNATURE):
        report.counts["download_failed"] += 1
        report.fail(record.arxiv_id, DOWNLOAD_STAGE, "la respuesta no es un PDF")
        return False
    write_bytes_atomically(target, content)
    report.counts["downloaded"] += 1
    return True


def _arxiv_document(record: ArxivRecord, path: Path) -> SourceDocument:
    return SourceDocument(
        paper_id=record.arxiv_id,
        file_stem=record.file_stem,
        path=path,
        media_type=PDF_MEDIA_TYPE,
        source=ARXIV_SOURCE,
        title=record.title,
        authors=record.authors,
        year=record.year,
        abstract=record.abstract,
        primary_category=record.primary_category,
        categories=record.categories,
        license=record.license,
        url=ABSTRACT_URL_TEMPLATE.format(arxiv_id=record.arxiv_id),
    )


def _copy_materials(materials: Sequence[Path], paths: CorpusPaths) -> list[SourceDocument]:
    documents = []
    for material in materials:
        description = _describe_material(material)
        target = paths.course_directory / material.name
        if not target.exists() or sha256_of(target) != sha256_of(material):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(material, target)
        documents.append(_course_document(description, target))
    return documents


def _describe_material(path: Path) -> CourseDocument:
    if not path.is_file():
        raise CorpusError(f"el material del curso {path} no existe")
    if not is_supported(path):
        raise CorpusError(f"el material del curso {path} no es PDF ni Markdown")
    return describe(path)


def _course_document(description: CourseDocument, path: Path) -> SourceDocument:
    return SourceDocument(
        paper_id=description.paper_id,
        file_stem=description.paper_id.replace(":", "-"),
        path=path,
        media_type=description.media_type,
        source=COURSE_SOURCE,
        title=description.title,
        authors=description.authors,
        year=description.year,
    )


def _extract_all(
    documents: Sequence[SourceDocument],
    paths: CorpusPaths,
    settings: CorpusSettings,
    report: BuildReport,
) -> dict[str, Extracted]:
    fingerprints = {document.paper_id: sha256_of(document.path) for document in documents}
    cleaning = {"version": EXTRACTION_VERSION, **asdict(settings.cleaning)}
    outcomes: dict[str, dict[str, object]] = {}
    pending = []
    for document in documents:
        cached = _cached_outcome(paths, document, fingerprints[document.paper_id], cleaning)
        if cached is None:
            pending.append(document)
        else:
            outcomes[document.paper_id] = cached
    report.counts["extracted_from_cache"] = len(outcomes)
    report.counts["extracted_now"] = len(pending)
    with ProcessPoolExecutor(max_workers=settings.extraction_workers) as pool:
        futures = {
            document.paper_id: pool.submit(
                extract_document, document.path, document.media_type, settings.cleaning
            )
            for document in pending
        }
        for position, document in enumerate(pending, start=1):
            outcome = _outcome(futures[document.paper_id].result)
            outcome.update(sha256=fingerprints[document.paper_id], cleaning=cleaning)
            write_json(_pages_file(paths, document), outcome)
            outcomes[document.paper_id] = outcome
            if position % PROGRESS_EVERY == 0:
                logger.info("extraídos %d de %d", position, len(pending))
    return _usable(documents, outcomes, report)


@dataclass(frozen=True, slots=True)
class Extracted:
    """El texto de un documento que sí se pudo extraer y la huella de su archivo."""

    sha256: str
    pages: list[str]


def _outcome(result: Callable[[], list[str]]) -> dict[str, object]:
    try:
        return {"pages": result()}
    except UnreadablePdfError as error:
        return {"failure": f"{UNREADABLE_REASON}: {error}", "kind": UNREADABLE_KIND}
    except TextlessPdfError as error:
        return {"failure": f"{TEXTLESS_REASON}: {error}", "kind": TEXTLESS_KIND}


def _cached_outcome(
    paths: CorpusPaths, document: SourceDocument, fingerprint: str, cleaning: dict[str, object]
) -> dict[str, object] | None:
    cache = _pages_file(paths, document)
    if not cache.exists():
        return None
    outcome = read_json(cache)
    if not isinstance(outcome, dict):
        return None
    if outcome.get("sha256") != fingerprint or outcome.get("cleaning") != cleaning:
        return None
    return outcome


def _pages_file(paths: CorpusPaths, document: SourceDocument) -> Path:
    return paths.pages_directory / f"{document.file_stem}.json"


def _usable(
    documents: Sequence[SourceDocument],
    outcomes: dict[str, dict[str, object]],
    report: BuildReport,
) -> dict[str, Extracted]:
    usable: dict[str, Extracted] = {}
    for document in documents:
        outcome = outcomes[document.paper_id]
        page_texts = outcome.get("pages")
        if not isinstance(page_texts, list):
            report.counts[str(outcome.get("kind"))] += 1
            report.fail(document.paper_id, EXTRACTION_STAGE, str(outcome.get("failure")))
            continue
        usable[document.paper_id] = Extracted(
            str(outcome["sha256"]), [str(text) for text in page_texts]
        )
    return usable


def _chunk_all(
    documents: Sequence[SourceDocument],
    extracted: dict[str, Extracted],
    paths: CorpusPaths,
    settings: CorpusSettings,
    report: BuildReport,
) -> tuple[list[Paper], list[ChunkRecord]]:
    papers: list[Paper] = []
    chunks: list[ChunkRecord] = []
    seen_files: dict[str, str] = {}
    seen_chunks: set[str] = set()
    for document in documents:
        content = extracted.get(document.paper_id)
        if content is None:
            continue
        if content.sha256 in seen_files:
            report.counts["duplicate_papers"] += 1
            original = seen_files[content.sha256]
            report.fail(document.paper_id, DUPLICATE_STAGE, f"mismo archivo que {original}")
            continue
        seen_files[content.sha256] = document.paper_id
        own = _document_chunks(document, content.pages, settings, seen_chunks, report)
        chunks.extend(own)
        papers.append(_paper(document, content, paths, own))
    return papers, chunks


def _document_chunks(
    document: SourceDocument,
    pages: Sequence[str],
    settings: CorpusSettings,
    seen_chunks: set[str],
    report: BuildReport,
) -> list[ChunkRecord]:
    records: list[ChunkRecord] = []
    for chunk in chunk_pages(pages, settings.chunks):
        fingerprint = sha256_of_text(chunk.text)
        report.counts["forced_splits"] += chunk.forced_splits
        if fingerprint in seen_chunks:
            report.counts["duplicate_chunks"] += 1
            continue
        seen_chunks.add(fingerprint)
        ordinal = len(records)
        records.append(
            ChunkRecord(
                chunk_id=f"{document.paper_id}{CHUNK_ID_SEPARATOR}{ordinal}",
                paper_id=document.paper_id,
                ordinal=ordinal,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                words=chunk.words,
                text=chunk.text,
                sha256=fingerprint,
            )
        )
    return records


def _paper(
    document: SourceDocument, content: Extracted, paths: CorpusPaths, chunks: Sequence[ChunkRecord]
) -> Paper:
    return Paper(
        paper_id=document.paper_id,
        source=document.source,
        title=document.title,
        authors=document.authors,
        year=document.year,
        abstract=document.abstract,
        primary_category=document.primary_category,
        categories=document.categories,
        license=document.license,
        url=document.url,
        file_path=document.path.relative_to(paths.root).as_posix(),
        media_type=document.media_type,
        sha256=content.sha256,
        bytes=document.path.stat().st_size,
        pages=len(content.pages),
        empty_pages=sum(not page for page in content.pages),
        words=sum(len(page.split()) for page in content.pages),
        chunks=len(chunks),
    )


def _manifest(
    paths: CorpusPaths,
    settings: CorpusSettings,
    papers: Sequence[Paper],
    chunks: Sequence[ChunkRecord],
    report: BuildReport,
) -> dict[str, object]:
    counts = dict.fromkeys(REPORTED_COUNTS, 0) | dict(sorted(report.counts.items()))
    counts.update(
        papers=len(papers),
        papers_arxiv=sum(paper.source == ARXIV_SOURCE for paper in papers),
        pages=sum(paper.pages for paper in papers),
        empty_pages=sum(paper.empty_pages for paper in papers),
        words=sum(paper.words for paper in papers),
        chunks=len(chunks),
        failures=len(report.failures),
    )
    return {
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "query": {
            **harvest_query(settings),
            "categories": list(settings.harvest.categories),
            "paper_limit": settings.harvest.paper_limit,
            "pdf_url_template": settings.harvest.pdf_url_template,
        },
        "parameters": {
            "chunks": asdict(settings.chunks),
            "cleaning": asdict(settings.cleaning),
            "request_delay_seconds": settings.harvest.request_delay_seconds,
            "pdf_delay_seconds": settings.harvest.pdf_delay_seconds,
        },
        "counts": counts,
        "licenses_in_scope": dict(report.licenses.most_common()),
        "bytes": {
            "documents": sum(paper.bytes for paper in papers),
            "papers_jsonl": paths.papers_file.stat().st_size,
            "chunks_jsonl": paths.chunks_file.stat().st_size,
        },
        "chunk_words": length_distribution(
            [chunk.words for chunk in chunks], settings.chunks.max_words
        ),
        "chunks_per_paper": length_distribution([paper.chunks for paper in papers], max_words=0),
        "durations_seconds": report.durations,
    }
