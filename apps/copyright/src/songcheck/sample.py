"""Un catálogo de juguete con audio generado, para probar el despliegue sin bajar FMA.

    PYTHONPATH=apps/copyright/src python3 -m songcheck.sample --data-dir data/datasets

Escribe `<data-dir>/fma_small/` como lo deja la descarga: unas pistas WAV, cada una una
melodía al azar con su propia semilla, y su `catalog.csv` completo, así que el trabajo de
carga las da por verificadas y no va a la red. Solo usa la biblioteca estándar: corre con el
Python del sistema, sin instalar nada (la CI del compose lo usa así).

Las pistas tienen que sonar distinto entre sí **y en cada instante**, o la detección no
tendría qué alinear: un barrido de frecuencia que empieza más arriba es el mismo barrido
desplazado en el tiempo, y una pista lo contendría entera. Notas al azar de una escala de
tres octavas sí: dos ventanas de 5 s, de la misma pista o de otra, casi nunca tienen las
mismas notas.
"""

from __future__ import annotations

import argparse
import io
import math
import random
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
LOWEST_HZ = 220.0
SEMITONES = 36
SEMITONES_PER_OCTAVE = 12
NOTE_SECONDS = 0.25
DEFAULT_SONGS = 4
DEFAULT_SECONDS = 12.0
GENRES = ("Rock", "Folk", "Hip-Hop", "Electronic")
SAMPLE_ARTIST = "Muestra"


def melody_wav(seconds: float, seed: int) -> bytes:
    """Notas de `NOTE_SECONDS` al azar (con `seed`) entre `LOWEST_HZ` y tres octavas más
    arriba, en WAV mono de 16 bits."""
    frames = round(seconds * SAMPLE_RATE)
    note_frames = round(NOTE_SECONDS * SAMPLE_RATE)
    generator = random.Random(seed)
    pitches = [
        LOWEST_HZ * 2 ** (generator.randrange(SEMITONES) / SEMITONES_PER_OCTAVE)
        for _ in range(math.ceil(frames / note_frames))
    ]
    samples = (
        round(
            AMPLITUDE * math.sin(2 * math.pi * pitches[index // note_frames] * index / SAMPLE_RATE)
        )
        for index in range(frames)
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
        data = melody_wav(seconds, track_id)
        relative = f"{TRACKS_DIRECTORY}/muestra/{track_id:06d}.wav"
        save_atomically(directory / relative, data)
        catalog.append(
            CatalogSong(
                track_id=track_id,
                title=f"Melodía {track_id}",
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
