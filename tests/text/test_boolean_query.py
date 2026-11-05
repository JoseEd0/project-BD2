"""El parser de las consultas booleanas de un `MATCH`: precedencia, paréntesis, OR implícito,
operandos que el analizador convierte en varios términos o en ninguno, y los errores."""

import pytest

from config import EngineConfig
from errors import DomainError
from text import (
    Analyzer,
    BooleanQuery,
    BooleanQueryError,
    Conjunction,
    Disjunction,
    Term,
    is_boolean_query,
    parse_boolean_query,
)

ROJO, AZUL, CABLE, USB, FUNDA = Term("roj"), Term("azul"), Term("cabl"), Term("usb"), Term("fund")


@pytest.fixture(scope="module")
def analyzer() -> Analyzer:
    return Analyzer(EngineConfig())


def parsed(query: str, analyzer: Analyzer) -> BooleanQuery:
    return parse_boolean_query(query, analyzer)


@pytest.mark.parametrize(
    ("query", "boolean"),
    [
        ("cable usb", False),
        ("cable and usb or not rojo", False),
        ("ANDROID", False),
        ("", False),
        ("cable AND usb", True),
        ("cable OR usb", True),
        ("NOT cable", True),
        ("cable (usb)", True),
        ("cable)", True),
    ],
)
def test_only_uppercase_operators_and_parentheses_make_a_query_boolean(
    query: str, boolean: bool
):
    assert is_boolean_query(query) is boolean


def test_and_binds_tighter_than_or(analyzer: Analyzer):
    assert parsed("rojo OR azul AND cable", analyzer).expression == Disjunction(
        (ROJO, Conjunction((AZUL, CABLE), ()))
    )
    assert parsed("rojo AND azul OR cable", analyzer).expression == Disjunction(
        (Conjunction((ROJO, AZUL), ()), CABLE)
    )


def test_not_binds_tighter_than_and(analyzer: Analyzer):
    assert parsed("NOT rojo AND azul", analyzer).expression == Conjunction((AZUL,), (ROJO,))


def test_parentheses_group_first(analyzer: Analyzer):
    assert parsed("(rojo OR azul) AND cable", analyzer).expression == Conjunction(
        (Disjunction((ROJO, AZUL)), CABLE), ()
    )
    assert parsed("((cable))", analyzer).expression == CABLE


def test_adjacent_operands_are_joined_with_or_at_its_precedence(analyzer: Analyzer):
    explicit = parsed("rojo OR azul AND cable OR (usb)", analyzer)
    assert parsed("rojo azul AND cable (usb)", analyzer) == explicit


def test_nested_ands_and_ors_become_one_n_ary_node(analyzer: Analyzer):
    assert parsed("rojo AND (azul AND (cable AND usb))", analyzer).expression == Conjunction(
        (ROJO, AZUL, CABLE, USB), ()
    )
    assert parsed("(rojo OR azul) OR (cable OR usb)", analyzer).expression == Disjunction(
        (ROJO, AZUL, CABLE, USB)
    )


def test_the_example_of_the_slides(analyzer: Analyzer):
    query = parsed("Brutus AND Caesar AND NOT Calpurnia", analyzer)
    assert query.describe() == "brutus AND caes AND NOT calpurni"
    assert dict(query.positive_terms) == {"brutus": 1, "caes": 1}


def test_a_not_inside_an_and_is_a_difference_wherever_it_is_written(analyzer: Analyzer):
    expected = Conjunction((FUNDA, CABLE), (ROJO, AZUL))
    assert parsed("NOT rojo AND funda AND NOT azul AND cable", analyzer).expression == expected
    assert parsed("funda AND cable AND NOT (rojo OR azul)", analyzer).describe() == (
        "fund AND cabl AND NOT (roj OR azul)"
    )


def test_double_negations_cancel(analyzer: Analyzer):
    assert parsed("NOT NOT cable", analyzer).expression == CABLE
    assert parsed("cable AND NOT (rojo AND NOT usb)", analyzer).describe() == (
        "cabl AND NOT (roj AND NOT usb)"
    )


def test_an_or_with_a_negated_operand_inside_an_and_is_still_a_difference(analyzer: Analyzer):
    query = parsed("(cable OR NOT usb) AND rojo", analyzer)
    assert query.expression == Conjunction((ROJO,), (Conjunction((USB,), (CABLE,)),))


def test_an_operand_with_several_terms_is_the_and_of_them(analyzer: Analyzer):
    assert parsed("iphone-12 AND funda", analyzer).expression == Conjunction(
        (Term("iphon"), Term("12"), FUNDA), ()
    )
    assert parsed("funda AND NOT iphone-12", analyzer).describe() == (
        "fund AND NOT (iphon AND 12)"
    )


@pytest.mark.parametrize(
    "query",
    [
        "cable AND el",
        "el AND cable",
        "cable OR el",
        "cable AND NOT el",
        "(el) cable",
        "el OR (de AND cable)",
    ],
)
def test_a_stopword_operand_is_dropped_with_its_operator(query: str, analyzer: Analyzer):
    assert parsed(query, analyzer).expression == CABLE


@pytest.mark.parametrize("query", ["el AND de", "NOT el", "(la OR de) AND NOT el", "(el)"])
def test_a_query_of_stopwords_is_empty(query: str, analyzer: Analyzer):
    empty = parsed(query, analyzer)
    assert empty.expression is None
    assert dict(empty.positive_terms) == {}
    assert empty.describe() == ""


def test_positive_terms_are_those_under_an_even_number_of_nots(analyzer: Analyzer):
    query = parsed("rojo AND NOT (usb AND NOT cable) AND NOT azul", analyzer)
    assert dict(query.positive_terms) == {"roj": 1, "cabl": 1}


def test_a_repeated_positive_term_counts_as_often_as_it_appears(analyzer: Analyzer):
    assert dict(parsed("cable OR (cable usb) OR cable-cable", analyzer).positive_terms) == {
        "cabl": 4,
        "usb": 1,
    }


def test_an_or_only_query_has_the_terms_of_the_free_text_query(analyzer: Analyzer):
    query = "Funda (iPhone cable) OR de usb"
    free_text = query.replace("(", " ").replace(")", " ").replace(" OR ", " ")
    assert parsed(query, analyzer).positive_terms == analyzer.analyze(free_text).frequencies


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("(cable AND usb", "falta cerrar el paréntesis de la posición 1"),
        ("cable (", "falta cerrar el paréntesis de la posición 7"),
        ("cable) AND usb", "el paréntesis de la posición 6 cierra sin haberse abierto"),
        (")", "el paréntesis de la posición 1 cierra sin haberse abierto"),
        ("cable ()", "los paréntesis de la posición 7 están vacíos"),
        ("AND cable", "AND de la posición 1 no tiene operando a la izquierda"),
        ("(OR cable)", "OR de la posición 2 no tiene operando a la izquierda"),
        ("cable AND", "AND de la posición 7 no tiene operando a la derecha"),
        ("cable OR", "OR de la posición 7 no tiene operando a la derecha"),
        ("cable AND OR usb", "AND de la posición 7 no tiene operando a la derecha"),
        ("cable AND NOT", "NOT de la posición 11 no tiene operando a la derecha"),
        ("NOT cable", "solo excluye documentos"),
        ("NOT cable AND NOT usb", "solo excluye documentos"),
        ("cable OR NOT usb", "solo excluye documentos"),
        ("cable NOT usb", "solo excluye documentos"),
        ("el AND NOT cable", "solo excluye documentos"),
    ],
)
def test_a_malformed_query_says_what_is_wrong(query: str, message: str, analyzer: Analyzer):
    with pytest.raises(BooleanQueryError, match=message):
        parsed(query, analyzer)


def test_a_malformed_query_is_an_error_of_the_user():
    assert issubclass(BooleanQueryError, DomainError)
