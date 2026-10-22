"""Qué valores se pueden comparar entre sí.

SQL compara valores de la misma familia: número con número, texto con texto, fecha con
fecha. El evaluador de expresiones y el planificador aplican aquí la misma regla, de modo
que una condición responde lo mismo —o se rechaza igual— recorra la tabla o use un índice.

La única conversión es la de una fecha escrita como texto ISO: `fecha >= '2024-01-05'`.
"""

from __future__ import annotations

from datetime import date
from enum import Enum, unique

from spatial.geometry import Point, Polygon
from storage.schema import Field
from storage.types import FieldType, InvalidValueError, Value, codec_for, date_from


class ComparisonError(Exception):
    """Los dos valores no se pueden comparar."""


@unique
class Family(Enum):
    NUMBER = "un número"
    TEXT = "un texto"
    BOOLEAN = "un booleano"
    DATE = "una fecha"
    BYTES = "bytes"
    POINT = "un punto"
    VECTOR = "un vector"
    POLYGON = "un polígono"


# Caracteres de un valor que se citan en un mensaje de error: un polígono o un entero de
# cientos de cifras lo volverían ilegible.
SHOWN_VALUE_LENGTH = 40
# Un punto, un vector o un polígono se pueden comparar por igualdad, pero no tienen orden.
UNORDERED_FAMILIES = frozenset({Family.POINT, Family.VECTOR, Family.POLYGON})
_FAMILY_OF_TYPE = {
    FieldType.INT: Family.NUMBER,
    FieldType.FLOAT: Family.NUMBER,
    FieldType.BOOL: Family.BOOLEAN,
    FieldType.STRING: Family.TEXT,
    FieldType.DATE: Family.DATE,
    FieldType.BYTES: Family.BYTES,
    FieldType.POINT: Family.POINT,
    FieldType.VECTOR: Family.VECTOR,
}
_NUMBER_TYPES = frozenset({int, float})
_ORDERED_TYPES = frozenset({int, float, str, bool, date, bytes})


def plainly_comparable(left: object, right: object) -> bool:
    """Si son dos números, o dos valores del mismo tipo con orden.

    Es el caso de casi todas las comparaciones, y se decide mirando solo los tipos:
    quien filtra una tabla lo pregunta antes, para no pagar por cada fila las reglas
    completas de `comparable` y `orderable`, que con estos valores no cambian nada.
    """
    kind = type(left)
    if kind is type(right):
        return kind in _ORDERED_TYPES
    return kind in _NUMBER_TYPES and type(right) in _NUMBER_TYPES


def comparable(left: object, right: object) -> tuple[object, object]:
    """Los dos valores listos para compararse con `=` o `<>`.

    Raises:
        ComparisonError: si son de familias distintas.
    """
    first, second = _with_dates_parsed(left, right)
    if family_of(first) is not family_of(second):
        raise ComparisonError(
            f"no se puede comparar {_described(left)} con {_described(right)}"
        )
    return first, second


def orderable(left: object, right: object) -> tuple[object, object]:
    """Los dos valores listos para compararse con `<`, `<=`, `>` o `>=`.

    Raises:
        ComparisonError: si son de familias distintas o de una familia sin orden.
    """
    first, second = comparable(left, right)
    family = family_of(first)
    if family in UNORDERED_FAMILIES:
        raise ComparisonError(f"{family.value} no tiene orden: solo admite = y <>")
    return first, second


def literal_for(field: Field, value: Value) -> Value:
    """El literal de una condición, llevado al tipo de la columna con la que se compara.

    Raises:
        ComparisonError: si un valor así no se puede comparar con esa columna.
    """
    sample = codec_for(field.type, field.length).neutral_value()
    try:
        comparable(sample, value)
    except ComparisonError:
        raise ComparisonError(
            f"la columna '{field.name}' es {field.type.value} y no se puede comparar "
            f"con {_described(value)}"
        ) from None
    if field.type is FieldType.DATE:
        return date_from(value)
    if field.type is FieldType.INT and isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def family_of(value: object) -> Family:
    """Familia a la que pertenece un valor no nulo.

    Raises:
        ComparisonError: si el valor no es de ningún tipo del dialecto.
    """
    if isinstance(value, bool):
        return Family.BOOLEAN
    if isinstance(value, int | float):
        return Family.NUMBER
    if isinstance(value, str):
        return Family.TEXT
    if isinstance(value, date):
        return Family.DATE
    if isinstance(value, bytes):
        return Family.BYTES
    if isinstance(value, Point):
        return Family.POINT
    if isinstance(value, tuple):
        return Family.VECTOR
    if isinstance(value, Polygon):
        return Family.POLYGON
    raise ComparisonError(f"no se puede comparar un valor de tipo {type(value).__name__}")


def family_of_type(field_type: FieldType) -> Family:
    """Familia de los valores que guarda una columna de ese tipo."""
    return _FAMILY_OF_TYPE[field_type]


def hash_comparable(left: FieldType, right: FieldType) -> bool:
    """Si dos columnas se pueden igualar comparando sus valores tal cual, sin convertir.

    Es lo que necesita un hash join: reparte por los bytes de cada valor, así que una fecha
    y su texto ISO, que `comparable` sí iguala, caerían en particiones distintas.

    Raises:
        ComparisonError: si sus valores no se pueden comparar de ningún modo.
    """
    first, second = family_of_type(left), family_of_type(right)
    if first is second:
        return True
    if {first, second} == {Family.DATE, Family.TEXT}:
        return False
    raise ComparisonError(
        f"no se puede comparar una columna {left.value} con una {right.value}"
    )


def _with_dates_parsed(left: object, right: object) -> tuple[object, object]:
    """Convierte en fecha el texto que se compara con una fecha."""
    try:
        if isinstance(left, date) and isinstance(right, str):
            return left, date_from(right)
        if isinstance(left, str) and isinstance(right, date):
            return date_from(left), right
    except InvalidValueError as error:
        raise ComparisonError(str(error)) from error
    return left, right


def describe_value(value: object) -> str:
    """Un valor tal como se escribiría en SQL."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, date):
        return f"'{value.isoformat()}'"
    if isinstance(value, Point):
        return f"POINT({value.lat!r}, {value.lon!r})"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return repr(value)


def _described(value: object) -> str:
    shown = describe_value(value)
    if len(shown) > SHOWN_VALUE_LENGTH:
        shown = f"{shown[:SHOWN_VALUE_LENGTH]}…"
    return f"{family_of(value).value} ({shown})"
