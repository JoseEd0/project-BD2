"""Lexical vocabulary shared by the lexer and the parser."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique


@unique
class TokenType(Enum):
    IDENTIFIER = "identifier"
    QUOTED_NAME = "double-quoted name"
    STRING = "string literal"
    INTEGER = "integer literal"
    FLOAT = "float literal"

    COMMA = ","
    SEMICOLON = ";"
    LPAREN = "("
    RPAREN = ")"
    DOT = "."
    STAR = "*"
    PLUS = "+"
    MINUS = "-"
    SLASH = "/"
    PERCENT = "%"
    EQUAL = "="
    NOT_EQUAL = "<>"
    LESS = "<"
    LESS_EQUAL = "<="
    GREATER = ">"
    GREATER_EQUAL = ">="

    SELECT = "SELECT"
    DISTINCT = "DISTINCT"
    FROM = "FROM"
    AS = "AS"
    WHERE = "WHERE"
    GROUP = "GROUP"
    BY = "BY"
    HAVING = "HAVING"
    ORDER = "ORDER"
    ASC = "ASC"
    DESC = "DESC"
    LIMIT = "LIMIT"
    OFFSET = "OFFSET"
    JOIN = "JOIN"
    INNER = "INNER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    FULL = "FULL"
    OUTER = "OUTER"
    ON = "ON"
    USING = "USING"
    WITH = "WITH"

    INSERT = "INSERT"
    INTO = "INTO"
    VALUES = "VALUES"
    DELETE = "DELETE"
    UPDATE = "UPDATE"
    SET = "SET"

    CREATE = "CREATE"
    TABLE = "TABLE"
    INDEX = "INDEX"
    DROP = "DROP"
    IF = "IF"
    EXISTS = "EXISTS"
    FILE = "FILE"
    PRIMARY = "PRIMARY"
    KEY = "KEY"
    UNIQUE = "UNIQUE"

    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    BETWEEN = "BETWEEN"
    IN = "IN"
    IS = "IS"
    LIKE = "LIKE"
    NULL = "NULL"
    TRUE = "TRUE"
    FALSE = "FALSE"

    BEGIN = "BEGIN"
    END = "END"
    TRANSACTION = "TRANSACTION"
    COMMIT = "COMMIT"
    ROLLBACK = "ROLLBACK"

    EXPLAIN = "EXPLAIN"
    ANALYZE = "ANALYZE"

    EOF = "end of input"


KEYWORDS: dict[str, TokenType] = {
    member.value: member
    for member in TokenType
    if member.value.isalpha() and member.value.isupper()
}


@dataclass(frozen=True, slots=True)
class Token:
    """A lexeme with its source position.

    `text` carries the decoded value for string tokens and the literal spelling
    for every other kind, so the parser never re-reads the raw source.
    """

    type: TokenType
    text: str
    line: int
    column: int

    def __str__(self) -> str:
        if self.type in (TokenType.EOF,):
            return str(self.type.value)
        return f"{self.type.value} '{self.text}'"
