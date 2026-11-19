"""Cuántas filas tiene que tener cada tabla, según el manifiesto del dataset (#79)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shop.catalog.rows import (
    INTERACTIONS_CSV,
    INVENTORY_CSV,
    PRODUCTS_CSV,
    STORES_CSV,
    USERS_CSV,
)

MANIFEST_NAME = "manifest.json"
TABLES_FIELD = "tables"
ROWS_FIELD = "rows"


class ManifestError(Exception):
    """Falta el manifiesto del dataset, o no dice cuántas filas tiene un CSV."""


@dataclass(frozen=True, slots=True)
class ExpectedRows:
    stores: int
    products: int
    users: int
    purchases: int
    inventory: int


def expected_rows(dataset: Path) -> ExpectedRows:
    """Las filas de cada CSV del dataset, como las anotó `python -m shop.dataset`.

    Raises:
        ManifestError: si no está el manifiesto o le falta un CSV.
    """
    path = dataset / MANIFEST_NAME
    if not path.is_file():
        raise ManifestError(f"falta {path}: prepara el dataset con `python -m shop.dataset`")
    tables = json.loads(path.read_text(encoding="utf-8")).get(TABLES_FIELD, {})
    try:
        return ExpectedRows(
            stores=tables[STORES_CSV][ROWS_FIELD],
            products=tables[PRODUCTS_CSV][ROWS_FIELD],
            users=tables[USERS_CSV][ROWS_FIELD],
            purchases=tables[INTERACTIONS_CSV][ROWS_FIELD],
            inventory=tables[INVENTORY_CSV][ROWS_FIELD],
        )
    except KeyError as missing:
        raise ManifestError(f"{path} no dice cuántas filas tiene {missing}") from None
