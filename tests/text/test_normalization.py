"""El plegado: la forma en que se comparan documentos, consultas y stopwords."""

import unicodedata

import pytest

from text import fold
from text.normalization import (
    APOSTROPHES,
    DECOMPOSED_FORM,
    LAST_BMP_CODE_POINT,
    NONSPACING_MARK,
    PRESERVED_LETTERS,
    UNICODE_FORM,
)


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("Canción ÁRBOL pingüino", "cancion arbol pinguino"),
        ("Ça, Ỳ, ŝ", "ca, y, s"),
        ("AÑO", "año"),
        ("Ñandú", "ñandu"),
        ("an\u0303o", "año"),
        ("Straße", "strasse"),
        ("\uff21\uff22\uff23\uff11\uff12", "abc12"),
        ("ﬁltro", "filtro"),
        ("m²", "m2"),
        ("x\u0301", "x"),
        ("L'Oréal", "loreal"),
        ("Kellogg\u2019s", "kelloggs"),
    ],
)
def test_text_is_folded_to_its_comparable_form(raw: str, folded: str):
    assert fold(raw) == folded


def test_the_eñe_keeps_año_apart_from_ano():
    assert fold("año") != fold("ano")


def test_folding_twice_changes_nothing():
    sample = "Él compró 3,5 kg de AÑIL en L'Hospitalet — ¡qué ﬁno! \uff21\uff22\uff23 ç ü ǰ İ"
    assert fold(fold(sample)) == fold(sample)


def test_empty_text_folds_to_empty_text():
    assert fold("") == ""


SURROGATES = range(0xD800, 0xE000)
# Una cara, una letra matemática, un ideograma y una marca diacrítica: fuera del plano
# básico la tabla no llega, y el plegado deja todo como estaba.
ASTRAL_SAMPLE = "\U0001f600\U0001d400\U00020000\U000101fd"


def folded_character_by_definition(character: str) -> str:
    """El plegado de un carácter ya compuesto y en minúsculas, escrito desde su definición
    y no desde la tabla: el oráculo del camino rápido de `fold`."""
    if ord(character) > LAST_BMP_CODE_POINT or character in PRESERVED_LETTERS:
        return character
    if character in APOSTROPHES or unicodedata.category(character) == NONSPACING_MARK:
        return ""
    decomposed = unicodedata.normalize(DECOMPOSED_FORM, character)
    base = "".join(part for part in decomposed if unicodedata.category(part) != NONSPACING_MARK)
    return character if base == decomposed else base


def test_every_character_folds_as_its_definition_says():
    every_character = "".join(
        chr(code_point)
        for code_point in range(LAST_BMP_CODE_POINT + 1)
        if code_point not in SURROGATES
    )
    sample = every_character + ASTRAL_SAMPLE
    composed = unicodedata.normalize(UNICODE_FORM, sample).casefold()
    expected = "".join(folded_character_by_definition(character) for character in composed)
    assert fold(sample) == expected
