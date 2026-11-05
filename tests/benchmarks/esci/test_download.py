"""El conversor del Shopping Queries Dataset (ESCI), sin tocar la red.

Los parquet se construyen aquí mismo con las columnas del dataset original. Leerlos pide
`pyarrow`, que es un extra opcional (`.[datasets]`): sin él, esos tests se omiten.
"""

import csv
from pathlib import Path
from typing import Any

import pytest

from benchmarks.esci import corpus as esci_corpus
from benchmarks.esci import download as esci
from config import EngineConfig
from query.engine import Engine


def product_row(identifier: str, locale: str, **fields: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "product_id": identifier,
        "product_title": f"Producto {identifier}",
        "product_description": None,
        "product_bullet_point": None,
        "product_brand": None,
        "product_color": None,
        "product_locale": locale,
    }
    row.update(fields)
    return row


def example_row(
    query_id: int, query: str, product: str, label: str, locale: str = "es"
) -> dict[str, Any]:
    return {
        "example_id": 0,
        "query": query,
        "query_id": query_id,
        "product_id": product,
        "product_locale": locale,
        "esci_label": label,
        "small_version": 0,
        "large_version": 1,
        "split": "train",
    }


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> Path:
    pyarrow = pytest.importorskip("pyarrow")
    parquet = pytest.importorskip("pyarrow.parquet")
    parquet.write_table(pyarrow.Table.from_pylist(rows), path)
    return path


def read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


def test_html_is_stripped_and_entities_and_spaces_normalized():
    fragment = (
        "<p>Cable USB&nbsp;<b>3.0</b></p><br/>de 2&nbsp;m &amp; funda\n\n<ul><li>negro</li></ul>"
    )
    assert esci.plain_text(fragment) == "Cable USB 3.0 de 2 m & funda negro"


def test_a_tag_separates_words_and_a_lone_less_than_sign_is_kept():
    assert esci.plain_text("uno<br>dos") == "uno dos"
    assert esci.plain_text("talla < 40") == "talla < 40"


def test_missing_or_empty_html_is_empty_text():
    assert esci.plain_text(None) == ""
    assert esci.plain_text("") == ""
    assert esci.plain_text("   <br/>  ") == ""


def test_the_document_is_title_bullets_description_and_brand():
    product = esci.product_of(
        product_row(
            "B01",
            "es",
            product_title="Zapatillas  Running",
            product_bullet_point="Suela de goma\nTranspirables",
            product_description="<p>Para <i>correr</i></p>",
            product_brand="Kalenji",
        )
    )
    assert product == esci_corpus.Product(
        "B01",
        "Zapatillas Running",
        "Kalenji",
        "Zapatillas Running Suela de goma Transpirables Para correr Kalenji",
    )


def test_missing_parts_leave_no_extra_spaces():
    product = esci.product_of(product_row("B02", "es", product_title="Taza"))
    assert product == esci_corpus.Product("B02", "Taza", "", "Taza")


def test_words_are_what_whitespace_separates():
    assert esci_corpus.word_count("funda") == 1
    assert esci_corpus.word_count("funda iphone 12") == 3
    assert esci_corpus.word_count("funda iphone-12 pro max, negra") == 5


def test_the_corpus_keeps_only_spain_and_known_products(tmp_path: Path):
    products = write_parquet(
        tmp_path / "productos.parquet",
        [
            product_row("B1", "es", product_description="<b>Rojo</b>", product_brand="Acme"),
            product_row("B2", "us"),
            product_row("B3", "es", product_title='Cable "HDMI", 2 m'),
        ],
    )
    examples = write_parquet(
        tmp_path / "ejemplos.parquet",
        [
            example_row(20, "cable  hdmi", "B3", "E"),
            example_row(20, "cable  hdmi", "B1", "I"),
            example_row(10, "camiseta roja algodón manga corta", "B1", "E"),
            example_row(10, "camiseta roja algodón manga corta", "B1", "S"),
            example_row(10, "camiseta roja algodón manga corta", "B9", "E"),
            example_row(30, "red shirt", "B2", "E", locale="us"),
            example_row(40, "solo fuera", "B9", "C"),
        ],
    )
    summary = esci.build_corpus(products, examples, tmp_path / "corpus")
    assert summary == esci.CorpusSummary(products=2, queries=2, judgments=3, discarded_judgments=3)
    assert read_csv(tmp_path / "corpus" / "productos.csv") == [
        ["id", "titulo", "marca", "texto"],
        ["B1", "Producto B1", "Acme", "Producto B1 Rojo Acme"],
        ["B3", 'Cable "HDMI", 2 m', "", 'Cable "HDMI", 2 m'],
    ]
    assert read_csv(tmp_path / "corpus" / "consultas.csv") == [
        ["query_id", "consulta", "palabras"],
        ["10", "camiseta roja algodón manga corta", "5"],
        ["20", "cable hdmi", "2"],
    ]
    assert read_csv(tmp_path / "corpus" / "juicios.csv") == [
        ["query_id", "product_id", "etiqueta"],
        ["10", "B1", "E"],
        ["20", "B1", "I"],
        ["20", "B3", "E"],
    ]


def test_an_unknown_label_is_rejected(tmp_path: Path):
    products = write_parquet(tmp_path / "productos.parquet", [product_row("B1", "es")])
    examples = write_parquet(tmp_path / "ejemplos.parquet", [example_row(1, "taza", "B1", "X")])
    with pytest.raises(esci.CorpusDownloadError):
        esci.build_corpus(products, examples, tmp_path / "corpus")


def test_an_empty_catalog_gives_csvs_with_only_the_header(tmp_path: Path):
    products = write_parquet(tmp_path / "productos.parquet", [product_row("B1", "us")])
    examples = write_parquet(
        tmp_path / "ejemplos.parquet", [example_row(1, "mug", "B1", "E", "us")]
    )
    assert esci.build_corpus(products, examples, tmp_path / "corpus") == esci.CorpusSummary(
        0, 0, 0, 0
    )
    assert read_csv(tmp_path / "corpus" / "juicios.csv") == [["query_id", "product_id", "etiqueta"]]


def test_the_products_csv_is_loaded_by_the_engine_as_it_is(config: EngineConfig):
    path = config.data_directory / "productos.csv"
    rows = [
        ("B1", 'Cable "HDMI", 2 m', "Ugreen", 'Cable "HDMI", 2 m Ugreen'),
        ("B2", "Taza", "", "Taza"),
    ]
    assert esci_corpus.write_rows(path, esci_corpus.PRODUCT_HEADER, rows) == 2
    with Engine(config) as engine:
        engine.execute(f"CREATE TABLE productos FROM FILE '{path}'")
        loaded = engine.execute("SELECT id, titulo, marca, texto FROM productos ORDER BY id")
    assert loaded.rows == (
        ("B1", 'Cable "HDMI", 2 m', "Ugreen", 'Cable "HDMI", 2 m Ugreen'),
        ("B2", "Taza", None, "Taza"),
    )
