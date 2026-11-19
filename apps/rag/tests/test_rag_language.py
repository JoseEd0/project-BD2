"""El idioma de una pregunta y la pregunta que recibe BM25."""

from __future__ import annotations

import pytest

from rag.language import Language, detect_language, without_stopwords
from rag.search import lexical_query


@pytest.mark.parametrize(
    ("question", "language"),
    [
        ("¿Cómo elige SHARP qué coincidencias parciales conservar?", Language.SPANISH),
        ("búsqueda aproximada de vecinos más cercanos en bases vectoriales", Language.SPANISH),
        ("How does SHARP choose which partial matches to keep?", Language.ENGLISH),
        ("text-to-SQL with large language models", Language.ENGLISH),
        ("BM25 term frequency", Language.UNKNOWN),
        ("B+ tree range query", Language.UNKNOWN),
        ("", Language.UNKNOWN),
    ],
)
def test_the_language_comes_from_stopwords_and_spanish_marks(question: str, language: Language):
    assert detect_language(question) is language


def test_words_shared_by_both_languages_decide_nothing():
    assert detect_language("a no me he") is Language.UNKNOWN


def test_spanish_stopwords_leave_the_terms_shared_with_english_papers():
    question = "¿Cómo infiere PG-HIVE el tipo de dato de las propiedades?"
    assert without_stopwords(question, Language.SPANISH) == "infiere PG-HIVE tipo dato propiedades?"


def test_other_languages_are_left_alone():
    question = "How does the B+ tree work?"
    assert without_stopwords(question, Language.ENGLISH) == question
    assert without_stopwords(question, Language.UNKNOWN) == question


def test_a_question_made_only_of_stopwords_is_kept():
    assert without_stopwords("¿qué es?", Language.SPANISH) == "¿qué es?"


def test_bm25_receives_the_content_words_as_free_text():
    assert lexical_query("¿Qué es el árbol (B+) de NOT?") == "árbol  b+  not?"
    assert lexical_query("Is NOT (B-tree) AND hash?") == "is not  b-tree  and hash?"
