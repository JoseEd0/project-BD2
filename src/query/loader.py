"""Carga de tablas desde CSV para `CREATE TABLE ... FROM FILE`.

El esquema se deduce del archivo: la cabecera da los nombres y los valores dan los tipos.
Se recorre el archivo dos veces —una para deducir y otra para cargar— porque deducir con
una muestra dejaría fuera el valor largo que aparece en la fila diez mil.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from config import EngineConfig
from storage.schema import Field, Schema
from storage.types import FieldType, Value

DATE_FORMAT_LENGTH = 10
STRING_LENGTH_MARGIN = 8
MINIMUM_STRING_LENGTH = 8


class LoaderError(Exception):
    """El archivo no se puede cargar como tabla."""


def infer_schema(path: Path, config: EngineConfig) -> Schema:
    """Deduce el esquema de un CSV con cabecera.

    Raises:
        LoaderError: si el archivo no existe o no tiene cabecera.
    """
    names = read_header(path, config)
    kinds: list[FieldType] = [FieldType.INT] * len(names)
    lengths = [MINIMUM_STRING_LENGTH] * len(names)
    for row in _read_rows(path, config, len(names)):
        for position, raw in enumerate(row):
            kinds[position] = _widen(kinds[position], raw)
            lengths[position] = max(lengths[position], len(raw.encode()) + STRING_LENGTH_MARGIN)
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


def _widen(current: FieldType, raw: str) -> FieldType:
    """Un tipo solo puede ensancharse: INT → FLOAT → DATE/STRING."""
    if current is FieldType.STRING or not raw.strip():
        return current
    if current is FieldType.INT and _is_integer(raw):
        return FieldType.INT
    if current in (FieldType.INT, FieldType.FLOAT) and _is_float(raw):
        return FieldType.FLOAT
    if current is FieldType.DATE and _is_date(raw):
        return FieldType.DATE
    if current is FieldType.INT and _is_date(raw):
        return FieldType.DATE
    return FieldType.STRING


def _is_integer(raw: str) -> bool:
    text = raw.strip()
    return bool(text) and (text[1:] if text[0] in "+-" else text).isdigit()


def _is_float(raw: str) -> bool:
    try:
        float(raw)
    except ValueError:
        return False
    return True


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
        return text.lower() in ("1", "true", "t", "sí", "si")
    if kind is FieldType.DATE:
        return date.fromisoformat(text)
    return text
