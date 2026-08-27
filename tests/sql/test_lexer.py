import pytest

from sql import tokenize
from sql.errors import SqlLexicalError
from sql.tokens import TokenType


def types(source: str) -> list[TokenType]:
    return [token.type for token in tokenize(source)]


def test_empty_source_yields_only_eof():
    assert types("") == [TokenType.EOF]


def test_keywords_are_case_insensitive():
    assert types("select From wHeRe") == [
        TokenType.SELECT,
        TokenType.FROM,
        TokenType.WHERE,
        TokenType.EOF,
    ]


def test_identifiers_keep_their_spelling():
    assert tokenize("MiTabla")[0].text == "MiTabla"


def test_underscore_and_digits_in_identifiers():
    token = tokenize("_col1$")[0]
    assert token.type is TokenType.IDENTIFIER
    assert token.text == "_col1$"


@pytest.mark.parametrize(
    ("source", "expected"),
    [("42", "42"), ("3.14", "3.14"), ("1e3", "1e3"), ("2.5E-4", "2.5E-4"), ("7e+2", "7e+2")],
)
def test_number_literals(source: str, expected: str):
    token = tokenize(source)[0]
    assert token.text == expected


def test_integer_and_float_are_distinguished():
    assert types("1 1.0") == [TokenType.INTEGER, TokenType.FLOAT, TokenType.EOF]


def test_dot_after_integer_without_digits_is_not_part_of_the_number():
    assert types("t.1") == [TokenType.IDENTIFIER, TokenType.DOT, TokenType.INTEGER, TokenType.EOF]


def test_string_literal_unescapes_doubled_quote():
    assert tokenize("'O''Brien'")[0].text == "O'Brien"


def test_string_literal_keeps_backslashes():
    assert tokenize("'C:\\data.csv'")[0].text == "C:\\data.csv"


def test_double_quoted_name_is_its_own_token():
    token = tokenize('"order by"')[0]
    assert token.type is TokenType.QUOTED_NAME
    assert token.text == "order by"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("<>", TokenType.NOT_EQUAL),
        ("!=", TokenType.NOT_EQUAL),
        ("<=", TokenType.LESS_EQUAL),
        (">=", TokenType.GREATER_EQUAL),
        ("==", TokenType.EQUAL),
        ("<", TokenType.LESS),
        ("%", TokenType.PERCENT),
    ],
)
def test_operators(source: str, expected: TokenType):
    assert tokenize(source)[0].type is expected


def test_line_comment_is_skipped():
    assert types("1 -- comment\n2") == [TokenType.INTEGER, TokenType.INTEGER, TokenType.EOF]


def test_block_comment_is_skipped():
    assert types("1 /* two\nlines */ 2") == [TokenType.INTEGER, TokenType.INTEGER, TokenType.EOF]


def test_positions_track_newlines():
    token = tokenize("SELECT\n  age")[1]
    assert (token.line, token.column) == (2, 3)


def test_unterminated_string_reports_its_opening_position():
    with pytest.raises(SqlLexicalError) as error:
        tokenize("SELECT 'abc")
    assert error.value.column == 8


def test_unterminated_block_comment_is_rejected():
    with pytest.raises(SqlLexicalError):
        tokenize("/* never closed")


def test_unknown_character_is_rejected():
    with pytest.raises(SqlLexicalError) as error:
        tokenize("SELECT #")
    assert "unexpected character" in error.value.message


def test_number_glued_to_letters_is_rejected():
    with pytest.raises(SqlLexicalError):
        tokenize("12abc")


def test_empty_quoted_identifier_is_rejected():
    with pytest.raises(SqlLexicalError, match="empty quoted identifier"):
        tokenize('SELECT "" FROM t')


def test_empty_string_literal_is_allowed():
    assert tokenize("''")[0].text == ""


@pytest.mark.parametrize("source", ["²", "³", "½", "٣"])
def test_non_ascii_digits_are_not_number_literals(source: str):
    with pytest.raises(SqlLexicalError):
        tokenize(f"SELECT {source} FROM t")


def test_accented_letters_are_valid_identifiers():
    assert tokenize("categoría")[0].text == "categoría"


def test_integer_literal_beyond_the_conversion_limit_is_rejected():
    with pytest.raises(SqlLexicalError, match="too large"):
        tokenize("SELECT " + "9" * 5000)


def test_long_but_convertible_integer_is_accepted():
    assert tokenize("9" * 100)[0].text == "9" * 100
