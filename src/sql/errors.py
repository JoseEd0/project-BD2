"""Errors raised while turning SQL text into an abstract syntax tree."""

from __future__ import annotations

CARET_MARKER = "^"


class SqlError(Exception):
    """Base class for every failure of the SQL front-end."""


class SqlPositionError(SqlError):
    """A failure that can be pinned to a line and column of the source.

    Attributes:
        message: human readable cause, without position information.
        line: 1-based line number.
        column: 1-based column number.
        source_line: the offending line, used to render a caret excerpt.
    """

    def __init__(self, message: str, line: int, column: int, source_line: str) -> None:
        self.message = message
        self.line = line
        self.column = column
        self.source_line = source_line
        super().__init__(self._render())

    def _render(self) -> str:
        header = f"{self.message} (line {self.line}, column {self.column})"
        if not self.source_line:
            return header
        caret = " " * (self.column - 1) + CARET_MARKER
        return f"{header}\n{self.source_line}\n{caret}"


class SqlLexicalError(SqlPositionError):
    """The input contains a character sequence that is not a valid token."""


class SqlSyntaxError(SqlPositionError):
    """The token sequence does not match the grammar."""
