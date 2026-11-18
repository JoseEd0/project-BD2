"""Qué scripts SQL solo leen, para saber cuáles se pueden reintentar sin peligro.

No es un parser: quita comentarios y literales, corta en `;` y mira el verbo de cada
sentencia. Ante la duda responde que el script escribe, porque reintentar una escritura que
quizá ya se aplicó la duplicaría, y no reintentar una lectura solo cuesta un error.
"""

from __future__ import annotations

import re

READING_VERB = "SELECT"
EXPLAIN_VERB = "EXPLAIN"
# `EXPLAIN ANALYZE` ejecuta la sentencia que explica: un `EXPLAIN ANALYZE INSERT` inserta.
EXECUTING_MODIFIER = "ANALYZE"
STATEMENT_SEPARATOR = ";"
# Comentarios de línea y de bloque, textos entre comillas simples y nombres entre dobles, con
# la comilla repetida como escape; uno sin cerrar llega hasta el final del script.
_COMMENT_OR_QUOTED = re.compile(
    r"--[^\n]*|/\*.*?(?:\*/|\Z)|'(?:[^']|'')*'?|\"(?:[^\"]|\"\")*\"?", re.DOTALL
)
_WORD = re.compile(r"[A-Za-z_]+")


def is_read_only(sql: str) -> bool:
    """`True` si cada sentencia del script es un `SELECT`, un `EXPLAIN` (que solo planifica)
    o un `EXPLAIN ANALYZE SELECT`."""
    code = _COMMENT_OR_QUOTED.sub(" ", sql)
    statements = [part for part in code.split(STATEMENT_SEPARATOR) if part.strip()]
    return bool(statements) and all(_reads(statement) for statement in statements)


def _reads(statement: str) -> bool:
    verb, *rest = [word.upper() for word in _WORD.findall(statement)[:3]] or [""]
    if verb == READING_VERB:
        return True
    if verb != EXPLAIN_VERB:
        return False
    return rest[:1] != [EXECUTING_MODIFIER] or rest[1:] == [READING_VERB]
