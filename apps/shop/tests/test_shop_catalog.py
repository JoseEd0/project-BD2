"""Fichas de Amazon Reviews 2023 aplanadas: precios, imágenes y categorías raras."""

from pathlib import Path

import pytest
from apps.shop.tests.shop_support import write_jsonl

from shop.dataset.catalog import (
    LARGE_VARIANT_SIDE,
    department_of,
    leaf_category_of,
    parse_product,
    price_of,
    read_products,
)

SMALL_SIDE = 256
LARGE = "https://m.media-amazon.com/images/I/a._AC_.jpg"
HI_RES = "https://m.media-amazon.com/images/I/a._AC_SL1500_.jpg"


def record(**fields: object) -> dict[str, object]:
    return {"parent_asin": "B000000001", **fields}


def test_a_complete_record_is_flattened() -> None:
    product = parse_product(
        record(
            title="  Pearl  Export\nDrum Set ",
            store="Pearl",
            categories=["Musical Instruments", "Drums & Percussion", "Drum Sets"],
            price=699.99,
            average_rating=4.2,
            rating_number=22,
            features=["Pedal", "Hardware"],
            description=["Best seller.", "Lacquer finish."],
            images=[{"variant": "MAIN", "large": LARGE, "hi_res": HI_RES}],
        ),
        SMALL_SIDE,
    )
    assert product.title == "Pearl Export Drum Set"
    assert product.brand == "Pearl"
    assert product.department == "Drums & Percussion"
    assert product.category == "Drum Sets"
    assert product.category_path == "Musical Instruments > Drums & Percussion > Drum Sets"
    assert product.price == 699.99
    assert (product.average_rating, product.rating_count) == (4.2, 22)
    assert product.features == "Pedal | Hardware"
    assert product.description == "Best seller. Lacquer finish."
    assert product.image_url == LARGE


def test_missing_fields_stay_empty() -> None:
    product = parse_product(record(), SMALL_SIDE)
    assert (product.title, product.brand, product.category_path) == ("", "", "")
    assert product.price is None and product.average_rating is None
    assert product.rating_count == 0
    assert product.image_url is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (19.5, 19.5),
        (12, 12.0),
        (0, None),
        (True, None),
        ("23.99", 23.99),
        ("$1,299.00", 1299.0),
        ("from 23.99", None),
        ("—", None),
    ],
)
def test_prices(value: object, expected: float | None) -> None:
    assert price_of(value) == expected


def test_the_brand_falls_back_to_the_details() -> None:
    product = parse_product(record(store=None, details={"Brand": "Fender"}), SMALL_SIDE)
    assert product.brand == "Fender"


@pytest.mark.parametrize(
    ("categories", "department", "leaf"),
    [
        ([], "", ""),
        (["Musical Instruments"], "", ""),
        (["Musical Instruments", "Guitars"], "Guitars", "Guitars"),
        (["Instrument Accessories", "Drum & Percussion Accessories"], "Instrument Accessories",
         "Drum & Percussion Accessories"),
        (["Musical Instruments", "Prime Card Bonus"], "Prime Card Bonus", "Prime Card Bonus"),
    ],
)
def test_weird_categories(categories: list[str], department: str, leaf: str) -> None:
    assert department_of(categories) == department
    assert leaf_category_of(categories) == leaf


def test_blank_and_non_text_categories_are_dropped() -> None:
    product = parse_product(record(categories=["Musical Instruments", " ", 7, "Guitars"]), 1)
    assert product.category_path == "Musical Instruments > Guitars"


def test_repeated_paragraphs_are_kept_once() -> None:
    product = parse_product(record(description=["Loud.", "Loud.", " Loud. ", "Light."]), 1)
    assert product.description == "Loud. Light."


def test_the_main_image_wins_over_the_first() -> None:
    images = [
        {"variant": "PT01", "large": "https://x/other.jpg"},
        {"variant": "MAIN", "large": LARGE},
    ]
    assert parse_product(record(images=images), SMALL_SIDE).image_url == LARGE


def test_without_a_main_image_the_first_is_used() -> None:
    images = [{"variant": "PT01", "large": LARGE}, {"variant": "PT02", "large": HI_RES}]
    assert parse_product(record(images=images), SMALL_SIDE).image_url == LARGE


def test_a_larger_side_prefers_the_high_resolution_variant() -> None:
    images = [{"variant": "MAIN", "large": LARGE, "hi_res": HI_RES}]
    assert parse_product(record(images=images), LARGE_VARIANT_SIDE + 1).image_url == HI_RES


def test_a_missing_variant_falls_back_to_the_other() -> None:
    images = [{"variant": "MAIN", "large": LARGE, "hi_res": None}]
    assert parse_product(record(images=images), LARGE_VARIANT_SIDE + 1).image_url == LARGE


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/a.jpg", None])
def test_only_http_image_urls_are_accepted(url: str | None) -> None:
    images = [{"variant": "MAIN", "large": url, "hi_res": None}]
    assert parse_product(record(images=images), SMALL_SIDE).image_url is None


def test_empty_or_malformed_image_lists_give_no_url() -> None:
    assert parse_product(record(images=[]), SMALL_SIDE).image_url is None
    assert parse_product(record(images="none"), SMALL_SIDE).image_url is None
    assert parse_product(record(images=["x"]), SMALL_SIDE).image_url is None


def test_read_products_keeps_the_wanted_ones_sorted_and_the_first_duplicate(
    tmp_path: Path,
) -> None:
    path = write_jsonl(
        tmp_path / "meta.jsonl",
        [
            {"parent_asin": "B2", "title": "second"},
            {"parent_asin": "B9", "title": "not in the core"},
            {"parent_asin": "B1", "title": "first"},
            {"parent_asin": "B2", "title": "duplicate"},
        ],
    )
    products = read_products(path, {"B1", "B2"}, SMALL_SIDE)
    assert [(product.product_id, product.title) for product in products] == [
        ("B1", "first"),
        ("B2", "second"),
    ]
