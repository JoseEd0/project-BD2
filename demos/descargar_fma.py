"""Descarga unas pistas del Free Music Archive (FMA) para la demo de audio, con su género.

`fma_small` son 8 000 fragmentos de 30 s en MP3, mil por cada uno de ocho géneros, en un
`.zip` de 7,7 GB. El guion no lo baja entero: lee por rangos HTTP el índice del final del
`.zip` y después solo los bytes de las pistas elegidas. Lo mismo con `fma_metadata.zip`
(358 MB), del que lee solo `tracks.csv` (unos 20 MB comprimidos).

    .venv/bin/python -m demos.descargar_fma --salida data/datasets/fma_small
    .venv/bin/python -m demos.descargar_fma --salida data/datasets/fma_small --pistas 800

Las pistas se eligen con la semilla y en proporción a los géneros, así que salen las
mismas por género. Deja `pistas/<xxx>/<id>.mp3` y `canciones.csv`, con las columnas
`id, ruta, titulo, genero, artista`. La lectura del FMA es la del servicio de copyright
(`songcheck.fma`, dueño del catálogo completo): misma carpeta de pistas, y una pista que ya
está en disco con el tamaño y el CRC-32 del `.zip` no se vuelve a pedir.

Licencia: los metadatos del dataset son CC BY 4.0 (Defferrard et al., 2017); cada pista
conserva la licencia Creative Commons que eligió su artista, anotada en `tracks.csv`.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from pathlib import Path

from benchmarks.multimedia.subsets import (
    DEFAULT_SEED,
    Columns,
    MediaItem,
    partition,
    summary_table,
    write_items,
)

from songcheck.catalog import TRACKS_DIRECTORY
from songcheck.fma.archive import (
    AudioEntry,
    fetch_tracks,
    playable_tracks,
    read_audio_index,
    track_path,
)
from songcheck.fma.prepare import DEFAULT_AUDIO_URL, DEFAULT_METADATA_URL
from songcheck.fma.tracks import Track, read_small_tracks

DEFAULT_TRACKS = 2000
DEFAULT_THREADS = 4
SONGS_CSV = "canciones.csv"
COLUMNS = Columns(category="genero", group="artista")


def media_item(track: Track) -> MediaItem:
    """La pista como elemento de los subconjuntos multimedia: el género es su clase y el
    artista el grupo que no se reparte."""
    return MediaItem(
        path=f"{TRACKS_DIRECTORY}/{track_path(track.track_id)}",
        title=track.title,
        category=track.genre,
        group=track.artist,
    )


def choose_tracks(items: Iterable[MediaItem], count: int, seed: int) -> tuple[MediaItem, ...]:
    """`count` pistas al azar con la semilla, con los géneros en proporción."""
    return partition(items, count, 0, seed).base


def entries_of(chosen: Iterable[MediaItem], index: Mapping[int, AudioEntry]) -> list[AudioEntry]:
    """Las entradas del `.zip` de las pistas elegidas."""
    by_path = {f"{TRACKS_DIRECTORY}/{entry.relative_path}": entry for entry in index.values()}
    return [by_path[item.path] for item in chosen]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--salida", type=Path, required=True, help="carpeta de la demo")
    parser.add_argument("--pistas", type=int, default=DEFAULT_TRACKS, help="cuántas bajar")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--hilos", type=int, default=DEFAULT_THREADS, help="descargas a la vez")
    parser.add_argument("--url", default=DEFAULT_AUDIO_URL, help="el .zip de fma_small")
    parser.add_argument("--metadatos", default=DEFAULT_METADATA_URL, help="el .zip de metadatos")
    arguments = parser.parse_args()
    output: Path = arguments.salida
    tracks = read_small_tracks(arguments.metadatos)
    index = read_audio_index(arguments.url)
    playable = [media_item(track) for track in playable_tracks(tracks, index)]
    chosen = choose_tracks(playable, arguments.pistas, arguments.seed)
    fetched = fetch_tracks(
        arguments.url, entries_of(chosen, index), output / TRACKS_DIRECTORY, arguments.hilos
    )
    write_items(output / SONGS_CSV, chosen, str(output), COLUMNS)
    print(summary_table({SONGS_CSV: chosen}))
    print(f"{len(tracks) - len(playable)} pistas de fma_small sin audio, descartadas")
    print(f"{len(chosen)} pistas en {output / SONGS_CSV}; {fetched.fetched} bajadas ahora")
    print("Free Music Archive: metadatos CC BY 4.0; cada pista, con la licencia de su artista")


if __name__ == "__main__":
    main()
