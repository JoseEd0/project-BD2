"""Una pregunta llega a `MATCH` como texto libre, nunca como expresión booleana."""

from __future__ import annotations

from rag.search import plain_text_query


def test_operators_and_parentheses_lose_their_meaning():
    assert plain_text_query("Is NOT (B-tree) AND hash OR R-tree?") == (
        "is not  b-tree  and hash or r-tree?"
    )


def test_a_plain_question_only_changes_case():
    assert plain_text_query("How does BM25 work") == "how does bm25 work"
