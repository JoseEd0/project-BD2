from __future__ import annotations

from rag.cleaning import (
    clean_document,
    normalize_characters,
    page_lines,
    remove_repeated_edges,
)
from rag.settings import CleaningSettings

SETTINGS = CleaningSettings()


def test_lines_are_joined_with_single_spaces() -> None:
    assert clean_document(["first line\nsecond line"], SETTINGS).pages == ["first line second line"]
    assert page_lines("  a \t b \n\n  c  \n") == ["a b", "c"]


def test_ligatures_math_letters_soft_hyphens_and_controls_are_normalized() -> None:
    assert normalize_characters("\ufb01nd e\u00adffect") == "find effect"
    assert normalize_characters("a\x00b\nc") == "a b\nc"
    assert normalize_characters("x = \ud835\udc65") == "x = x"
    assert normalize_characters("lone \ud835 surrogate") == "lone \ufffd surrogate"


PAGE_WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]


def body(page: int) -> list[str]:
    word = PAGE_WORDS[page - 1]
    return [f"Body text about {word} only.", f"Another line on {word} and more {word}."]


def test_repeated_headers_and_page_numbers_are_removed() -> None:
    pages = [["Proceedings of VLDB 2025", *body(page), str(page)] for page in range(1, 7)]
    cleaned = remove_repeated_edges(pages, SETTINGS)
    assert cleaned == [body(page) for page in range(1, 7)]


def test_a_line_repeated_in_few_pages_is_kept() -> None:
    pages = [body(page) for page in range(1, 9)]
    pages[0] = ["Related Work", *pages[0]]
    pages[1] = ["Related Work", *pages[1]]
    assert remove_repeated_edges(pages, SETTINGS) == pages


def test_a_repeated_line_in_the_middle_of_a_page_is_not_an_edge() -> None:
    middle = ["a", "b", "c", "Repeated sentence.", "d", "e", "f"]
    pages = [list(middle) for _ in range(5)]
    cleaned = remove_repeated_edges(pages, SETTINGS)
    assert all("Repeated sentence." in lines for lines in cleaned)


def test_documents_shorter_than_the_minimum_keep_their_edges() -> None:
    pages = [["Header", "text one"], ["Header", "text two"]]
    assert remove_repeated_edges(pages, SETTINGS) == pages


def test_the_arxiv_stamp_and_headers_are_cleaned_from_whole_pages() -> None:
    raw = [
        "arXiv:2501.01234v2 [cs.DB] 3 Jan 2025\nTitle of the paper\n"
        "We study effi-\ncient joins.\n1",
        "Short Title\nSecond page text.\n2",
        "Short Title\nThird page text.\n3",
        "Short Title\nFourth page.\n4",
    ]
    assert clean_document(raw, SETTINGS).pages == [
        "Title of the paper We study efficient joins.",
        "Second page text.",
        "Third page text.",
        "Fourth page.",
    ]


def test_an_empty_page_stays_empty() -> None:
    assert clean_document(["", "  \n "], SETTINGS).pages == ["", ""]


def test_a_document_reports_its_hyphenation_decisions() -> None:
    raw = ["A data-driven exam-\nple of data-\ndriven work.", "Another exam-\nple here."]
    document = clean_document(raw, SETTINGS)
    assert document.pages == [
        "A data-driven example of data-driven work.",
        "Another example here.",
    ]
    assert document.hyphenation == {"joined_by_default": 2, "kept_compound_seen": 1}
