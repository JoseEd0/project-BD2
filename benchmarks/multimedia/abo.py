"""Descarga las fotos de catálogo de Amazon Berkeley Objects (ABO) y arma los subconjuntos.

ABO publica unas 398 000 fotos de producto de hasta 256 px y, aparte, la ficha de cada
producto con su tipo (`product_type`) y sus imágenes. Cada producto entra con su **foto
principal** (`main_image_id`), con el tipo como clase y su nombre como título:

    .venv/bin/python -m benchmarks.multimedia.abo --salida data/datasets/abo

Deja en la carpeta de salida:

- `imagenes/<xx>/<id>.jpg`: solo las fotos que usan los subconjuntos y las consultas (dos
  de las 398 212 del dataset son `.png`);
- `subconjuntos/<tamaño>/imagenes.csv` y `consultas.csv`, con las columnas
  `id, ruta, titulo, categoria` (ver `benchmarks.multimedia.subsets`);
- `abo-listings.tar` (83 MB, las fichas) e `images.csv.gz` (de qué archivo es cada foto).

El archivo de fotos (3,25 GB) se baja con reanudación, se le sacan las fotos elegidas y se
borra. Si ya están todas en disco, no se vuelve a bajar.

Licencia de los datos: CC BY 4.0, © Amazon.com (amazon-berkeley-objects.s3.amazonaws.com).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import tarfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.multimedia.subsets import (
    DEFAULT_QUERIES,
    DEFAULT_SEED,
    DEFAULT_SIZES,
    Columns,
    MediaItem,
    SubsetError,
    missing_files,
    partition,
    summary_table,
    write_partition,
)
from benchmarks.remote import download, save_file

DEFAULT_ARCHIVES_URL = "https://amazon-berkeley-objects.s3.amazonaws.com/archives"
IMAGES_ARCHIVE = "abo-images-small.tar"
LISTINGS_ARCHIVE = "abo-listings.tar"
IMAGES_METADATA = "images.csv.gz"
IMAGES_METADATA_MEMBER = "images/metadata/images.csv.gz"
IMAGES_MEMBER_PREFIX = "images/small/"
LISTINGS_MEMBER = re.compile(r"listings/metadata/listings_[0-9a-f]+\.json\.gz")
# Las rutas salen de archivos descargados: solo se aceptan las de la forma que publica ABO,
# para que ninguna escriba fuera de la carpeta de imágenes.
IMAGE_PATH = re.compile(r"[0-9a-f]{2}/[0-9a-f]+\.(jpg|png)")
IMAGES_DIRECTORY = "imagenes"
IMAGES_CSV = "imagenes.csv"
COLUMNS = Columns(category="categoria")
# Idiomas del título por preferencia; si el producto no trae ninguno, el primero que traiga.
TITLE_LANGUAGES = ("es", "en")
LANGUAGE_SEPARATOR = "_"


class CatalogError(SubsetError):
    """Las fichas o el índice de imágenes no tienen la forma que publica ABO."""


@dataclass(frozen=True, slots=True)
class Listing:
    """La ficha de un producto en una tienda: su tipo, su nombre y su foto principal."""

    item_id: str
    domain: str
    product_type: str
    title: str
    main_image_id: str


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    """Dónde empieza y cuánto mide el contenido de un archivo dentro del `.tar`."""

    offset: int
    size: int


def title_of(names: Sequence[Mapping[str, str]]) -> str:
    """El nombre del producto en el primer idioma de `TITLE_LANGUAGES` que tenga; si no,
    el primero que traiga; `""` si no trae ninguno."""
    for language in TITLE_LANGUAGES:
        for name in names:
            if name.get("language_tag", "").split(LANGUAGE_SEPARATOR)[0] == language:
                return name["value"]
    return names[0]["value"] if names else ""


def listing_of(record: Mapping[str, Any]) -> Listing | None:
    """La ficha de una línea del JSON de ABO; `None` si no tiene foto principal o tipo."""
    image = record.get("main_image_id")
    types = record.get("product_type") or []
    if not image or not types:
        return None
    return Listing(
        item_id=record["item_id"],
        domain=record.get("domain_name", ""),
        product_type=types[0]["value"],
        title=" ".join(title_of(record.get("item_name") or []).split()),
        main_image_id=image,
    )


def read_listings(archive: Path) -> Iterator[Listing]:
    """Las fichas con foto principal y tipo de los `listings_*.json.gz` del `.tar`."""
    with tarfile.open(archive) as tar:
        for member in tar:
            if not LISTINGS_MEMBER.fullmatch(member.name):
                continue
            compressed = tar.extractfile(member)
            if compressed is None:
                continue
            with gzip.open(compressed, "rt", encoding="utf-8") as lines:
                for line in lines:
                    listing = listing_of(json.loads(line))
                    if listing is not None:
                        yield listing


def read_image_paths(metadata: Path) -> dict[str, str]:
    """De `images.csv.gz`: la ruta de cada foto (`xx/yyyy.jpg`) por su `image_id`.

    Raises:
        CatalogError: si una ruta no tiene la forma que publica ABO.
    """
    paths: dict[str, str] = {}
    with gzip.open(metadata, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if not IMAGE_PATH.fullmatch(row["path"]):
                raise CatalogError(f"ruta de imagen inesperada {row['path']!r}")
            paths[row["image_id"]] = row["path"]
    return paths


def catalog_items(listings: Iterable[Listing], image_paths: Mapping[str, str]) -> list[MediaItem]:
    """Una foto principal por elemento, unida a su producto.

    Si varias fichas comparten la foto —el mismo producto en varias tiendas— manda la de
    menor `(item_id, dominio)`. Las fotos que no están en el índice de imágenes se dejan
    fuera. El grupo es la propia foto: ninguna consulta está repetida en la base.
    """
    chosen: dict[str, Listing] = {}
    for listing in listings:
        if listing.main_image_id not in image_paths:
            continue
        current = chosen.get(listing.main_image_id)
        if current is None or _listing_key(listing) < _listing_key(current):
            chosen[listing.main_image_id] = listing
    return [
        MediaItem(
            path=f"{IMAGES_DIRECTORY}/{image_paths[image]}",
            title=listing.title,
            category=listing.product_type,
            group=image,
        )
        for image, listing in sorted(chosen.items())
    ]


def index_archive(archive: Path, metadata_destination: Path) -> dict[str, ArchiveMember]:
    """Recorre el `.tar` de fotos una vez: copia el índice de imágenes a
    `metadata_destination` y devuelve dónde está cada foto, por su ruta `xx/yyyy.jpg`."""
    members: dict[str, ArchiveMember] = {}
    with tarfile.open(archive) as tar:
        for member in tar:
            if member.name == IMAGES_METADATA_MEMBER:
                _copy_member(tar, member, metadata_destination)
            elif member.isfile() and member.name.startswith(IMAGES_MEMBER_PREFIX):
                relative = member.name.removeprefix(IMAGES_MEMBER_PREFIX)
                members[relative] = ArchiveMember(member.offset_data, member.size)
    return members


def extract_images(
    archive: Path, members: Mapping[str, ArchiveMember], wanted: Iterable[str], images: Path
) -> int:
    """Copia del `.tar` a `images` las fotos `wanted` (rutas `xx/yyyy.jpg`). Devuelve
    cuántas copió.

    Raises:
        CatalogError: si alguna no está en el archivo.
    """
    copied = 0
    with archive.open("rb") as handle:
        for relative in sorted(wanted):
            member = members.get(relative)
            if member is None:
                raise CatalogError(f"la foto {relative} no está en {archive.name}")
            handle.seek(member.offset)
            save_file(images / relative, handle.read(member.size))
            copied += 1
    return copied


def _listing_key(listing: Listing) -> tuple[str, str]:
    return listing.item_id, listing.domain


def _copy_member(tar: tarfile.TarFile, member: tarfile.TarInfo, destination: Path) -> None:
    source = tar.extractfile(member)
    if source is None:
        raise CatalogError(f"{member.name} no es un archivo dentro del .tar")
    save_file(destination, source.read())


def build(
    output: Path, root: str, sizes: Sequence[int], query_count: int, seed: int, url: str
) -> tuple[list[MediaItem], int]:
    """Baja lo que falte, escribe los CSV y devuelve el catálogo y las fotos copiadas."""
    listings = download(f"{url}/{LISTINGS_ARCHIVE}", output / LISTINGS_ARCHIVE)
    metadata = output / IMAGES_METADATA
    archive = output / IMAGES_ARCHIVE
    members: dict[str, ArchiveMember] | None = None
    if not metadata.exists():
        download(f"{url}/{IMAGES_ARCHIVE}", archive)
        members = index_archive(archive, metadata)
    catalog = catalog_items(read_listings(listings), read_image_paths(metadata))
    split = partition(catalog, max(sizes), query_count, seed)
    missing = missing_files((*split.base, *split.queries), output, IMAGES_DIRECTORY)
    copied = 0
    if missing:
        download(f"{url}/{IMAGES_ARCHIVE}", archive)
        members = members or index_archive(archive, metadata)
        copied = extract_images(archive, members, missing, output / IMAGES_DIRECTORY)
    write_partition(split, sizes, output, root, IMAGES_CSV, COLUMNS)
    print(summary_table(split.named_sets(sizes)))
    return catalog, copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--salida", type=Path, required=True, help="carpeta del dataset")
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--consultas", type=int, default=DEFAULT_QUERIES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--url", default=DEFAULT_ARCHIVES_URL, help="carpeta remota de ABO")
    parser.add_argument(
        "--conservar-tar",
        action="store_true",
        help=f"no borrar {IMAGES_ARCHIVE} después de sacarle las fotos",
    )
    arguments = parser.parse_args()
    output: Path = arguments.salida
    catalog, copied = build(
        output, str(output), arguments.sizes, arguments.consultas, arguments.seed, arguments.url
    )
    if not arguments.conservar_tar:
        (output / IMAGES_ARCHIVE).unlink(missing_ok=True)
    print(f"{len(catalog)} fotos principales con producto; {copied} copiadas ahora")
    print(f"Subconjuntos en {output}")
    print("Amazon Berkeley Objects, licencia CC BY 4.0: © Amazon.com")


if __name__ == "__main__":
    main()
