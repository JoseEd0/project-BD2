"""Qué mensaje es charla, cuánto de la conversación se reenvía y qué se busca en un
seguimiento."""

from __future__ import annotations

import pytest

from rag.conversation import Exchange, bounded_history, is_small_talk, retrieval_query


@pytest.mark.parametrize(
    "message",
    ["hola", "¡Hola!", "Buenas tardes", "hola, ¿qué puedes hacer?", "What can you do?", "gracias"],
)
def test_greetings_and_questions_about_the_app_are_small_talk(message: str):
    assert is_small_talk(message)


@pytest.mark.parametrize(
    "message",
    [
        "hola, ¿qué es BM25?",
        "¿Qué es un índice aprendido?",
        "help me understand HNSW",
        "¿Cómo funciona un árbol B+?",
    ],
)
def test_a_question_about_the_corpus_is_not_small_talk(message: str):
    assert not is_small_talk(message)


def exchanges(count: int) -> list[Exchange]:
    return [Exchange(question=f"q{n}", answer=f"a{n} [1][2]") for n in range(count)]


def test_the_history_keeps_the_last_turns_without_their_citations():
    bounded = bounded_history(exchanges(5), turns=2, characters=100)
    assert bounded == [Exchange(question="q3", answer="a3"), Exchange(question="q4", answer="a4")]


def test_a_long_answer_is_cut_and_an_unanswered_turn_is_dropped():
    history = [Exchange(question="q", answer="x" * 50), Exchange(question="failed", answer="")]
    assert bounded_history(history, turns=6, characters=10) == [
        Exchange(question="q", answer="x" * 10)
    ]


def test_no_turns_means_no_history():
    assert bounded_history(exchanges(3), turns=0, characters=100) == []


def test_a_short_follow_up_is_searched_with_the_previous_question():
    history = [Exchange(question="How do learned indexes replace B-trees?", answer="...")]
    assert retrieval_query("¿y cuál es más rápido?", history, follow_up_words=8) == (
        "How do learned indexes replace B-trees? ¿y cuál es más rápido?"
    )


def test_a_full_question_or_a_first_question_is_searched_alone():
    history = [Exchange(question="previous", answer="...")]
    question = "How does BM25 saturate the term frequency of long documents?"
    assert retrieval_query(question, history, follow_up_words=8) == question
    assert retrieval_query("¿y eso?", [], follow_up_words=8) == "¿y eso?"
