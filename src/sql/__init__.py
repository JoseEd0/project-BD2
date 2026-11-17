"""SQL front-end: text in, abstract syntax tree out.

`parse` handles a single statement and `parse_script` a `;`-separated batch. Both take
the values of the `$n` parameters as already built expressions and put them in place of
each `$n` while parsing, so a value never goes through the lexer.
Nothing in this package touches storage, so it can be exercised on its own.
"""

from .errors import (
    SqlError,
    SqlLexicalError,
    SqlMissingParameterError,
    SqlParameterError,
    SqlPositionError,
    SqlSyntaxError,
)
from .lexer import tokenize
from .parser import parse, parse_script
from .tokens import Token, TokenType

__all__ = [
    "SqlError",
    "SqlLexicalError",
    "SqlMissingParameterError",
    "SqlParameterError",
    "SqlPositionError",
    "SqlSyntaxError",
    "Token",
    "TokenType",
    "parse",
    "parse_script",
    "tokenize",
]
