"""Amazon Berkeley Objects sin tocar la red: los `.tar` se arman aquí con el formato del
dataset y se sirven como `file://`."""

import csv
import gzip
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
from benchmarks.multimedia.subsets import QUERIES_CSV, SUBSETS_DIRECTORY

from benchmarks.multimedia import abo

SEED = 3


def record(item: str, image: str | None, kind: str | None, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"item_id": item, "domain_name": "amazon.com", **fields}
    if image is not None:
        row["main_image_id"] = image
    if kind is not None:
        row["product_type"] = [{"value": kind}]
    return row


def name(value: str, language: str) -> dict[str, str]:
    return {"language_tag": language, "value": value}


def add_member(tar: tarfile.TarFile, member: str, data: bytes) -> None:
    info = tarfile.TarInfo(member)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def json_lines(records: list[dict[str, Any]]) -> bytes:
    return gzip.compress("".join(json.dumps(item) + "\n" for item in records).encode())


def images_csv(paths: dict[str, str]) -> bytes:
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(("image_id", "height", "width", "path"))
    for image, path in paths.items():
        writer.writerow((image, 256, 256, path))
    return gzip.compress(text.getvalue().encode())


def test_the_title_prefers_spanish_then_english_then_the_first():
    assert abo.title_of([name("Shoe", "en_US"), name("Zapato", "es_MX")]) == "Zapato"
    assert abo.title_of([name("Schuh", "de_DE"), name("Shoe", "en_IN")]) == "Shoe"
    assert abo.title_of([name("Schuh", "de_DE"), name("Chaussure", "fr_FR")]) == "Schuh"
    assert abo.title_of([{"value": "Sin idioma"}]) == "Sin idioma"
    assert abo.title_of([]) == ""


def test_a_listing_without_main_image_or_type_is_skipped():
    assert abo.listing_of(record("B1", None, "SHOES")) is None
    assert abo.listing_of(record("B1", "img", None)) is None
    listing = abo.listing_of(record("B1", "img", "SHOES", item_name=[name(" Zapato   rojo ", "es_US")]))
    assert listing == abo.Listing("B1", "amazon.com", "SHOES", "Zapato rojo", "img")


def test_each_main_image_joins_one_product_and_unknown_images_are_dropped():
    listings = [
        abo.Listing("B2", "amazon.de", "CHAIR", "Silla", "shared"),
        abo.Listing("B1", "amazon.com", "SOFA", "Sofá", "shared"),
        abo.Listing("B3", "amazon.com", "LAMP", "Lámpara", "lamp"),
        abo.Listing("B4", "amazon.com", "RUG", "Alfombra", "missing"),
    ]
    paths = {"shared": "aa/aa01.jpg", "lamp": "bb/bb02.png"}
    items = abo.catalog_items(listings, paths)
    assert [(item.path, item.title, item.category, item.group) for item in items] == [
        ("imagenes/bb/bb02.png", "Lámpara", "LAMP", "lamp"),
        ("imagenes/aa/aa01.jpg", "Sofá", "SOFA", "shared"),
    ]


def test_listings_are_read_from_the_json_files_of_the_tar(tmp_path: Path):
    archive = tmp_path / abo.LISTINGS_ARCHIVE
    with tarfile.open(archive, "w") as tar:
        add_member(tar, "listings/README.md", b"# ABO")
        add_member(tar, "listings/metadata/listings_0.json.gz", json_lines([record("B1", "a", "SHOES")]))
        add_member(tar, "listings/metadata/listings_f.json.gz", json_lines([record("B2", None, "SHOES")]))
    assert [item.item_id for item in abo.read_listings(archive)] == ["B1"]


def test_an_image_path_outside_the_published_layout_is_rejected(tmp_path: Path):
    metadata = tmp_path / abo.IMAGES_METADATA
    metadata.write_bytes(images_csv({"ok": "0a/0a1b.jpg"}))
    assert abo.read_image_paths(metadata) == {"ok": "0a/0a1b.jpg"}
    metadata.write_bytes(images_csv({"malo": "../../etc/passwd"}))
    with pytest.raises(abo.CatalogError):
        abo.read_image_paths(metadata)


def test_only_the_wanted_images_are_copied_out_of_the_tar(tmp_path: Path):
    archive = tmp_path / abo.IMAGES_ARCHIVE
    with tarfile.open(archive, "w") as tar:
        add_member(tar, "images/small/0a/0a01.jpg", b"JPEG uno")
        add_member(tar, "images/small/0b/0b02.jpg", b"JPEG dos")
        add_member(tar, abo.IMAGES_METADATA_MEMBER, images_csv({"x": "0a/0a01.jpg"}))
    metadata = tmp_path / "copia" / abo.IMAGES_METADATA
    members = abo.index_archive(archive, metadata)
    assert set(members) == {"0a/0a01.jpg", "0b/0b02.jpg"}
    assert abo.read_image_paths(metadata) == {"x": "0a/0a01.jpg"}
    images = tmp_path / "imagenes"
    assert abo.extract_images(archive, members, ["0b/0b02.jpg"], images) == 1
    assert (images / "0b" / "0b02.jpg").read_bytes() == b"JPEG dos"
    assert not (images / "0a").exists()
    with pytest.raises(abo.CatalogError):
        abo.extract_images(archive, members, ["0c/0c03.jpg"], images)


def publish_dataset(directory: Path, products: int) -> None:
    """Los dos `.tar` de ABO con `products` productos de tres tipos, uno por foto."""
    kinds = ("SHOES", "CHAIR", "CELLULAR_PHONE_CASE")
    records, paths, images = [], {}, {}
    for number in range(products):
        image = f"img{number:03d}"
        path = f"{number % 256:02x}/{number:08x}.jpg"
        records.append(record(f"B{number:03d}", image, kinds[number % 3], item_name=[name(f"Producto {number}", "es_ES")]))
        paths[image] = path
        images[path] = f"JPEG {number}".encode()
    directory.mkdir(parents=True, exist_ok=True)
    with tarfile.open(directory / abo.LISTINGS_ARCHIVE, "w") as tar:
        add_member(tar, "listings/metadata/listings_0.json.gz", json_lines(records))
    with tarfile.open(directory / abo.IMAGES_ARCHIVE, "w") as tar:
        for path, data in images.items():
            add_member(tar, f"images/small/{path}", data)
        add_member(tar, abo.IMAGES_METADATA_MEMBER, images_csv(paths))


def test_build_leaves_nested_subsets_and_queries_and_does_not_need_the_tar_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    remote = tmp_path / "remoto"
    publish_dataset(remote, 60)
    output = tmp_path / "abo"
    catalog, copied = abo.build(output, "data/datasets/abo", [6, 30], 3, SEED, remote.as_uri())
    assert len(catalog) == 60
    queries = list(csv.DictReader((output / QUERIES_CSV).open(encoding="utf-8")))
    largest = list(csv.DictReader((output / SUBSETS_DIRECTORY / "30" / abo.IMAGES_CSV).open(encoding="utf-8")))
    smallest = list(csv.DictReader((output / SUBSETS_DIRECTORY / "6" / abo.IMAGES_CSV).open(encoding="utf-8")))
    assert copied == len(queries) + len(largest) == 33
    assert smallest == largest[:6]
    assert {row["categoria"] for row in smallest} == {"SHOES", "CHAIR", "CELLULAR_PHONE_CASE"}
    assert {row["ruta"] for row in queries}.isdisjoint(row["ruta"] for row in largest)
    assert len(list((output / abo.IMAGES_DIRECTORY).rglob("*.jpg"))) == 33
    first = largest[0]["ruta"].removeprefix("data/datasets/abo/")
    assert (output / first).read_bytes().startswith(b"JPEG")
    assert "| consultas | 3 |" in capsys.readouterr().out

    (output / abo.IMAGES_ARCHIVE).unlink()
    (remote / abo.IMAGES_ARCHIVE).unlink()
    assert abo.build(output, "data/datasets/abo", [6, 30], 3, SEED, remote.as_uri())[1] == 0
