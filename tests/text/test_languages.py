"""El registro de idiomas y las listas de stopwords versionadas."""

import pytest

from errors import DomainError
from text import TOKEN_PATTERN, UnknownLanguageError, fold, language_named

LANGUAGES = ["es", "en"]


def test_a_language_is_found_by_its_code_ignoring_case_and_spaces():
    assert language_named(" EN ").code == "en"
    assert language_named("es").stemmer_algorithm == "spanish"


def test_an_unknown_language_is_a_domain_error_that_lists_the_known_ones():
    with pytest.raises(UnknownLanguageError, match=r"'fr'.*en, es") as raised:
        language_named("fr")
    assert isinstance(raised.value, DomainError)


@pytest.mark.parametrize("code", LANGUAGES)
def test_every_stopword_is_already_folded(code: str):
    stopwords = language_named(code).stopwords
    assert stopwords
    assert all(fold(word) == word for word in stopwords)


@pytest.mark.parametrize("code", LANGUAGES)
def test_every_stopword_is_one_token(code: str):
    """Una entrada que el tokenizador cortara en dos no coincidiría nunca con un token."""
    for word in language_named(code).stopwords:
        assert ["".join(pair) for pair in TOKEN_PATTERN.findall(word)] == [word]


def test_the_spanish_list_is_matched_without_accents():
    stopwords = language_named("es").stopwords
    assert {"mas", "el", "que", "estan", "tambien"} <= stopwords
    assert "más" not in stopwords


def test_the_comments_of_the_snowball_files_are_not_loaded():
    assert "from" not in language_named("es").stopwords
    assert "subject" not in language_named("en").stopwords


def test_english_contractions_do_not_turn_content_words_into_stopwords():
    assert {"shell", "well", "hell", "shed"}.isdisjoint(language_named("en").stopwords)
