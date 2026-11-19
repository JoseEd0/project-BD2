"""`catalog.csv` y la verificación de las pistas en disco."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from songcheck.catalog import (
    CatalogSong,
    checksum_of,
    is_intact,
    read_catalog,
    save_atomically,
    write_catalog,
)
from songcheck.errors import CatalogError

SONGS = (
    CatalogSong(2, "Food", "AWOL", "Hip-Hop", 29.98, "pistas/000/000002.mp3", 10, 7),
    CatalogSong(5, 'Coma, "comillas"', "Ñandú", "Folk", 30.0, "pistas/000/000005.mp3", 11, 8),
)


def test_the_catalog_round_trips(tmp_path: Path):
    path = tmp_path / "fma_small" / "catalog.csv"
    assert write_catalog(path, SONGS) == len(SONGS)
    assert read_catalog(path) == list(SONGS)
    assert [item.name for item in path.parent.iterdir()] == ["catalog.csv"]


def test_a_file_with_other_columns_is_not_a_catalog(tmp_path: Path):
    path = tmp_path / "catalog.csv"
    path.write_text("id,ruta\n1,x.mp3\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="columnas"):
        read_catalog(path)


def test_an_unreadable_row_names_its_line(tmp_path: Path):
    path = tmp_path / "catalog.csv"
    write_catalog(path, SONGS)
    path.write_text(path.read_text(encoding="utf-8").replace("29.98", "treinta"), encoding="utf-8")
    with pytest.raises(CatalogError, match="línea 2"):
        read_catalog(path)


def test_a_track_is_intact_only_with_its_size_and_crc(tmp_path: Path):
    path = tmp_path / "pistas" / "000" / "000002.mp3"
    data = b"ID3" * 1000
    save_atomically(path, data)
    assert checksum_of(path) == zlib.crc32(data)
    assert is_intact(path, len(data), zlib.crc32(data))
    assert not is_intact(path, len(data) + 1, zlib.crc32(data))
    assert not is_intact(path, len(data), zlib.crc32(data) ^ 1)
    assert not is_intact(tmp_path / "otra.mp3", len(data), zlib.crc32(data))
    assert [item.name for item in path.parent.iterdir()] == ["000002.mp3"]
