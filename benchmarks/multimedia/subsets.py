"""Subconjuntos anidados y reproducibles de un dataset multimedia, más sus consultas.

Lo comparten las imágenes (ABO), el audio del experimento (Speech Commands) y el de la
demo (FMA). Las reglas:

- **Consultas fuera de la base.** Se apartan grupos enteros —un hablante, un artista, una
  imagen— elegidos al azar con la semilla hasta juntar `QUERY_POOL_FACTOR` veces las
  consultas pedidas, y de ellos se toman las consultas en proporción a las clases. Ningún
  elemento de un grupo apartado entra en la base, así que una consulta no se encuentra a
  sí misma ni a otra toma de su mismo hablante.
- **Clases en proporción y anidados.** La base se ordena de modo que **cualquier prefijo**
  tenga las clases en la proporción del total: el de 1 000 es el principio del de 10 000,
  y este, el del de 100 000.

Misma semilla y mismos elementos, en cualquier orden: misma partición.
"""

from __future__ import annotations

import csv
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from benchmarks.common import QUERIES_STREAM, ROWS_STREAM, random_stream
from config import DEFAULT_CSV_DELIMITER, DEFAULT_CSV_ENCODING
from errors import DomainError

SUBSETS_DIRECTORY = "subconjuntos"
QUERIES_CSV = "consultas.csv"
QUERIES_NAME = "consultas"
DEFAULT_SIZES = (1000, 10_000, 100_000)
DEFAULT_QUERIES = 500
DEFAULT_SEED = 20261105
GROUPS_STREAM = "grupos"
# Se apartan grupos hasta juntar cuatro veces las consultas: con justo las necesarias, unos
# pocos hablantes prolíficos las darían todas y faltarían palabras; con margen, las
# consultas se eligen en proporción a las clases entre decenas de grupos.
QUERY_POOL_FACTOR = 4
# Cada elemento ocupa el centro de su tramo dentro de su clase: el k-ésimo de n cae en
# (k + 1/2) / n. Así un prefijo de la base lleva de cada clase lo que le toca redondeado.
STRATUM_CENTER = 0.5
ID_COLUMN = "id"
PATH_COLUMN = "ruta"
TITLE_COLUMN = "titulo"
LARGEST_SHARE_SHOWN = 3


class SubsetError(DomainError):
    """Los parámetros piden una partición que el dataset no puede dar."""


@dataclass(frozen=True, slots=True)
class MediaItem:
    """Un archivo del dataset con su clase y el grupo que no se puede repartir entre la
    base y las consultas.

    `path` es relativo a la carpeta del dataset y usa `/` como separador.
    """

    path: str
    title: str
    category: str
    group: str


@dataclass(frozen=True, slots=True)
class Partition:
    """La base en el orden de los prefijos y las consultas, apartadas de ella."""

    base: tuple[MediaItem, ...]
    queries: tuple[MediaItem, ...]

    def subset(self, size: int) -> tuple[MediaItem, ...]:
        """Los `size` primeros de la base: el subconjunto de ese tamaño.

        Raises:
            SubsetError: si la base no tiene tantos elementos.
        """
        if size > len(self.base):
            raise SubsetError(f"la base tiene {len(self.base)} elementos y se piden {size}")
        return self.base[:size]

    def named_sets(self, sizes: Sequence[int]) -> dict[str, tuple[MediaItem, ...]]:
        """Cada subconjunto por su tamaño y, si las hay, las consultas."""
        named = {str(size): self.subset(size) for size in sizes}
        if self.queries:
            named[QUERIES_NAME] = self.queries
        return named


@dataclass(frozen=True, slots=True)
class Columns:
    """Cómo se llaman en el CSV la clase y, si se escribe, el grupo."""

    category: str
    group: str | None = None

    @property
    def header(self) -> tuple[str, ...]:
        named = (ID_COLUMN, PATH_COLUMN, TITLE_COLUMN, self.category)
        return named if self.group is None else (*named, self.group)


def partition(
    items: Iterable[MediaItem], largest: int, query_count: int, seed: int
) -> Partition:
    """Aparta `query_count` consultas y ordena una base de `largest` elementos.

    Raises:
        SubsetError: si un parámetro no es válido, o si apartados los grupos de las
            consultas no quedan `largest` elementos para la base.
    """
    _validate(largest, query_count)
    pool = list(items)
    held_out = _held_out_groups(pool, query_count * QUERY_POOL_FACTOR, seed)
    rest = [item for item in pool if item.group not in held_out]
    if len(rest) < largest:
        raise SubsetError(
            f"apartadas las consultas quedan {len(rest)} elementos y la base pide {largest}"
        )
    candidates = [item for item in pool if item.group in held_out]
    queries = stratified_order(candidates, random_stream(seed, QUERIES_STREAM))[:query_count]
    base = stratified_order(rest, random_stream(seed, ROWS_STREAM))[:largest]
    return Partition(tuple(base), tuple(queries))


def stratified_order(items: Iterable[MediaItem], generator: random.Random) -> list[MediaItem]:
    """Los elementos en un orden cuyos prefijos tienen las clases en proporción.

    Dentro de cada clase el orden es al azar; entre clases, intercalado según el lugar
    relativo de cada elemento en la suya. El resultado no depende del orden de entrada.
    """
    by_category: dict[str, list[MediaItem]] = defaultdict(list)
    for item in items:
        by_category[item.category].append(item)
    keyed: list[tuple[float, str, MediaItem]] = []
    for category in sorted(by_category):
        members = sorted(by_category[category], key=lambda item: item.path)
        generator.shuffle(members)
        keyed.extend(
            ((rank + STRATUM_CENTER) / len(members), category, item)
            for rank, item in enumerate(members)
        )
    keyed.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _, _, item in keyed]


def write_items(path: Path, items: Sequence[MediaItem], root: str, columns: Columns) -> int:
    """Escribe los elementos en un CSV que el gestor carga, numerados desde 1.

    La ruta de cada archivo es `root` seguido de su ruta en el dataset: con `root` relativo
    a la raíz del repositorio, la ruta lo es también. Devuelve las filas escritas.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding=DEFAULT_CSV_ENCODING) as handle:
        writer = csv.writer(handle, delimiter=DEFAULT_CSV_DELIMITER)
        writer.writerow(columns.header)
        for identifier, item in enumerate(items, start=1):
            row: tuple[object, ...] = (
                identifier,
                str(PurePosixPath(root) / item.path),
                item.title,
                item.category,
            )
            writer.writerow(row if columns.group is None else (*row, item.group))
    return len(items)


def missing_files(items: Iterable[MediaItem], output: Path, directory: str) -> list[str]:
    """Las rutas, relativas a `output/directory`, de los archivos de `items` que aún no
    están en disco."""
    prefix = f"{directory}/"
    return [
        item.path.removeprefix(prefix) for item in items if not (output / item.path).exists()
    ]


def write_partition(
    split: Partition,
    sizes: Sequence[int],
    output: Path,
    root: str,
    file_name: str,
    columns: Columns,
) -> None:
    """Escribe `<output>/subconjuntos/<tamaño>/<file_name>` por tamaño y, si hay
    consultas, `<output>/consultas.csv`."""
    for size in sizes:
        target = output / SUBSETS_DIRECTORY / str(size) / file_name
        write_items(target, split.subset(size), root, columns)
    if split.queries:
        write_items(output / QUERIES_CSV, split.queries, root, columns)


def summary_table(named: Mapping[str, Sequence[MediaItem]]) -> str:
    """Tabla Markdown con una fila por conjunto: elementos, grupos, clases presentes y la
    parte de las clases más grandes."""
    lines = [
        "| Conjunto | Elementos | Grupos | Clases | Clases más grandes |",
        "|---|---:|---:|---:|---|",
    ]
    lines.extend(_summary_row(name, items) for name, items in named.items())
    return "\n".join(lines)


def _summary_row(name: str, items: Sequence[MediaItem]) -> str:
    counts = Counter(item.category for item in items)
    groups = len({item.group for item in items})
    largest = ", ".join(
        f"{category} {count / len(items):.1%}"
        for category, count in counts.most_common(LARGEST_SHARE_SHOWN)
    )
    return f"| {name} | {len(items)} | {groups} | {len(counts)} | {largest} |"


def _held_out_groups(items: Sequence[MediaItem], wanted: int, seed: int) -> frozenset[str]:
    """Grupos al azar, enteros, hasta sumar al menos `wanted` elementos."""
    sizes = Counter(item.group for item in items)
    groups = sorted(sizes)
    random_stream(seed, GROUPS_STREAM).shuffle(groups)
    chosen: set[str] = set()
    held = 0
    for group in groups:
        if held >= wanted:
            break
        chosen.add(group)
        held += sizes[group]
    if held < wanted:
        raise SubsetError(f"el dataset tiene {held} elementos y se apartan {wanted}")
    return frozenset(chosen)


def _validate(largest: int, query_count: int) -> None:
    if largest <= 0:
        raise SubsetError(f"el tamaño tiene que ser positivo y es {largest}")
    if query_count < 0:
        raise SubsetError(f"las consultas no pueden ser negativas y son {query_count}")
