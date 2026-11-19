"""El rango de claves de los fragmentos de un documento y la ruta de su archivo en el corpus."""

from __future__ import annotations

from pathlib import Path

import pytest
from rag_llm_fakes import paper

from appkit import ResourceNotFoundError
from rag.papers import PapersFilesUnavailableError, chunk_key_range, corpus_file, file_url_of


def test_the_key_range_holds_exactly_the_chunks_of_the_document():
    low, high = chunk_key_range("2505.01180")
    inside = ["2505.01180#0", "2505.01180#10", "2505.01180#402"]
    outside = ["2505.0118#0", "2505.011801#0", "2505.01181#0", "2505.01180", "2505.01180$"]
    assert all(low <= key < high for key in inside)
    assert not any(low <= key < high for key in outside)


def test_the_file_url_keeps_the_colon_and_escapes_the_rest():
    assert file_url_of("curso:informe") == "/papers/curso:informe/file"
    assert file_url_of("cs/0101001") == "/papers/cs%2F0101001/file"


def test_the_file_is_found_inside_the_corpus(tmp_path: Path):
    pdf = tmp_path / "pdf" / "2501.00001.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-")
    assert corpus_file(tmp_path, paper()) == pdf.resolve()


def test_without_a_mounted_corpus_files_are_unavailable():
    with pytest.raises(PapersFilesUnavailableError):
        corpus_file(None, paper())


def test_a_missing_file_is_not_found(tmp_path: Path):
    with pytest.raises(ResourceNotFoundError):
        corpus_file(tmp_path, paper())


def test_a_stored_path_that_leaves_the_corpus_is_not_served(tmp_path: Path):
    corpus = tmp_path / "rag_papers"
    corpus.mkdir()
    (tmp_path / "secreto.txt").write_text("no")
    escaping = paper().model_copy(update={"file_path": "../secreto.txt"})
    with pytest.raises(ResourceNotFoundError):
        corpus_file(corpus, escaping)
