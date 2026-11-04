"""Formato en disco del corpus ESCI en español: los tres CSV que comparten la descarga, los
subconjuntos y el experimento de texto.

Un solo lugar define los nombres de archivo, las columnas, cómo se cuentan las palabras de
una consulta y qué etiqueta cuenta como relevante.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from config import DEFAULT_CSV_DELIMITER, DEFAULT_CSV_ENCODING
from errors import DomainError

PRODUCTS_CSV = "productos.csv"
QUERIES_CSV = "consultas.csv"
JUDGMENTS_CSV = "juicios.csv"
PRODUCT_HEADER = ("id", "titulo", "marca", "texto")
QUERY_HEADER = ("query_id", "consulta", "palabras")
JUDGMENT_HEADER = ("query_id", "product_id", "etiqueta")
LABELS = frozenset({"E", "S", "C", "I"})
RELEVANT_LABEL = "E"


class CorpusError(DomainError):
    """El corpus no tiene la forma esperada o no puede dar lo que se le pide."""


@dataclass(frozen=True, slots=True)
class Product:
    """Un producto del catálogo, con el texto que se indexa."""

    identifier: str
    title: str
    brand: str
    text: str


@dataclass(frozen=True, slots=True)
class Query:
    """Una consulta real de la tienda y cuántas palabras tiene (ver `word_count`)."""

    identifier: int
    text: str
    words: int


@dataclass(frozen=True, slots=True)
class Judgment:
    """La etiqueta E, S, C o I que un anotador le dio a un producto para una consulta."""

    query_id: int
    product_id: str
    label: str

    @property
    def relevant(self) -> bool:
        return self.label == RELEVANT_LABEL


def word_count(text: str) -> int:
    """Las palabras de una consulta son los trozos que separan los espacios: «funda
    iphone-12» tiene dos."""
    return len(text.split())


def read_products(path: Path) -> Iterator[Product]:
    """Los productos de un `productos.csv`, en el orden del archivo y sin cargarlo entero."""
    for row in read_rows(path):
        yield Product(row["id"], row["titulo"], row["marca"], row["texto"])


def read_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding=DEFAULT_CSV_ENCODING) as handle:
        yield from csv.DictReader(handle, delimiter=DEFAULT_CSV_DELIMITER)


def write_rows(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> int:
    """Escribe un CSV con cabecera en el formato que carga el gestor. Devuelve las filas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", newline="", encoding=DEFAULT_CSV_ENCODING) as handle:
        writer = csv.writer(handle, delimiter=DEFAULT_CSV_DELIMITER)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
            written += 1
    return written
