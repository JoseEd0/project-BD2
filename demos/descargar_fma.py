"""Descarga unas pistas del Free Music Archive (FMA) para la demo de audio, con su género.

`fma_small` son 8 000 fragmentos de 30 s en MP3, mil por cada uno de ocho géneros, en un
`.zip` de 7,7 GB. El guion no lo baja entero: lee por rangos HTTP el índice del final del
`.zip` y después solo los bytes de las pistas elegidas. Lo mismo con `fma_metadata.zip`
(358 MB), del que lee solo `tracks.csv` (unos 20 MB comprimidos).

    .venv/bin/python -m demos.descargar_fma --salida data/datasets/fma_small
    .venv/bin/python -m demos.descargar_fma --salida data/datasets/fma_small --pistas 800

Las pistas se eligen con la semilla y en proporción a los géneros, así que salen las
mismas por género. Deja `pistas/<xxx>/<id>.mp3` y `canciones.csv`, con las columnas
`id, ruta, titulo, genero, artista`. Las pistas que ya están en disco no se vuelven a pedir.

Licencia: los metadatos del dataset son CC BY 4.0 (Defferrard et al., 2017); cada pista
conserva la licencia Creative Commons que eligió su artista, anotada en `tracks.csv`.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from benchmarks.multimedia.subsets import (
    DEFAULT_SEED,
    Columns,
    MediaItem,
    missing_files,
    partition,
    summary_table,
    write_items,
)
from benchmarks.remote import HttpRangeSource, RemoteFile, save_file

from errors import DomainError

DEFAULT_SMALL_URL = "https://os.unil.cloud.switch.ch/fma/fma_small.zip"
DEFAULT_METADATA_URL = "https://os.unil.cloud.switch.ch/fma/fma_metadata.zip"
TRACKS_MEMBER = "fma_metadata/tracks.csv"
AUDIO_MEMBER_PREFIX = "fma_small/"
SUBSET = "small"
DEFAULT_TRACKS = 2000
DEFAULT_THREADS = 4
TRACKS_DIRECTORY = "pistas"
SONGS_CSV = "canciones.csv"
COLUMNS = Columns(category="genero", group="artista")
TRACKS_ENCODING = "utf-8"
READ_AHEAD_BYTES = 4 * 1024 * 1024
TRACK_ID_DIGITS = 6
FOLDER_DIGITS = 3
# `tracks.csv` lleva tres filas de cabecera: la tabla (`track`, `artist`, `set`…), la
# columna dentro de ella y una tercera con solo `track_id`. Una columna se nombra con las
# dos primeras.
HEADER_ROWS = 3
SUBSET_COLUMN = ("set", "subset")
GENRE_COLUMN = ("track", "genre_top")
TITLE_COLUMN = ("track", "title")
ARTIST_COLUMN = ("artist", "name")
# Un miembro del `.zip` empieza con una cabecera local de 30 bytes más su nombre y su campo
# extra, que puede no medir lo mismo que en el índice central. Pedir este margen además del
# contenido trae la cabecera y el miembro en una sola petición.
LOCAL_HEADER_ALLOWANCE_BYTES = 1024
# Un fragmento de 30 s en MP3 pesa cientos de KB. `fma_small` trae unas pocas pistas de un
# par de KB, sin audio que decodificar: se dejan fuera antes de elegir.
MIN_TRACK_BYTES = 100 * 1024
TRACK_PATH = re.compile(r"[0-9]{3}/[0-9]{6}\.mp3")


class TracksError(DomainError):
    """`tracks.csv` o el `.zip` de audio no tienen la forma que publica el FMA."""


def read_tracks(rows: Iterator[list[str]]) -> Iterator[MediaItem]:
    """Las pistas de `fma_small` de las filas de `tracks.csv`, con su género como clase y
    su artista como grupo.

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
        yield MediaItem(
            path=f"{TRACKS_DIRECTORY}/{track_path(int(row[0]))}",
            title=" ".join(row[title].split()),
            category=row[genre],
            group=row[artist],
        )


def track_path(track_id: int) -> str:
    """Ruta de una pista dentro de `fma_small/`: `000/000002.mp3`."""
    name = f"{track_id:0{TRACK_ID_DIGITS}d}"
    return f"{name[:FOLDER_DIGITS]}/{name}.mp3"


def choose_tracks(items: Iterable[MediaItem], count: int, seed: int) -> tuple[MediaItem, ...]:
    """`count` pistas al azar con la semilla, con los géneros en proporción."""
    return partition(items, count, 0, seed).base


def read_metadata(url: str) -> list[MediaItem]:
    """Las pistas de `fma_small` según el `tracks.csv` del `.zip` remoto de metadatos."""
    with HttpRangeSource(url) as source:
        remote = io.BufferedReader(RemoteFile(source), buffer_size=READ_AHEAD_BYTES)
        with zipfile.ZipFile(remote) as archive, archive.open(TRACKS_MEMBER) as member:
            text = io.TextIOWrapper(member, encoding=TRACKS_ENCODING, newline="")
            return list(read_tracks(csv.reader(text)))


def audio_sizes(url: str) -> dict[str, int]:
    """Bytes de cada pista de `fma_small.zip` por su ruta (`000/000002.mp3`), según el
    índice del final del `.zip`."""
    with HttpRangeSource(url) as source, zipfile.ZipFile(RemoteFile(source)) as archive:
        return {
            info.filename.removeprefix(AUDIO_MEMBER_PREFIX): info.file_size
            for info in archive.infolist()
            if info.filename.startswith(AUDIO_MEMBER_PREFIX)
        }


def playable_tracks(items: Iterable[MediaItem], sizes: Mapping[str, int]) -> list[MediaItem]:
    """Las pistas que están en el `.zip` y pesan al menos `MIN_TRACK_BYTES`."""
    prefix = f"{TRACKS_DIRECTORY}/"
    return [
        item for item in items if sizes.get(item.path.removeprefix(prefix), 0) >= MIN_TRACK_BYTES
    ]


def extract_tracks(source: RemoteFile, relatives: Sequence[str], destination: Path) -> int:
    """Copia de `fma_small.zip` a `destination` las pistas `relatives` (`000/000002.mp3`),
    pidiendo cada una con una sola petición. Devuelve cuántas copió.

    Raises:
        TracksError: si alguna ruta no tiene la forma de `fma_small`.
        KeyError: si alguna no está en el `.zip`.
    """
    with zipfile.ZipFile(source) as archive:
        for relative in relatives:
            if not TRACK_PATH.fullmatch(relative):
                raise TracksError(f"ruta de pista inesperada {relative!r}")
            info = archive.getinfo(AUDIO_MEMBER_PREFIX + relative)
            source.prefetch(
                info.header_offset,
                info.header_offset + LOCAL_HEADER_ALLOWANCE_BYTES + info.compress_size,
            )
            save_file(destination / relative, archive.read(info))
    return len(relatives)


def download_tracks(url: str, relatives: Sequence[str], destination: Path, threads: int) -> int:
    """Reparte las pistas entre `threads` hilos, cada uno con su propia conexión al `.zip`."""
    batches = [relatives[start::threads] for start in range(threads)]

    def extract(batch: Sequence[str]) -> int:
        with HttpRangeSource(url) as source:
            return extract_tracks(RemoteFile(source), batch, destination)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        return sum(pool.map(extract, [batch for batch in batches if batch]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--salida", type=Path, required=True, help="carpeta de la demo")
    parser.add_argument("--pistas", type=int, default=DEFAULT_TRACKS, help="cuántas bajar")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--hilos", type=int, default=DEFAULT_THREADS, help="descargas a la vez")
    parser.add_argument("--url", default=DEFAULT_SMALL_URL, help="el .zip de fma_small")
    parser.add_argument("--metadatos", default=DEFAULT_METADATA_URL, help="el .zip de metadatos")
    arguments = parser.parse_args()
    output: Path = arguments.salida
    tracks = read_metadata(arguments.metadatos)
    playable = playable_tracks(tracks, audio_sizes(arguments.url))
    chosen = choose_tracks(playable, arguments.pistas, arguments.seed)
    missing = missing_files(chosen, output, TRACKS_DIRECTORY)
    copied = download_tracks(arguments.url, missing, output / TRACKS_DIRECTORY, arguments.hilos)
    write_items(output / SONGS_CSV, chosen, str(output), COLUMNS)
    print(summary_table({SONGS_CSV: chosen}))
    print(f"{len(tracks) - len(playable)} pistas de fma_small sin audio, descartadas")
    print(f"{len(chosen)} pistas en {output / SONGS_CSV}; {copied} bajadas ahora")
    print("Free Music Archive: metadatos CC BY 4.0; cada pista, con la licencia de su artista")


if __name__ == "__main__":
    main()
