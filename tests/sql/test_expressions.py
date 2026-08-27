import pytest

from sql import parse
from sql.errors import SqlSyntaxError
from sql.nodes import (
    BetweenPredicate,
    BinaryOperation,
    BinaryOperator,
    ColumnRef,
    FunctionCall,
    InPredicate,
    LikePredicate,
    Literal,
    NullPredicate,
    SelectStatement,
    Star,
    TupleExpression,
    UnaryOperation,
    UnaryOperator,
)


def where(condition: str):
    statement = parse(f"SELECT * FROM t WHERE {condition}")
    assert isinstance(statement, SelectStatement)
    assert statement.where is not None
    return statement.where


def projection(expression: str):
    statement = parse(f"SELECT {expression} FROM t")
    assert isinstance(statement, SelectStatement)
    return statement.projections[0].expression


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("1", Literal(1)),
        ("1.5", Literal(1.5)),
        ("'text'", Literal("text")),
        ("TRUE", Literal(True)),
        ("FALSE", Literal(False)),
        ("NULL", Literal(None)),
    ],
)
def test_literals(source: str, expected: Literal):
    assert projection(source) == expected


def test_column_reference():
    assert projection("age") == ColumnRef(name="age")


def test_qualified_column_reference():
    assert projection("t.age") == ColumnRef(name="age", qualifier="t")


def test_double_quoted_column_reference():
    assert projection('"order"') == ColumnRef(name="order")


def test_star_projection():
    assert projection("*") == Star()


def test_qualified_star_projection():
    assert projection("t.*") == Star(qualifier="t")


def test_and_binds_tighter_than_or():
    assert where("a OR b AND c") == BinaryOperation(
        BinaryOperator.OR,
        ColumnRef("a"),
        BinaryOperation(BinaryOperator.AND, ColumnRef("b"), ColumnRef("c")),
    )


def test_comparison_binds_tighter_than_and():
    condition = where("a = 1 AND b = 2")
    assert isinstance(condition, BinaryOperation)
    assert condition.operator is BinaryOperator.AND


def test_arithmetic_precedence():
    assert projection("1 + 2 * 3") == BinaryOperation(
        BinaryOperator.ADD,
        Literal(1),
        BinaryOperation(BinaryOperator.MULTIPLY, Literal(2), Literal(3)),
    )


def test_parentheses_override_precedence():
    assert projection("(1 + 2) * 3") == BinaryOperation(
        BinaryOperator.MULTIPLY,
        BinaryOperation(BinaryOperator.ADD, Literal(1), Literal(2)),
        Literal(3),
    )


def test_subtraction_is_left_associative():
    assert projection("10 - 3 - 2") == BinaryOperation(
        BinaryOperator.SUBTRACT,
        BinaryOperation(BinaryOperator.SUBTRACT, Literal(10), Literal(3)),
        Literal(2),
    )


def test_unary_minus():
    assert projection("-x") == UnaryOperation(UnaryOperator.NEGATE, ColumnRef("x"))


def test_not_has_lower_precedence_than_comparison():
    assert where("NOT a = 1") == UnaryOperation(
        UnaryOperator.NOT, BinaryOperation(BinaryOperator.EQUAL, ColumnRef("a"), Literal(1))
    )


def test_between_stops_before_the_outer_and():
    condition = where("id BETWEEN 1 AND 10 AND active = TRUE")
    assert isinstance(condition, BinaryOperation)
    assert condition.operator is BinaryOperator.AND
    assert condition.left == BetweenPredicate(ColumnRef("id"), Literal(1), Literal(10))


def test_not_between():
    assert where("id NOT BETWEEN 1 AND 10") == BetweenPredicate(
        ColumnRef("id"), Literal(1), Literal(10), negated=True
    )


def test_in_list():
    assert where("city IN ('Lima', 'Cusco')") == InPredicate(
        ColumnRef("city"), (Literal("Lima"), Literal("Cusco"))
    )


def test_not_in_list():
    assert where("id NOT IN (1)") == InPredicate(ColumnRef("id"), (Literal(1),), negated=True)


def test_like_and_not_like():
    assert where("name LIKE 'a%'") == LikePredicate(ColumnRef("name"), Literal("a%"))
    assert where("name NOT LIKE 'a%'") == LikePredicate(
        ColumnRef("name"), Literal("a%"), negated=True
    )


def test_is_null_and_is_not_null():
    assert where("email IS NULL") == NullPredicate(ColumnRef("email"))
    assert where("email IS NOT NULL") == NullPredicate(ColumnRef("email"), negated=True)


def test_function_call_without_arguments():
    assert projection("SCORE()") == FunctionCall(name="SCORE")


def test_function_call_with_positional_arguments():
    assert projection("POINT(-12.04, -77.04)") == FunctionCall(
        name="POINT",
        arguments=(
            UnaryOperation(UnaryOperator.NEGATE, Literal(12.04)),
            UnaryOperation(UnaryOperator.NEGATE, Literal(77.04)),
        ),
    )


def test_function_call_with_keyword_argument():
    call = projection("SIMILAR_TO('query.jpg', k = 10)")
    assert isinstance(call, FunctionCall)
    assert call.arguments == (Literal("query.jpg"),)
    assert dict(call.keyword_arguments) == {"k": Literal(10)}


def test_nested_function_calls():
    call = projection("distancia(ubicacion, POINT(1, 2))")
    assert isinstance(call, FunctionCall)
    assert isinstance(call.arguments[1], FunctionCall)


def test_star_argument_in_aggregate():
    assert projection("COUNT(*)") == FunctionCall(name="COUNT", arguments=(Star(),))


def test_positional_argument_after_keyword_is_rejected():
    with pytest.raises(SqlSyntaxError, match="positional argument"):
        parse("SELECT f(k = 1, 2) FROM t")


def test_duplicate_keyword_argument_is_rejected():
    with pytest.raises(SqlSyntaxError, match="duplicate argument"):
        parse("SELECT f(k = 1, k = 2) FROM t")


def test_unclosed_parenthesis_is_rejected():
    with pytest.raises(SqlSyntaxError):
        parse("SELECT (1 + 2 FROM t")


def test_parenthesized_pair_is_a_tuple_expression():
    assert projection("(1, 2)") == TupleExpression((Literal(1), Literal(2)))


def test_single_parenthesized_expression_is_not_a_tuple():
    assert projection("(1)") == Literal(1)
