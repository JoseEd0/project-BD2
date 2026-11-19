"""El catálogo completo en disco: las 8 000 pistas de `fma_small`, verificadas, y `catalog.csv`.

Repetible: si `catalog.csv` existe y cada pista que lista mide y suma lo que anota, no se va
a la red. Si falta o alguna pista no coincide, se vuelven a leer `tracks.csv` y el índice del
`.zip` y se bajan solo las pistas que faltan o están dañadas.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from songcheck.audio import UnreadableAudioError, decoded_duration
from songcheck.catalog import (
    CATALOG_FILE,
    TRACKS_DIRECTORY,
    CatalogSong,
    dataset_directory,
    is_intact,
    read_catalog,
    write_catalog,
)
from songcheck.fma.archive import AudioEntry, fetch_tracks, playable_tracks, read_audio_index
from songcheck.fma.tracks import Track, read_small_tracks

DEFAULT_AUDIO_URL = "https://os.unil.cloud.switch.ch/fma/fma_small.zip"
DEFAULT_METADATA_URL = "https://os.unil.cloud.switch.ch/fma/fma_metadata.zip"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FmaSources:
    """De dónde se leen el `.zip` de audio y el de metadatos."""

    audio_url: str = DEFAULT_AUDIO_URL
    metadata_url: str = DEFAULT_METADATA_URL


@dataclass(frozen=True, slots=True)
class CatalogReport:
    """Qué quedó en disco: pistas del catálogo, cuántas se bajaron ahora y con cuántos bytes,
    cuántas ya estaban y cuántas se dejaron fuera porque libsndfile no las decodifica
    enteras."""

    catalog: Path
    songs: int
    fetched: int
    fetched_bytes: int
    already_intact: int
    unreadable: int


def prepare_catalog(data_directory: Path, sources: FmaSources, threads: int) -> CatalogReport:
    """Deja el catálogo completo en `<data_directory>/fma_small/`.

    Raises:
        TracksError: si los metadatos o el `.zip` no tienen la forma del FMA, o una pista
            llega dañada.
        appkit.download.DownloadError: si el servidor deja de entregar los rangos.
    """
    directory = dataset_directory(data_directory)
    catalog = directory / CATALOG_FILE
    if catalog.is_file():
        songs = read_catalog(catalog)
        if all(is_intact(directory / song.path, song.size_bytes, song.crc32) for song in songs):
            logger.info("catálogo completo y verificado: %d pistas en %s", len(songs), catalog)
            return CatalogReport(catalog, len(songs), 0, 0, len(songs), 0)
    tracks = read_small_tracks(sources.metadata_url)
    index = read_audio_index(sources.audio_url)
    chosen = playable_tracks(tracks, index)
    logger.info("fma_small: %d pistas en tracks.csv, %d con audio", len(tracks), len(chosen))
    fetched = fetch_tracks(
        sources.audio_url,
        [index[track.track_id] for track in chosen],
        directory / TRACKS_DIRECTORY,
        threads,
    )
    songs = describe_tracks(chosen, index, directory, threads)
    write_catalog(catalog, songs)
    logger.info("catálogo escrito: %d pistas en %s", len(songs), catalog)
    return CatalogReport(
        catalog=catalog,
        songs=len(songs),
        fetched=fetched.fetched,
        fetched_bytes=fetched.fetched_bytes,
        already_intact=fetched.already_intact,
        unreadable=len(chosen) - len(songs),
    )


def describe_tracks(
    tracks: Sequence[Track], index: Mapping[int, AudioEntry], directory: Path, threads: int
) -> list[CatalogSong]:
    """Las filas del catálogo de pistas ya en disco, ordenadas por id, con la duración de
    decodificarlas enteras (en `threads` hilos: libsndfile suelta el GIL). Una pista que no
    se decodifica entera queda fuera, anotada en el log."""
    ordered = sorted(tracks, key=lambda item: item.track_id)
    relatives = [f"{TRACKS_DIRECTORY}/{index[track.track_id].relative_path}" for track in ordered]
    with ThreadPoolExecutor(max_workers=threads) as pool:
        durations = list(pool.map(_duration_or_none, (directory / path for path in relatives)))
    return [
        CatalogSong(
            track_id=track.track_id,
            title=track.title,
            artist=track.artist,
            genre=track.genre,
            duration_seconds=duration,
            path=relative,
            size_bytes=index[track.track_id].size_bytes,
            crc32=index[track.track_id].crc32,
        )
        for track, relative, duration in zip(ordered, relatives, durations, strict=True)
        if duration is not None
    ]


def _duration_or_none(path: Path) -> float | None:
    try:
        return decoded_duration(path)
    except UnreadableAudioError as error:
        logger.warning("pista fuera del catálogo: %s", error)
        return None
