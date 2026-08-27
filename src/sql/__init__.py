"""SQL front-end: text in, abstract syntax tree out.

`parse` handles a single statement and `parse_script` a `;`-separated batch.
Nothing in this package touches storage, so it can be exercised on its own.
"""

from .errors import SqlError, SqlLexicalError, SqlPositionError, SqlSyntaxError
from .lexer import tokenize
from .parser import parse, parse_script
from .tokens import Token, TokenType

__all__ = [
    "SqlError",
    "SqlLexicalError",
    "SqlPositionError",
    "SqlSyntaxError",
    "Token",
    "TokenType",
    "parse",
    "parse_script",
    "tokenize",
]
