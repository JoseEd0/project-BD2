"""Parámetros `$n` y literales vectoriales en el front-end SQL."""

import pytest

from sql import parse, parse_script, tokenize
from sql.errors import (
    SqlLexicalError,
    SqlMissingParameterError,
    SqlParameterError,
    SqlSyntaxError,
)
from sql.nodes import (
    BinaryOperation,
    BinaryOperator,
    ColumnRef,
    FunctionCall,
    InsertStatement,
    Literal,
    SelectStatement,
    UnaryOperation,
    UpdateStatement,
)
from sql.tokens import TokenType

ANA = Literal("ana")
SEVEN = Literal(7)


def select(source: str, *parameters: Literal) -> SelectStatement:
    statement = parse(source, parameters)
    assert isinstance(statement, SelectStatement)
    return statement


def test_a_dollar_and_digits_is_a_parameter_token():
    token = tokenize("$12")[0]

    assert token.type is TokenType.PARAMETER
    assert token.text == "$12"


def test_a_dollar_inside_or_before_a_name_is_still_an_identifier():
    assert [token.type for token in tokenize("a$1 $col")][:2] == [
        TokenType.IDENTIFIER,
        TokenType.IDENTIFIER,
    ]


@pytest.mark.parametrize(
    ("source", "message"),
    [("$0", "se numeran desde \\$1"), ("$1a", "parámetro no válido '\\$1a'")],
)
def test_a_malformed_parameter_is_a_lexical_error(source: str, message: str):
    with pytest.raises(SqlLexicalError, match=message):
        tokenize(source)


def test_brackets_are_tokens():
    assert [token.type for token in tokenize("[]")][:2] == [
        TokenType.LBRACKET,
        TokenType.RBRACKET,
    ]


def test_a_parameter_takes_the_place_of_a_literal():
    statement = select("SELECT * FROM t WHERE nombre = $1", ANA)

    assert statement.where == BinaryOperation(BinaryOperator.EQUAL, ColumnRef("nombre"), ANA)


def test_the_same_parameter_can_appear_twice():
    statement = select("SELECT * FROM t WHERE a = $1 OR b = $1", ANA)

    assert isinstance(statement.where, BinaryOperation)
    assert isinstance(statement.where.left, BinaryOperation)
    assert isinstance(statement.where.right, BinaryOperation)
    assert statement.where.left.right == statement.where.right.right == ANA


def test_parameters_go_in_every_row_of_an_insert():
    statement = parse("INSERT INTO t VALUES ($1, $2), ($3, $2)", (SEVEN, ANA, Literal(8)))

    assert isinstance(statement, InsertStatement)
    assert statement.rows == ((SEVEN, ANA), (Literal(8), ANA))


def test_parameters_go_in_an_update():
    statement = parse("UPDATE t SET nombre = $1 WHERE id = $2", (ANA, SEVEN))

    assert isinstance(statement, UpdateStatement)
    assert statement.assignments[0].value == ANA


def test_parameters_go_in_function_arguments_named_or_not():
    statement = select("SELECT * FROM t WHERE SIMILAR_TO($1, k = $2)", Literal("foto.jpg"), SEVEN)

    assert statement.where == FunctionCall("SIMILAR_TO", (Literal("foto.jpg"),), {"k": SEVEN})


def test_a_parameter_can_be_negated():
    statement = select("SELECT -$1 FROM t", SEVEN)

    assert isinstance(statement.projections[0].expression, UnaryOperation)


def test_limit_and_offset_take_parameters():
    statement = select("SELECT * FROM t LIMIT $1 OFFSET $2", SEVEN, Literal(0))

    assert (statement.limit, statement.offset) == (7, 0)


@pytest.mark.parametrize("value", [Literal("7"), Literal(-1), Literal(1.5), Literal(True)])
def test_limit_rejects_a_parameter_that_is_not_a_row_count(value: Literal):
    with pytest.raises(SqlSyntaxError, match="entero no negativo") as raised:
        select("SELECT * FROM t LIMIT $1", value)

    assert raised.value.column == len("SELECT * FROM t LIMIT ") + 1


def test_a_parameter_without_value_is_pinned_to_its_position():
    with pytest.raises(SqlMissingParameterError, match="\\$2 no tiene valor") as raised:
        select("SELECT * FROM t WHERE a = $2", ANA)

    assert (raised.value.line, raised.value.column) == (1, len("SELECT * FROM t WHERE a = ") + 1)


def test_a_parameter_without_value_is_also_a_parameter_error():
    with pytest.raises(SqlParameterError):
        select("SELECT * FROM t WHERE a = $1")


def test_a_value_no_parameter_uses_is_rejected():
    with pytest.raises(SqlParameterError, match="\\$2 no aparece"):
        select("SELECT * FROM t WHERE a = $1", ANA, SEVEN)


def test_a_skipped_number_is_rejected():
    with pytest.raises(SqlParameterError, match="\\$2 no aparece"):
        select("SELECT * FROM t WHERE a = $1 AND b = $3", ANA, SEVEN, ANA)


def test_parameters_need_a_single_statement():
    with pytest.raises(SqlParameterError, match="una sola sentencia y llegaron 2"):
        parse_script("SELECT * FROM t WHERE a = $1; SELECT * FROM t", (ANA,))


def test_a_script_without_parameters_still_runs_whole():
    assert len(parse_script("SELECT * FROM t; SELECT * FROM u")) == 2


def test_a_script_with_one_statement_takes_parameters():
    statements = parse_script("SELECT * FROM t WHERE a = $1;", (ANA,))

    assert len(statements) == 1


def test_a_vector_literal_is_a_tuple_of_reals():
    statement = parse("INSERT INTO t VALUES (1, [0.5, -2, +3e-1])")

    assert isinstance(statement, InsertStatement)
    assert statement.rows[0][1] == Literal((0.5, -2.0, 0.3))


def test_a_vector_literal_goes_in_a_condition():
    statement = select("SELECT * FROM t WHERE v = [1, 2]")

    assert isinstance(statement.where, BinaryOperation)
    assert statement.where.right == Literal((1.0, 2.0))


@pytest.mark.parametrize(
    ("vector", "message"),
    [
        ("[]", "al menos un componente"),
        ("[1, 'a']", "un número como componente"),
        ("[1, x]", "un número como componente"),
        ("[1, 2", "'\\]' al final del vector"),
        (f"[1{'0' * 400}]", "demasiado grande"),
    ],
)
def test_a_malformed_vector_literal_is_a_syntax_error(vector: str, message: str):
    with pytest.raises(SqlSyntaxError, match=message):
        parse(f"SELECT * FROM t WHERE v = {vector}")
