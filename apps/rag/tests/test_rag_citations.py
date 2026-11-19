"""Las citas `[n]` contra los fragmentos enviados."""

from __future__ import annotations

from rag_llm_fakes import sources

from rag.citations import check_citations, cited_numbers

SENT = sources("first", "second", "third")


def test_no_citations():
    report = check_citations("No cita nada.", SENT)
    assert (report.citations, report.invalid) == ([], [])


def test_the_three_forms_of_a_citation():
    assert cited_numbers("a [2], b [1][3] y c [1, 3] o [2,3]") == [2, 1, 3, 1, 3, 2, 3]


def test_valid_citations_in_order_of_first_appearance_with_their_counts():
    report = check_citations("B+ [2]. Hojas [1][2]. Rango [2].", SENT)
    assert [(citation.n, citation.count) for citation in report.citations] == [(2, 3), (1, 1)]
    second = report.citations[0]
    assert (second.chunk_id, second.paper_id, second.page_start, second.page_end) == (
        "2501.00002#0",
        "2501.00002",
        3,
        4,
    )
    assert report.invalid == []


def test_numbers_that_were_not_sent_are_invalid_once_each():
    report = check_citations("Inventa [4], [0] y [99]; repite [4]; acierta [3].", SENT)
    assert [citation.n for citation in report.citations] == [3]
    assert report.invalid == [4, 0, 99]


def test_brackets_without_numbers_are_not_citations():
    assert cited_numbers("[a] [] [1a] [ 2 ] x[-1]") == []
