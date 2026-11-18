from __future__ import annotations

from pathlib import Path

import pytest
from rag_pdf_factory import pdf_bytes

from rag.errors import TextlessPdfError, UnreadablePdfError
from rag.extraction import extract_pages, read_raw_pages
from rag.settings import CleaningSettings

SETTINGS = CleaningSettings(min_words_per_page=2)


def write(tmp_path: Path, content: bytes, name: str = "paper.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_text_comes_out_per_page_cleaned(tmp_path: Path) -> None:
    pdf = pdf_bytes([["Database (systems) are", "use-", "ful for everyone."], ["Page two."]])
    pages = extract_pages(write(tmp_path, pdf), SETTINGS)
    assert pages == ["Database (systems) are useful for everyone.", "Page two."]


def test_a_page_without_text_is_kept_empty(tmp_path: Path) -> None:
    pdf = pdf_bytes([["Words on the first page here."], [], ["And on the third page too."]])
    pages = extract_pages(write(tmp_path, pdf), SETTINGS)
    assert pages == ["Words on the first page here.", "", "And on the third page too."]


def test_a_scanned_pdf_without_text_is_rejected(tmp_path: Path) -> None:
    pdf = pdf_bytes([[], [], ["1"]])
    assert read_raw_pages(write(tmp_path, pdf))[0] == ""
    with pytest.raises(TextlessPdfError):
        extract_pages(write(tmp_path, pdf), SETTINGS)


@pytest.mark.parametrize(
    "content",
    [b"", b"<html>not a pdf</html>", b"%PDF-1.4\n1 0 obj << >>", pdf_bytes([["x"]])[:120]],
)
def test_a_broken_pdf_is_reported_as_unreadable(tmp_path: Path, content: bytes) -> None:
    with pytest.raises(UnreadablePdfError):
        extract_pages(write(tmp_path, content), SETTINGS)
