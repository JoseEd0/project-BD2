"""Valores de los parámetros `$n` de una sentencia, tal como llegan en JSON.

Cada valor se convierte en la expresión que se habría escrito en su lugar y el parser la
pone donde va cada `$n` (ver `sql.parser.Parser`). Así un parámetro pasa por las mismas
comprobaciones de tipo que un literal, antes de leer ninguna fila, y nunca se lee como SQL:
`'; DROP TABLE x; --` llega a la consulta como un texto más.

| JSON | Valor |
|---|---|
| número, texto, `true`/`false`, `null` | el literal del mismo tipo |
| lista de números | un `VECTOR`, igual que `[0.1, 0.2]` |
| `{"lat": …, "lon": …}` | un `POINT`, igual que `POINT(lat, lon)` |

Las mismas reglas convierten cada valor de las filas de la carga masiva (`query.ingest`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from errors import DomainError
from query.functions import POINT
from sql.nodes import Expression, FunctionCall, Literal
from sql.tokens import PARAMETER_PREFIX

LATITUDE_KEY = "lat"
LONGITUDE_KEY = "lon"
POINT_KEYS = frozenset({LATITUDE_KEY, LONGITUDE_KEY})


class ParameterValueError(DomainError):
    """El valor de un parámetro no corresponde a ningún tipo del dialecto."""


def bound_parameters(values: Sequence[object]) -> tuple[Expression, ...]:
    """La expresión de cada parámetro, `$1` primero, para entregarla al parser.

    Raises:
        ParameterValueError: si algún valor no es de un tipo que el gestor entienda.
    """
    return tuple(
        expression_of_json(value, f"el parámetro {PARAMETER_PREFIX}{number}")
        for number, value in enumerate(values, start=1)
    )


def expression_of_json(value: object, name: str) -> Expression:
    """La expresión de un valor JSON según la tabla de arriba; `name` lo nombra en los
    errores, como `el parámetro $1` o `la columna 'emb'`.

    Raises:
        ParameterValueError: si el valor no es de un tipo que el gestor entienda.
    """
    if value is None or isinstance(value, bool | str):
        return Literal(value)
    if isinstance(value, int | float):
        return Literal(_finite(value, name))
    if isinstance(value, list):
        return Literal(_vector_of(value, name))
    if isinstance(value, dict):
        return _point_of(value, name)
    raise ParameterValueError(f"{name} es de un tipo que el gestor no admite")


def _vector_of(components: list[object], name: str) -> tuple[float, ...]:
    if not components:
        raise ParameterValueError(f"{name} es un vector sin componentes")
    return tuple(_component(component, name) for component in components)


def _component(value: object, name: str) -> float:
    try:
        return float(_number(value, name))
    except OverflowError as error:
        raise ParameterValueError(
            f"{name} tiene un componente demasiado grande"
        ) from error


def _point_of(fields: dict[object, object], name: str) -> Expression:
    if fields.keys() != POINT_KEYS:
        raise ParameterValueError(
            f"{name} es un objeto: solo se admite un punto, "
            f'{{"{LATITUDE_KEY}": …, "{LONGITUDE_KEY}": …}}'
        )
    latitude = _number(fields[LATITUDE_KEY], name)
    longitude = _number(fields[LONGITUDE_KEY], name)
    return FunctionCall(POINT, (Literal(latitude), Literal(longitude)))


def _number(value: object, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ParameterValueError(f"{name} lleva {value!r} donde va un número")
    return _finite(value, name)


def _finite(value: int | float, name: str) -> int | float:
    """Ni NaN ni infinito: ninguna columna los guarda y el lexer tampoco los produce."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ParameterValueError(f"{name} no es un número finito: {value!r}")
    return value
