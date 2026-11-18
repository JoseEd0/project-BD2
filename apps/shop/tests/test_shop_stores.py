"""Tiendas de Lima elegidas de los lugares de OpenStreetMap e inventario con semilla."""

from collections import Counter
from pathlib import Path

import pytest

from shop.dataset.errors import PlacesFormatError
from shop.dataset.stores import (
    STORE_KINDS,
    InventorySettings,
    Store,
    generate_inventory,
    read_places,
    select_stores,
)

PLACES = """id,nombre,rubro,ubicacion
30,Saga Falabella,tienda por departamentos,"POINT(-12.091000, -77.023000)"
10,Jockey Plaza,centro comercial,"POINT(-12.085000, -76.977000)"
20,Interbank,banco,"POINT(-12.104020, -77.052737)"
40,  ,tienda de cómputo,"POINT(-12.050000, -77.040000)"
50,Compu  Palace,tienda de cómputo,"POINT(-12.060000, -77.035000)"
10,Jockey Plaza (otra etiqueta),tienda,"POINT(-12.085000, -76.977000)"
"""
SETTINGS = InventorySettings(seed=7, products_per_store=3, max_quantity=5)


def places_file(tmp_path: Path, text: str = PLACES) -> Path:
    path = tmp_path / "lugares.csv"
    path.write_text(text, encoding="utf-8")
    return path


def store(identifier: int) -> Store:
    return Store(identifier, f"Tienda {identifier}", "tienda", -12.0, -77.0)


def catalog(size: int) -> dict[str, int]:
    return {f"P{index:03d}": 0 for index in range(size)}


def test_stores_are_the_named_places_of_the_chosen_kinds(tmp_path: Path) -> None:
    stores = select_stores(read_places(places_file(tmp_path)), STORE_KINDS)
    assert [(item.store_id, item.name, item.kind) for item in stores] == [
        (10, "Jockey Plaza", "centro comercial"),
        (30, "Saga Falabella", "tienda por departamentos"),
        (50, "Compu Palace", "tienda de cómputo"),
    ]
    assert stores[0].location == "POINT(-12.085000, -76.977000)"


def test_a_places_file_without_the_columns_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PlacesFormatError, match="columnas"):
        list(read_places(places_file(tmp_path, "id,name\n1,x\n")))


def test_a_location_that_is_not_a_point_is_rejected(tmp_path: Path) -> None:
    text = 'id,nombre,rubro,ubicacion\n1,X,tienda,"-12.0, -77.0"\n'
    with pytest.raises(PlacesFormatError, match="ubicación"):
        list(read_places(places_file(tmp_path, text)))


def test_the_same_seed_gives_the_same_inventory() -> None:
    stores = [store(1), store(2)]
    first = list(generate_inventory(stores, catalog(20), SETTINGS))
    assert list(generate_inventory(stores, catalog(20), SETTINGS)) == first
    other_seed = InventorySettings(seed=8, products_per_store=3, max_quantity=5)
    assert list(generate_inventory(stores, catalog(20), other_seed)) != first


def test_removing_a_store_leaves_the_others_untouched() -> None:
    both = list(generate_inventory([store(1), store(2)], catalog(20), SETTINGS))
    alone = list(generate_inventory([store(2)], catalog(20), SETTINGS))
    assert [entry for entry in both if entry.store_id == 2] == alone


def test_each_store_stocks_distinct_products_in_range() -> None:
    entries = list(generate_inventory([store(1)], catalog(20), SETTINGS))
    assert len(entries) == SETTINGS.products_per_store
    assert len({entry.product_id for entry in entries}) == len(entries)
    assert all(1 <= entry.quantity <= SETTINGS.max_quantity for entry in entries)
    assert [entry.product_id for entry in entries] == sorted(entry.product_id for entry in entries)


def test_a_small_catalog_is_stocked_whole() -> None:
    entries = list(generate_inventory([store(1)], catalog(2), SETTINGS))
    assert {entry.product_id for entry in entries} == {"P000", "P001"}


def test_popular_products_are_in_more_stores() -> None:
    popularity = catalog(50) | {"P000": 500}
    stores = [store(identifier) for identifier in range(100)]
    counts = Counter(entry.product_id for entry in generate_inventory(stores, popularity, SETTINGS))
    assert counts["P000"] > max(count for product, count in counts.items() if product != "P000")
