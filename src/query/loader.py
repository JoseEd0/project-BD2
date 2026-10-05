"""Carga de tablas desde CSV para `CREATE TABLE ... FROM FILE`.

El esquema se deduce del archivo: la cabecera da los nombres y los valores dan los tipos.
Se recorre el archivo dos veces —una para deducir y otra para cargar— porque deducir con
una muestra dejaría fuera el valor largo que aparece en la fila diez mil.

Un punto geográfico se escribe en el CSV igual que en SQL, `POINT(-12.0464, -77.0428)`:
latitud y luego longitud, separadas por una coma.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from math import isfinite
from pathlib import Path

from config import EngineConfig
from spatial.geometry import GeometryError, Point, geographic_point
from storage.schema import Field, Schema
from storage.types import MAX_INT, MIN_INT, FieldType, Value

DATE_FORMAT_LENGTH = 10
STRING_LENGTH_MARGIN = 8
MINIMUM_STRING_LENGTH = 8
# Tipo de una columna en la que ninguna fila trae valor.
EMPTY_COLUMN_TYPE = FieldType.INT
# Un booleano se escribe con palabras: `1` y `0` son números.
BOOLEAN_WORDS = {"true": True, "false": False}
# La forma `POINT(a, b)`; que `a` y `b` sean números lo decide `float`, que entiende
# todas las escrituras de un real (`5e-05`, `.5`, `-12.`).
POINT_PATTERN = re.compile(
    r"^POINT\s*\(\s*(?P<lat>[^,()]+?)\s*,\s*(?P<lon>[^,()]+?)\s*\)$", re.IGNORECASE
)


class LoaderError(Exception):
    """El archivo no se puede cargar como tabla."""


def infer_schema(path: Path, config: EngineConfig) -> Schema:
    """Deduce el esquema de un CSV con cabecera.

    Raises:
        LoaderError: si el archivo no existe o no tiene cabecera.
    """
    names = read_header(path, config)
    seen: list[FieldType | None] = [None] * len(names)
    lengths = [MINIMUM_STRING_LENGTH] * len(names)
    for row in _read_rows(path, config, len(names)):
        for position, raw in enumerate(row):
            seen[position] = _widen(seen[position], raw)
            lengths[position] = max(lengths[position], len(raw.encode()) + STRING_LENGTH_MARGIN)
    kinds = [EMPTY_COLUMN_TYPE if kind is None else kind for kind in seen]
    return Schema(
        [
            Field(
                name=name,
                type=kind,
                length=min(lengths[position], config.text_length)
                if kind is FieldType.STRING
                else None,
            )
            for position, (name, kind) in enumerate(zip(names, kinds, strict=True))
        ]
    )


def read_values(path: Path, schema: Schema, config: EngineConfig) -> Iterator[tuple[Value, ...]]:
    """Filas del CSV ya convertidas a los tipos del esquema."""
    for row in _read_rows(path, config, len(schema)):
        yield tuple(
            _convert(raw, field.type) for raw, field in zip(row, schema.fields, strict=True)
        )


@contextmanager
def _readable(path: Path, config: EngineConfig) -> Iterator[None]:
    """Un archivo con otra codificación o con comillas rotas es un error del archivo, no del
    gestor: se informa como `LoaderError` para que el usuario sepa qué corregir."""
    try:
        yield
    except (UnicodeDecodeError, csv.Error) as error:
        raise LoaderError(
            f"'{path.name}' no se puede leer como CSV en {config.csv_encoding}: {error}"
        ) from error


def read_header(path: Path, config: EngineConfig) -> list[str]:
    """Nombres de columna de la cabecera del CSV.

    Raises:
        LoaderError: si el archivo no existe, no tiene cabecera o no se puede leer.
    """
    if not path.exists():
        raise LoaderError(f"no existe el archivo '{path}'")
    with _readable(path, config), path.open(newline="", encoding=config.csv_encoding) as handle:
        header = next(csv.reader(handle, delimiter=config.csv_delimiter), None)
    if not header:
        raise LoaderError(f"'{path.name}' no tiene cabecera")
    return [name.strip() for name in header]


def _read_rows(path: Path, config: EngineConfig, columns: int) -> Iterator[Sequence[str]]:
    with _readable(path, config), path.open(newline="", encoding=config.csv_encoding) as handle:
        reader = csv.reader(handle, delimiter=config.csv_delimiter)
        next(reader, None)
        for number, row in enumerate(reader, start=2):
            if not row:
                continue
            if len(row) != columns:
                raise LoaderError(
                    f"la fila {number} de '{path.name}' tiene {len(row)} campos "
                    f"y la cabecera {columns}"
                )
            yield row


def _widen(current: FieldType | None, raw: str) -> FieldType | None:
    """Tipo de la columna tras ver un valor más. `None` es "todavía sin valores".

    Un tipo solo puede ensancharse, y siempre hacia uno que admita todo lo ya visto: un
    entero cabe en una columna real, pero una fecha y un número solo conviven como texto.
    """
    if not raw.strip():
        return current
    kind = _type_of(raw)
    if current is None or current is kind:
        return kind
    if {current, kind} == {FieldType.INT, FieldType.FLOAT}:
        return FieldType.FLOAT
    return FieldType.STRING


def _type_of(raw: str) -> FieldType:
    if raw.strip().lower() in BOOLEAN_WORDS:
        return FieldType.BOOL
    if _is_integer(raw):
        # Un entero que no cabe en 64 bits se guarda como texto: como real perdería cifras.
        return FieldType.INT if MIN_INT <= int(raw) <= MAX_INT else FieldType.STRING
    if _is_float(raw):
        return FieldType.FLOAT
    if _is_date(raw):
        return FieldType.DATE
    if _coordinates(raw) is not None:
        return FieldType.POINT
    return FieldType.STRING


def _is_integer(raw: str) -> bool:
    text = raw.strip()
    return bool(text) and (text[1:] if text[0] in "+-" else text).isdecimal()


def _is_float(raw: str) -> bool:
    """Si el texto es un real finito: `nan` e `inf` son nombres, no medidas."""
    try:
        return isfinite(float(raw))
    except ValueError:
        return False


def _coordinates(raw: str) -> tuple[float, float] | None:
    """Latitud y longitud de un texto `POINT(lat, lon)`; `None` si no tiene esa forma."""
    matched = POINT_PATTERN.match(raw.strip())
    if matched is None or not _is_float(matched["lat"]) or not _is_float(matched["lon"]):
        return None
    return float(matched["lat"]), float(matched["lon"])


def _is_date(raw: str) -> bool:
    text = raw.strip()
    if len(text) != DATE_FORMAT_LENGTH:
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _convert(raw: str, kind: FieldType) -> Value:
    text = raw.strip()
    if not text:
        return None
    if kind is FieldType.INT:
        return int(text)
    if kind is FieldType.FLOAT:
        return float(text)
    if kind is FieldType.BOOL:
        return _parse_boolean(text)
    if kind is FieldType.DATE:
        return date.fromisoformat(text)
    if kind is FieldType.POINT:
        return _parse_point(text)
    return text


def _parse_boolean(text: str) -> bool:
    """Booleano escrito como `true` o `false`, en cualquier combinación de mayúsculas.

    Raises:
        LoaderError: si el texto es otra cosa.
    """
    value = BOOLEAN_WORDS.get(text.lower())
    if value is None:
        raise LoaderError(f"'{text}' no es un booleano; se espera true o false")
    return value


def _parse_point(text: str) -> Point:
    """Punto escrito como `POINT(latitud, longitud)`.

    Raises:
        LoaderError: si las coordenadas están fuera de rango.
    """
    coordinates = _coordinates(text)
    if coordinates is None:
        raise LoaderError(f"'{text}' no es un punto; se espera POINT(latitud, longitud)")
    try:
        return geographic_point(*coordinates)
    except GeometryError as error:
        raise LoaderError(f"punto inválido '{text}': {error}") from error
