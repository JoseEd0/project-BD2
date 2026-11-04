"""El plegado: la forma en que se comparan documentos, consultas y stopwords."""

import pytest

from text import fold


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
