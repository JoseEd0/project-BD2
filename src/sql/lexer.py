"""Scanner that turns SQL text into a token stream."""

from __future__ import annotations

from .errors import SqlLexicalError
from .tokens import KEYWORDS, Token, TokenType

LINE_COMMENT_PREFIX = "--"
BLOCK_COMMENT_OPEN = "/*"
BLOCK_COMMENT_CLOSE = "*/"
STRING_QUOTE = "'"
NAME_QUOTE = '"'
DIGITS = frozenset("0123456789")
IDENTIFIER_EXTRA_CHARS = frozenset("_$")
EXPONENT_MARKERS = frozenset("eE")
SIGN_CHARS = frozenset("+-")

_SINGLE_CHAR_TOKENS: dict[str, TokenType] = {
    ",": TokenType.COMMA,
    ";": TokenType.SEMICOLON,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    ".": TokenType.DOT,
    "*": TokenType.STAR,
    "+": TokenType.PLUS,
    "-": TokenType.MINUS,
    "/": TokenType.SLASH,
    "%": TokenType.PERCENT,
    "=": TokenType.EQUAL,
    "<": TokenType.LESS,
    ">": TokenType.GREATER,
}

_TWO_CHAR_TOKENS: dict[str, TokenType] = {
    "<>": TokenType.NOT_EQUAL,
    "!=": TokenType.NOT_EQUAL,
    "<=": TokenType.LESS_EQUAL,
    ">=": TokenType.GREATER_EQUAL,
    "==": TokenType.EQUAL,
}


class Lexer:
    """Produces the token stream of a SQL script.

    Raises:
        SqlLexicalError: on unterminated literals or unknown characters.
    """

    def __init__(self, source: str) -> None:
        self._source = source
        self._position = 0
        self._line = 1
        self._column = 1

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while True:
            token = self._next_token()
            tokens.append(token)
            if token.type is TokenType.EOF:
                return tokens

    def _next_token(self) -> Token:
        self._skip_blanks_and_comments()
        line, column = self._line, self._column
        if self._at_end():
            return Token(TokenType.EOF, "", line, column)

        char = self._peek()
        if char in DIGITS:
            return self._read_number(line, column)
        if char.isalpha() or char in IDENTIFIER_EXTRA_CHARS:
            return self._read_word(line, column)
        if char == STRING_QUOTE:
            return self._read_quoted(STRING_QUOTE, TokenType.STRING, line, column)
        if char == NAME_QUOTE:
            return self._read_quoted(NAME_QUOTE, TokenType.QUOTED_NAME, line, column)
        return self._read_operator(line, column)

    def _skip_blanks_and_comments(self) -> None:
        while not self._at_end():
            if self._peek().isspace():
                self._advance()
            elif self._starts_with(LINE_COMMENT_PREFIX):
                self._skip_line_comment()
            elif self._starts_with(BLOCK_COMMENT_OPEN):
                self._skip_block_comment()
            else:
                return

    def _skip_line_comment(self) -> None:
        while not self._at_end() and self._peek() != "\n":
            self._advance()

    def _skip_block_comment(self) -> None:
        line, column = self._line, self._column
        self._advance_by(len(BLOCK_COMMENT_OPEN))
        while not self._starts_with(BLOCK_COMMENT_CLOSE):
            if self._at_end():
                raise self._error("unterminated block comment", line, column)
            self._advance()
        self._advance_by(len(BLOCK_COMMENT_CLOSE))

    def _read_number(self, line: int, column: int) -> Token:
        start = self._position
        self._consume_digits()
        is_float = False
        if self._peek() == "." and self._peek(1) in DIGITS:
            is_float = True
            self._advance()
            self._consume_digits()
        if self._peek() in EXPONENT_MARKERS and self._exponent_follows():
            is_float = True
            self._advance()
            if self._peek() in SIGN_CHARS:
                self._advance()
            self._consume_digits()
        text = self._source[start : self._position]
        if self._peek().isalpha() or self._peek() in IDENTIFIER_EXTRA_CHARS:
            raise self._error(f"invalid number literal '{text}{self._peek()}'", line, column)
        if is_float:
            return Token(TokenType.FLOAT, text, line, column)
        self._reject_unconvertible_integer(text, line, column)
        return Token(TokenType.INTEGER, text, line, column)

    def _reject_unconvertible_integer(self, text: str, line: int, column: int) -> None:
        """Guarantees every INTEGER token converts: CPython caps `int(str)` at 4300 digits."""
        try:
            int(text)
        except ValueError as error:
            raise self._error("integer literal is too large", line, column) from error

    def _exponent_follows(self) -> bool:
        offset = 2 if self._peek(1) in SIGN_CHARS else 1
        return self._peek(offset) in DIGITS

    def _consume_digits(self) -> None:
        while self._peek() in DIGITS:
            self._advance()

    def _read_word(self, line: int, column: int) -> Token:
        start = self._position
        while self._peek().isalnum() or self._peek() in IDENTIFIER_EXTRA_CHARS:
            self._advance()
        text = self._source[start : self._position]
        return Token(KEYWORDS.get(text.upper(), TokenType.IDENTIFIER), text, line, column)

    def _read_quoted(self, quote: str, token_type: TokenType, line: int, column: int) -> Token:
        self._advance()
        chunks: list[str] = []
        while True:
            if self._at_end():
                raise self._error(f"unterminated {token_type.value}", line, column)
            char = self._advance()
            if char != quote:
                chunks.append(char)
                continue
            if self._peek() == quote:
                self._advance()
                chunks.append(quote)
                continue
            text = "".join(chunks)
            if not text and token_type is TokenType.QUOTED_NAME:
                raise self._error("empty quoted identifier", line, column)
            return Token(token_type, text, line, column)

    def _read_operator(self, line: int, column: int) -> Token:
        pair = self._source[self._position : self._position + 2]
        if pair in _TWO_CHAR_TOKENS:
            self._advance_by(2)
            return Token(_TWO_CHAR_TOKENS[pair], pair, line, column)
        char = self._peek()
        if char in _SINGLE_CHAR_TOKENS:
            self._advance()
            return Token(_SINGLE_CHAR_TOKENS[char], char, line, column)
        raise self._error(f"unexpected character '{char}'", line, column)

    def _error(self, message: str, line: int, column: int) -> SqlLexicalError:
        return SqlLexicalError(message, line, column, self._line_text(line))

    def _line_text(self, line: int) -> str:
        lines = self._source.splitlines()
        return lines[line - 1] if 0 < line <= len(lines) else ""

    def _at_end(self) -> bool:
        return self._position >= len(self._source)

    def _peek(self, offset: int = 0) -> str:
        index = self._position + offset
        return self._source[index] if index < len(self._source) else ""

    def _starts_with(self, prefix: str) -> bool:
        return self._source.startswith(prefix, self._position)

    def _advance(self) -> str:
        char = self._source[self._position]
        self._position += 1
        if char == "\n":
            self._line += 1
            self._column = 1
        else:
            self._column += 1
        return char

    def _advance_by(self, count: int) -> None:
        for _ in range(count):
            self._advance()


def tokenize(source: str) -> list[Token]:
    """Tokenizes `source` and returns the tokens, always ending with EOF."""
    return Lexer(source).tokenize()
