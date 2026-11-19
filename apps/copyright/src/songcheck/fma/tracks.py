"""Las pistas de `fma_small` según `tracks.csv`, leído por rangos de `fma_metadata.zip`.

De los 358 MB del `.zip` de metadatos solo se leen el índice del final y `tracks.csv`, unos
20 MB comprimidos.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass

from appkit.ranges import HttpRangeSource, RangeReader
from songcheck.errors import TracksError

TRACKS_MEMBER = "fma_metadata/tracks.csv"
TRACKS_ENCODING = "utf-8"
SUBSET = "small"
READ_AHEAD_BYTES = 4 * 1024 * 1024
# `tracks.csv` lleva tres filas de cabecera: la tabla (`track`, `artist`, `set`…), la
# columna dentro de ella y una tercera con solo `track_id`. Una columna se nombra con las
# dos primeras.
HEADER_ROWS = 3
SUBSET_COLUMN = ("set", "subset")
GENRE_COLUMN = ("track", "genre_top")
TITLE_COLUMN = ("track", "title")
ARTIST_COLUMN = ("artist", "name")
TRACK_ID_COLUMN = 0


@dataclass(frozen=True, slots=True)
class Track:
    """Una pista de `fma_small` con lo que dice de ella `tracks.csv`."""

    track_id: int
    title: str
    artist: str
    genre: str


def read_tracks(rows: Iterator[list[str]]) -> Iterator[Track]:
    """Las pistas de `fma_small` de las filas de `tracks.csv`, con los espacios del título y
    del artista normalizados.

    Raises:
        TracksError: si a la cabecera le falta alguna de las columnas que se leen.
    """
    header = [next(rows) for _ in range(HEADER_ROWS)]
    columns = {name: index for index, name in enumerate(zip(header[0], header[1], strict=True))}
    wanted = (SUBSET_COLUMN, GENRE_COLUMN, TITLE_COLUMN, ARTIST_COLUMN)
    missing = [name for name in wanted if name not in columns]
    if missing:
        raise TracksError(f"a tracks.csv le faltan las columnas {missing}")
    subset, genre, title, artist = (columns[name] for name in wanted)
    for row in rows:
        if row[subset] != SUBSET:
            continue
        yield Track(
            track_id=int(row[TRACK_ID_COLUMN]),
            title=_collapsed(row[title]),
            artist=_collapsed(row[artist]),
            genre=row[genre],
        )


def read_small_tracks(metadata_url: str) -> list[Track]:
    """Las pistas de `fma_small` según el `tracks.csv` del `.zip` remoto de metadatos.

    Raises:
        TracksError: si `tracks.csv` no tiene las columnas que se leen.
        appkit.download.DownloadError: si el servidor no entrega los rangos pedidos.
    """
    with HttpRangeSource(metadata_url) as source:
        remote = io.BufferedReader(RangeReader(source), buffer_size=READ_AHEAD_BYTES)
        with zipfile.ZipFile(remote) as archive, archive.open(TRACKS_MEMBER) as member:
            text = io.TextIOWrapper(member, encoding=TRACKS_ENCODING, newline="")
            return list(read_tracks(csv.reader(text)))


def _collapsed(text: str) -> str:
    return " ".join(text.split())
