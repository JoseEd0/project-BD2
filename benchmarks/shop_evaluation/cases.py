"""Qué hay que acertar: la compra de validación y la de prueba de cada usuario, leídas del
`interactions.csv` del dataset, y lo que se sabe del entrenamiento (cuántas compras tiene cada
usuario y cada producto)."""

from __future__ import annotations

import csv
import random
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from shop.catalog.rows import CSV_ENCODING, INTERACTIONS_CSV, PRODUCTS_CSV
from shop.dataset.sources import Split

USER_COLUMN = "user_id"
PRODUCT_COLUMN = "product_id"
SPLIT_COLUMN = "split"


class IncompleteSplitError(ValueError):
    """Un usuario no tiene exactamente una compra de validación y una de prueba."""


@dataclass(frozen=True, slots=True)
class HeldOut:
    """Las dos compras que se dejan fuera de un usuario: la penúltima y la última."""

    validation: str
    test: str


@dataclass(frozen=True, slots=True)
class EvaluationSplit:
    """Lo que la evaluación sabe del dataset.

    Attributes:
        held_out: las compras que hay que acertar, por usuario.
        train_purchases: compras de entrenamiento distintas de cada usuario.
        popularity: compras de entrenamiento de cada producto.
        catalog: los ids de todos los productos, ordenados.
    """

    held_out: dict[str, HeldOut]
    train_purchases: dict[str, int]
    popularity: Counter[str]
    catalog: tuple[str, ...]

    def most_popular(self) -> tuple[str, ...]:
        """El catálogo de más a menos comprado en el entrenamiento; un empate, por id."""
        return tuple(sorted(self.catalog, key=lambda product: (-self.popularity[product], product)))


def read_split(dataset: Path) -> EvaluationSplit:
    """La partición del dataset preparado en `dataset` (la carpeta de `shop.dataset`).

    Raises:
        IncompleteSplitError: si algún usuario no tiene su compra de validación y de prueba.
    """
    held: dict[Split, dict[str, str]] = {Split.VALID: {}, Split.TEST: {}}
    train: dict[str, set[str]] = {}
    popularity: Counter[str] = Counter()
    for row in _records(dataset / INTERACTIONS_CSV):
        user, product, split = row[USER_COLUMN], row[PRODUCT_COLUMN], Split(row[SPLIT_COLUMN])
        if split is Split.TRAIN:
            train.setdefault(user, set()).add(product)
            popularity[product] += 1
            continue
        if user in held[split]:
            raise IncompleteSplitError(f"{user} tiene más de una compra en {split}")
        held[split][user] = product
    catalog = tuple(sorted(row[PRODUCT_COLUMN] for row in _records(dataset / PRODUCTS_CSV)))
    return EvaluationSplit(
        _held_out(held),
        {user: len(products) for user, products in train.items()},
        popularity,
        catalog,
    )


def sample_users(users: Sequence[str], size: int, seed: int) -> list[str]:
    """`size` usuarios al azar con la semilla `seed`, en orden de id; todos si son menos.

    Con la misma semilla, una muestra mayor contiene a la menor: ampliarla reutiliza los
    candidatos ya pedidos."""
    shuffled = sorted(users)
    random.Random(seed).shuffle(shuffled)
    return sorted(shuffled[:size])


def _held_out(held: dict[Split, dict[str, str]]) -> dict[str, HeldOut]:
    validation, test = held[Split.VALID], held[Split.TEST]
    missing = sorted(set(validation) ^ set(test))
    if missing:
        raise IncompleteSplitError(f"{missing[0]} no tiene compra de validación y de prueba")
    return {user: HeldOut(validation[user], test[user]) for user in sorted(validation)}


def _records(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding=CSV_ENCODING, newline="") as handle:
        yield from csv.DictReader(handle)
