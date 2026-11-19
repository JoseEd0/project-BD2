"""El catálogo de canciones originales en disco: las pistas de `fma_small` y su `catalog.csv`.

```
<data-dir>/fma_small/
  pistas/<xxx>/<id>.mp3   el audio, tal como sale del .zip del FMA
  catalog.csv             una fila por pista: metadatos, duración, bytes y CRC-32
```

`catalog.csv` es lo último que escribe la descarga, y solo cuando todas las pistas que lista
están en disco y verificadas: si existe, el catálogo está completo. El tamaño y el CRC-32 de
cada pista son los del índice del `.zip`, así que comprobar que una pista sigue sana no
necesita la red. Las rutas son relativas a la carpeta del dataset; quien las manda al gestor
les antepone la carpeta tal como la ve el gestor.
"""

from __future__ import annotations

import csv
import os
import zlib
from collections.abc import Iterable
from dataclasses import astuple, dataclass, fields
from pathlib import Path

from songcheck.errors import CatalogError

DATASET_DIRECTORY = "fma_small"
TRACKS_DIRECTORY = "pistas"
CATALOG_FILE = "catalog.csv"
CATALOG_ENCODING = "utf-8"
STAGING_SUFFIX = ".parcial"
CHECKSUM_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class CatalogSong:
    """Una pista del catálogo. `path` es relativa a la carpeta del dataset."""

    track_id: int
    title: str
    artist: str
    genre: str
    duration_seconds: float
    path: str
    size_bytes: int
    crc32: int


CATALOG_COLUMNS = tuple(field.name for field in fields(CatalogSong))


def dataset_directory(data_directory: Path) -> Path:
    """La carpeta del dataset dentro de la carpeta de datasets."""
    return data_directory / DATASET_DIRECTORY


def write_catalog(path: Path, songs: Iterable[CatalogSong]) -> int:
    """Escribe el catálogo de una vez: hasta el final, con otro nombre. Devuelve las filas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + STAGING_SUFFIX)
    written = 0
    with staged.open("w", encoding=CATALOG_ENCODING, newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CATALOG_COLUMNS)
        for song in songs:
            writer.writerow(astuple(song))
            written += 1
    staged.replace(path)
    return written


def read_catalog(path: Path) -> list[CatalogSong]:
    """Las pistas de `catalog.csv`, en el orden del archivo.

    Raises:
        CatalogError: si el archivo no tiene las columnas del catálogo o una fila no se lee.
    """
    with path.open(encoding=CATALOG_ENCODING, newline="") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader, ()))
        if header != CATALOG_COLUMNS:
            raise CatalogError(f"{path} no tiene las columnas {CATALOG_COLUMNS}: {header}")
        return [_song(row, path, line) for line, row in enumerate(reader, start=2)]


def is_intact(path: Path, size_bytes: int, crc32: int) -> bool:
    """Si `path` existe con exactamente esos bytes y ese CRC-32."""
    if not path.is_file() or path.stat().st_size != size_bytes:
        return False
    return checksum_of(path) == crc32


def checksum_of(path: Path) -> int:
    """El CRC-32 de `path`, el que anota el índice de un `.zip`, leído por trozos."""
    checksum = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHECKSUM_CHUNK_BYTES):
            checksum = zlib.crc32(chunk, checksum)
    return checksum


def save_atomically(path: Path, data: bytes) -> None:
    """Guarda `data` en `path` con otro nombre y lo renombra al terminar: un corte nunca
    deja un archivo a medias con el nombre final."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + STAGING_SUFFIX)
    with staged.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    staged.replace(path)


def _song(row: list[str], path: Path, line: int) -> CatalogSong:
    try:
        track_id, title, artist, genre, duration, relative, size, crc32 = row
        return CatalogSong(
            int(track_id), title, artist, genre, float(duration), relative, int(size), int(crc32)
        )
    except ValueError as error:
        raise CatalogError(f"{path}, línea {line}: {error}") from error
