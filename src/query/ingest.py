"""Filas que llegan como objetos JSON, una por línea: la carga masiva del gestor.

Es lo que el API recibe en `POST /tables/{name}/rows` (NDJSON). Cada línea es un objeto
`{columna: valor}`; cada valor se convierte con las reglas de los parámetros `$n` (ver
`query.parameters`), y una columna que no aparece queda en NULL. Un lote de filas es una
`LoadRowsStatement`: entra entero o no entra, como un `INSERT` de varias filas, y si una
fila lo impide, `RejectedRowError` dice cuál.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from errors import DomainError
from query.deadline import current_deadline
from query.expressions import NO_COLUMNS, NO_ROW, ExpressionEvaluator
from query.parameters import expression_of_json
from query.table import DuplicateError, RowChanges, Table
from sql.nodes import Statement
from storage.record import Record
from storage.schema import Schema
from storage.types import Value

JsonRow = Mapping[str, object]


class RowFormatError(DomainError):
    """Una línea no es una fila: no es JSON, no es un objeto o nombra una columna que no hay."""


class RejectedRowError(DomainError):
    """Una fila de un lote no se puede guardar, y por ella no se guarda ninguna del lote.

    `position` es el lugar de la fila en el lote.
    """

    def __init__(self, position: int, reason: str) -> None:
        super().__init__(reason)
        self.position = position


@dataclass(frozen=True, slots=True)
class LoadRowsStatement(Statement):
    """Un lote de la carga masiva, con sus filas ya leídas del JSON. Es el `COPY ... FROM
    STDIN` del gestor: no sale del parser, la arma el API con lo que recibe."""

    table: str
    rows: tuple[JsonRow, ...]


def decode_row(line: bytes) -> JsonRow:
    """El objeto JSON de una línea.

    Raises:
        RowFormatError: si la línea no es JSON en UTF-8 o no es un objeto.
    """
    try:
        decoded = json.loads(line)
    except ValueError as error:
        raise RowFormatError(f"la línea no es JSON válido: {error}") from None
    if not isinstance(decoded, dict):
        raise RowFormatError(
            f"la línea es un {type(decoded).__name__} de JSON y una fila es un objeto "
            "{columna: valor}"
        )
    return decoded


def insert_rows(table: Table, rows: Sequence[JsonRow], changes: RowChanges) -> int:
    """Inserta todas las filas o ninguna y devuelve cuántas. Mira el plazo de la sentencia
    antes de convertir cada fila.

    Raises:
        RejectedRowError: si una fila nombra una columna que no hay, trae un valor que no
            es de su columna, deja en NULL una que no lo admite o repite una clave; nada
            queda escrito.
        StatementTimeoutError: si el lote pasa del plazo antes de escribir nada.
    """
    deadline = current_deadline()
    stored = []
    for position, row in enumerate(rows):
        deadline.check()
        stored.append(_stored(table, position, row))
    try:
        table.insert_normalized(stored, changes)
    except DuplicateError as error:
        raise RejectedRowError(error.position, str(error)) from error
    return len(stored)


def _stored(table: Table, position: int, row: JsonRow) -> Record:
    try:
        return table.normalized(_values_of(row, table.schema))
    except DomainError as error:
        raise RejectedRowError(position, str(error)) from error


def _values_of(row: JsonRow, schema: Schema) -> list[Value]:
    evaluator = ExpressionEvaluator(NO_COLUMNS)
    values: list[Value] = [None] * len(schema)
    for name, value in row.items():
        if not schema.has_field(name):
            raise RowFormatError(f"la tabla no tiene la columna '{name}'")
        expression = expression_of_json(value, f"la columna '{name}'")
        values[schema.position_of(name)] = evaluator.evaluate(expression, NO_ROW)
    return values
