from __future__ import annotations

from pathlib import Path

from rag_pdf_factory import pdf_bytes

from rag.course import MARKDOWN_MEDIA_TYPE, PDF_MEDIA_TYPE, describe, markdown_sections, slug

MARKDOWN = """Preamble line.

# Course **project**

Build a [database](https://example.org) from `scratch`.

## Part 1

| Column | Meaning |
|---|---|
| id | key |

- first item
- second item
"""


def test_markdown_sections_are_its_pages_without_markup() -> None:
    assert markdown_sections(MARKDOWN) == [
        "Preamble line.",
        "Course project Build a database from scratch.",
        "Part 1 Column Meaning id key first item second item",
    ]


def test_slugs_are_ascii_and_lowercase() -> None:
    assert slug("Proyecto_Integrador_BD2") == "proyecto-integrador-bd2"
    assert slug("Índice Ñandú") == "indice-nandu"


def test_a_markdown_document_is_described_by_its_first_heading(tmp_path: Path) -> None:
    path = tmp_path / "Enunciado.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    document = describe(path)
    assert document.paper_id == "curso:enunciado"
    assert document.title == "Course **project**"
    assert document.media_type == MARKDOWN_MEDIA_TYPE


def test_a_pdf_without_metadata_takes_its_first_line_as_title(tmp_path: Path) -> None:
    path = tmp_path / "informe.pdf"
    path.write_bytes(pdf_bytes([["Informe del proyecto", "Texto."]]))
    document = describe(path)
    assert document.title == "Informe del proyecto"
    assert document.media_type == PDF_MEDIA_TYPE
    assert document.year is None
