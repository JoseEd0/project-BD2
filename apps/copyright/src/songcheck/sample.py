"""Un catálogo de juguete con audio generado, para probar el despliegue sin bajar FMA.

    PYTHONPATH=apps/copyright/src python3 -m songcheck.sample --data-dir data/datasets

Escribe `<data-dir>/fma_small/` como lo deja la descarga: unas pistas WAV con un barrido de
frecuencia distinto cada una y su `catalog.csv` completo, así que el trabajo de carga las
da por verificadas y no va a la red. Solo usa la biblioteca estándar: corre con el Python
del sistema, sin instalar nada (la CI del compose lo usa así).
"""

from __future__ import annotations

import argparse
import io
import math
import struct
import wave
import zlib
from pathlib import Path

from songcheck.catalog import (
    CATALOG_FILE,
    TRACKS_DIRECTORY,
    CatalogSong,
    dataset_directory,
    save_atomically,
    write_catalog,
)

SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2
CHANNELS = 1
AMPLITUDE = 8_000
LOWEST_HZ = 200.0
SWEEP_HZ = 1_800.0
DEFAULT_SONGS = 4
DEFAULT_SECONDS = 12.0
GENRES = ("Rock", "Folk", "Hip-Hop", "Electronic")
SAMPLE_ARTIST = "Muestra"


def sweep_wav(seconds: float, start_hz: float) -> bytes:
    """Un barrido de `start_hz` a `start_hz + SWEEP_HZ` en WAV mono de 16 bits: cada tramo
    suena distinto, así que cada uno tiene otros MFCC."""
    frames = round(seconds * SAMPLE_RATE)
    rate = SWEEP_HZ / seconds
    samples = (
        round(AMPLITUDE * math.sin(2 * math.pi * (start_hz * time + rate * time * time / 2)))
        for time in (index / SAMPLE_RATE for index in range(frames))
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(CHANNELS)
        audio.setsampwidth(SAMPLE_WIDTH)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(struct.pack(f"<{frames}h", *samples))
    return buffer.getvalue()


def write_sample(data_directory: Path, songs: int, seconds: float) -> Path:
    """Escribe el catálogo de juguete y devuelve la ruta de su `catalog.csv`."""
    directory = dataset_directory(data_directory)
    catalog = []
    for track_id in range(1, songs + 1):
        data = sweep_wav(seconds, LOWEST_HZ * track_id)
        relative = f"{TRACKS_DIRECTORY}/muestra/{track_id:06d}.wav"
        save_atomically(directory / relative, data)
        catalog.append(
            CatalogSong(
                track_id=track_id,
                title=f"Barrido {track_id}",
                artist=SAMPLE_ARTIST,
                genre=GENRES[track_id % len(GENRES)],
                duration_seconds=seconds,
                path=relative,
                size_bytes=len(data),
                crc32=zlib.crc32(data),
            )
        )
    path = directory / CATALOG_FILE
    write_catalog(path, catalog)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, required=True, help="carpeta de datasets")
    parser.add_argument("--songs", type=int, default=DEFAULT_SONGS)
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    arguments = parser.parse_args()
    path = write_sample(arguments.data_dir, arguments.songs, arguments.seconds)
    print(f"{arguments.songs} pistas de {arguments.seconds:g} s en {path}")


if __name__ == "__main__":
    main()
