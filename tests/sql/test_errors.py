import pytest

from sql import parse
from sql.errors import SqlError, SqlLexicalError, SqlSyntaxError


def test_syntax_error_points_at_the_offending_token():
    with pytest.raises(SqlSyntaxError) as error:
        parse("SELECT * FROM")
    assert error.value.line == 1
    assert error.value.column == 14


def test_error_on_a_later_line_reports_that_line():
    with pytest.raises(SqlSyntaxError) as error:
        parse("SELECT *\nFROM t\nWHERE = 1")
    assert error.value.line == 3
    assert error.value.source_line == "WHERE = 1"


def test_error_message_renders_a_caret_under_the_token():
    with pytest.raises(SqlSyntaxError) as error:
        parse("SELECT * FROM t WHERE")
    rendered = str(error.value)
    assert "SELECT * FROM t WHERE" in rendered
    assert rendered.splitlines()[-1].strip() == "^"


def test_error_message_names_what_was_found():
    with pytest.raises(SqlSyntaxError, match="found end of input"):
        parse("SELECT")


def test_lexical_and_syntax_errors_share_a_base_class():
    assert issubclass(SqlLexicalError, SqlError)
    assert issubclass(SqlSyntaxError, SqlError)


def test_lexical_error_is_raised_before_parsing():
    with pytest.raises(SqlLexicalError):
        parse("SELECT ~ FROM t")


@pytest.mark.parametrize(
    "condition",
    [  # `--` would start a line comment, so the unary chain needs separating blanks
    "(" * 500 + "1" + ")" * 500, "NOT " * 500 + "a", "- " * 500 + "1"],
)
def test_pathological_nesting_fails_as_a_syntax_error(condition: str):
    with pytest.raises(SqlSyntaxError, match="nests deeper"):
        parse(f"SELECT * FROM t WHERE {condition}")
