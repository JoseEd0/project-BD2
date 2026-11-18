from __future__ import annotations

import json
import urllib.error
from collections import Counter
from pathlib import Path

import pytest
from rag_fakes import ARXIV_DEFAULT, client_serving, page_xml, record_xml
from rag_pdf_factory import pdf_bytes

from rag.errors import CorpusError
from rag.fetch import PoliteClient
from rag.pipeline import CorpusPaths, build_corpus
from rag.settings import CorpusSettings, HarvestSettings, HttpSettings

PDF_URL = "https://pdf.test/{arxiv_id}"
LINES_PER_PAGE = 30


def page_lines(paper: str, page: int) -> list[str]:
    return [
        f"In {paper} page {page} the statement {line} explains how indexes help queries."
        for line in range(LINES_PER_PAGE)
    ]


def paper_pdf(paper: str, pages: int = 3) -> bytes:
    return pdf_bytes([page_lines(paper, page) for page in range(1, pages + 1)])


SHARED = paper_pdf("shared")
SERVED = {
    "2501.00001": SHARED,
    "2501.00002": paper_pdf("two"),
    "2501.00004": b"<html>captcha</html>",
    "2501.00005": b"%PDF-1.4\n1 0 obj << >>",
    "2501.00006": pdf_bytes([[], [], []]),
    "2501.00007": SHARED,
    "2501.00008": pdf_bytes(
        [page_lines("shared", page) for page in (1, 2, 3)] + [page_lines("extra", 4)]
    ),
}
RECORDS = (
    record_xml("2501.00001", "cs.DB", created="2025-01-09")
    + record_xml("2501.00002", "cs.IR", created="2025-01-08")
    + record_xml("2501.00003", "cs.DB", ARXIV_DEFAULT, created="2025-01-07")
    + record_xml("2501.00004", "cs.IR", created="2025-01-06")
    + record_xml("2501.00005", "cs.DB", created="2025-01-05")
    + record_xml("2501.00006", "cs.IR", created="2025-01-04")
    + record_xml("2501.00007", "cs.DB", created="2025-01-03")
    + record_xml("2501.00008", "cs.IR", created="2025-01-02")
    + record_xml("2501.00009", "cs.LG", created="2025-01-01")
)
MARKDOWN = "# Enunciado\n\n" + " ".join(
    f"La parte {number} del proyecto pide una estructura de datos." for number in range(60)
)


class Network:
    """El OAI-PMH y el servidor de PDF de mentira, con lo que se les pidió."""

    def __init__(self) -> None:
        self.harvest_requests = 0
        self.pdf_requests: Counter[str] = Counter()
        self.unreachable: set[str] = set()

    def harvest_client(self) -> PoliteClient:
        return client_serving(self._oai)

    def pdf_client(self) -> PoliteClient:
        return client_serving(self._pdf, HttpSettings(attempts=1))

    def _oai(self, url: str) -> bytes:
        self.harvest_requests += 1
        return page_xml(RECORDS if "cs%3Acs%3ADB" in url else "")

    def _pdf(self, url: str) -> bytes:
        arxiv_id = url.rsplit("/", 1)[-1]
        self.pdf_requests[arxiv_id] += 1
        if arxiv_id in self.unreachable:
            raise urllib.error.URLError("sin red")
        return SERVED[arxiv_id]


@pytest.fixture
def settings() -> CorpusSettings:
    return CorpusSettings(
        harvest=HarvestSettings(
            endpoint="https://oai.test/oai", paper_limit=20, pdf_url_template=PDF_URL
        ),
        extraction_workers=1,
    )


@pytest.fixture
def material(tmp_path: Path) -> Path:
    path = tmp_path / "Enunciado.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    return path


def build(
    paths: CorpusPaths, settings: CorpusSettings, network: Network, material: Path
) -> dict[str, object]:
    return build_corpus(paths, settings, [material], network.harvest_client(), network.pdf_client())


def rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def counts(manifest: dict[str, object]) -> dict[str, int]:
    value = manifest["counts"]
    assert isinstance(value, dict)
    return value


def test_the_corpus_keeps_licensed_readable_unique_papers(
    tmp_path: Path, settings: CorpusSettings, material: Path
) -> None:
    paths = CorpusPaths(tmp_path / "corpus")
    manifest = build(paths, settings, Network(), material)
    papers = {str(row["paper_id"]): row for row in rows(paths.papers_file)}
    assert set(papers) == {"2501.00001", "2501.00002", "2501.00008", "curso:enunciado"}
    failures = {str(row["paper_id"]): str(row["stage"]) for row in rows(paths.failures_file)}
    assert failures == {
        "2501.00004": "descarga",
        "2501.00005": "extracción",
        "2501.00006": "extracción",
        "2501.00007": "duplicado",
    }
    numbers = counts(manifest)
    assert numbers["harvested"] == 9
    assert numbers["in_scope"] == 8
    assert numbers["redistributable"] == 7
    assert numbers["downloaded"] == 6
    assert numbers["download_failed"] == 1
    assert numbers["unreadable"] == 1
    assert numbers["textless"] == 1
    assert numbers["duplicate_papers"] == 1
    assert numbers["duplicate_chunks"] > 0
    assert numbers["papers"] == 4
    hyphenation = manifest["hyphenation"]
    assert isinstance(hyphenation, dict)
    assert hyphenation["kept"] == hyphenation["joined"] == 0
    first = papers["2501.00001"]
    assert first["file_path"] == "pdf/2501.00001.pdf"
    assert first["license"] == "http://creativecommons.org/licenses/by/4.0/"
    assert first["authors"] == ["Edgar F. Codd", "Jim Gray Jr."]
    assert first["pages"] == 3
    assert papers["curso:enunciado"]["media_type"] == "text/markdown"
    assert (paths.root / "curso" / "Enunciado.md").exists()


def test_chunks_are_unique_bounded_and_point_to_their_pages(
    tmp_path: Path, settings: CorpusSettings, material: Path
) -> None:
    paths = CorpusPaths(tmp_path / "corpus")
    build(paths, settings, Network(), material)
    chunks = rows(paths.chunks_file)
    assert len({chunk["sha256"] for chunk in chunks}) == len(chunks)
    assert len({chunk["chunk_id"] for chunk in chunks}) == len(chunks)
    pages = {str(row["paper_id"]): int(str(row["pages"])) for row in rows(paths.papers_file)}
    for chunk in chunks:
        words = int(str(chunk["words"]))
        assert settings.chunks.min_words <= words <= settings.chunks.max_words
        start, end = int(str(chunk["page_start"])), int(str(chunk["page_end"]))
        assert 1 <= start <= end <= pages[str(chunk["paper_id"])]
        assert f"page {start} " in str(chunk["text"]) or chunk["paper_id"] == "curso:enunciado"
    extended = [chunk for chunk in chunks if chunk["paper_id"] == "2501.00008"]
    assert all("extra" in str(chunk["text"]) for chunk in extended)


def test_running_again_reuses_everything_and_duplicates_nothing(
    tmp_path: Path, settings: CorpusSettings, material: Path
) -> None:
    paths = CorpusPaths(tmp_path / "corpus")
    network = Network()
    build(paths, settings, network, material)
    papers, chunks = paths.papers_file.read_bytes(), paths.chunks_file.read_bytes()
    manifest = build(paths, settings, network, material)
    assert paths.papers_file.read_bytes() == papers
    assert paths.chunks_file.read_bytes() == chunks
    assert network.harvest_requests == 2
    assert network.pdf_requests["2501.00001"] == 1
    assert network.pdf_requests["2501.00004"] == 2
    numbers = counts(manifest)
    assert numbers["downloaded"] == 0
    assert numbers["already_on_disk"] == 6
    assert numbers["extracted_now"] == 0


def test_an_interrupted_run_resumes_where_it_stopped(
    tmp_path: Path, settings: CorpusSettings, material: Path
) -> None:
    paths = CorpusPaths(tmp_path / "corpus")
    network = Network()
    network.unreachable = {"2501.00002", "2501.00008"}
    first = build(paths, settings, network, material)
    assert counts(first)["download_failed"] == 3
    leftover = paths.pdf_directory / "2501.00002.pdf.parcial"
    leftover.write_bytes(b"%PDF-1.4 half")
    network.unreachable = set()
    second = build(paths, settings, network, material)
    assert counts(second)["downloaded"] == 2
    assert counts(second)["extracted_now"] == 2
    assert network.pdf_requests["2501.00001"] == 1
    assert {row["paper_id"] for row in rows(paths.papers_file)} >= {"2501.00002", "2501.00008"}


def test_a_changed_query_harvests_again(
    tmp_path: Path, settings: CorpusSettings, material: Path
) -> None:
    paths = CorpusPaths(tmp_path / "corpus")
    network = Network()
    build(paths, settings, network, material)
    later = CorpusSettings(
        harvest=HarvestSettings(
            endpoint="https://oai.test/oai", from_date="2025-06-01", pdf_url_template=PDF_URL
        ),
        extraction_workers=1,
    )
    build(paths, later, network, material)
    assert network.harvest_requests == 4


def test_a_course_material_that_is_not_a_document_is_rejected(
    tmp_path: Path, settings: CorpusSettings
) -> None:
    material = tmp_path / "notas.txt"
    material.write_text("texto", encoding="utf-8")
    with pytest.raises(CorpusError):
        build(CorpusPaths(tmp_path / "corpus"), settings, Network(), material)
