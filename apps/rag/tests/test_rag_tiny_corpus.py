from __future__ import annotations

import json
from pathlib import Path

from apps.rag.tests.tiny_corpus import write_tiny_corpus

from rag.extraction import read_raw_pages


def test_every_paper_gets_a_pdf_with_its_pages_and_each_chunk_on_its_first_page(
    tmp_path: Path,
) -> None:
    corpus = write_tiny_corpus(tmp_path)
    papers = [json.loads(line) for line in (corpus / "papers.jsonl").read_text().splitlines()]
    chunks = [json.loads(line) for line in (corpus / "fragmentos.jsonl").read_text().splitlines()]
    pages = {paper["paper_id"]: read_raw_pages(corpus / paper["file_path"]) for paper in papers}
    assert {paper_id: len(text) for paper_id, text in pages.items()} == {
        paper["paper_id"]: paper["pages"] for paper in papers
    }
    for chunk in chunks:
        page = " ".join(pages[chunk["paper_id"]][chunk["page_start"] - 1].split())
        assert chunk["text"] in page


def test_writing_it_again_leaves_the_same_corpus(tmp_path: Path) -> None:
    first = write_tiny_corpus(tmp_path)
    contents = {path: path.read_bytes() for path in first.rglob("*") if path.is_file()}
    write_tiny_corpus(tmp_path)
    assert {path: path.read_bytes() for path in first.rglob("*") if path.is_file()} == contents
