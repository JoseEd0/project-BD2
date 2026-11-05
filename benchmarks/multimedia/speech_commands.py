"""Descarga Speech Commands v0.02 y arma los subconjuntos del experimento de audio.

Son 105 829 clips de un segundo, a 16 kHz, de 2 618 hablantes diciendo una de 35 palabras
en inglés. La palabra es la clase y el hablante, el grupo: las consultas son de hablantes
que no están en la base.

    .venv/bin/python -m benchmarks.multimedia.speech_commands --salida data/datasets/speech_commands

Deja en la carpeta de salida:

- `clips/<palabra>/<hablante>_nohash_<toma>.wav`: todos los clips, sin el ruido de fondo;
- `subconjuntos/<tamaño>/clips.csv` y `consultas.csv`, con las columnas
  `id, ruta, titulo, palabra, hablante` (ver `benchmarks.multimedia.subsets`).

El `.tar.gz` (2,43 GB) se baja con reanudación, se descomprime y se borra. Si los clips ya
están, no se vuelve a bajar.

Licencia de los datos: CC BY 4.0, Pete Warden (Google), «Speech Commands: A Dataset for
Limited-Vocabulary Speech Recognition» (2018).
"""

from __future__ import annotations

import argparse
import re
import tarfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from benchmarks.multimedia.subsets import (
    DEFAULT_QUERIES,
    DEFAULT_SEED,
    DEFAULT_SIZES,
    Columns,
    MediaItem,
    partition,
    summary_table,
    write_partition,
)
from benchmarks.remote import download, save_file

DEFAULT_URL = "http://download.tensorflow.org/data/speech_commands_v0.02.tar.gz"
ARCHIVE = "speech_commands_v0.02.tar.gz"
CLIPS_DIRECTORY = "clips"
CLIPS_CSV = "clips.csv"
# Se escribe al terminar de descomprimir: su presencia dice que `clips/` está completo.
COMPLETE_MARKER = ".completo"
COLUMNS = Columns(category="palabra", group="hablante")
# Un clip es `<palabra>/<hablante>_nohash_<toma>.wav`. La carpeta `_background_noise_`
# trae seis grabaciones largas de ruido, que no son clips, y no encaja en el patrón.
# Las rutas salen de un archivo descargado: solo las de esta forma se escriben a disco.
CLIP_PATH = re.compile(r"(?P<word>[a-z]+)/(?P<speaker>[0-9a-f]{8})_nohash_(?P<take>[0-9]+)\.wav")


def clip_item(relative: str) -> MediaItem | None:
    """El clip de una ruta `<palabra>/<archivo>.wav` del dataset; `None` si no es un clip."""
    match = CLIP_PATH.fullmatch(relative)
    if match is None:
        return None
    word, speaker, take = match["word"], match["speaker"], match["take"]
    return MediaItem(
        path=f"{CLIPS_DIRECTORY}/{relative}",
        title=f"{word} ({speaker}, toma {take})",
        category=word,
        group=speaker,
    )


def extract_clips(archive: Path, clips: Path) -> int:
    """Descomprime los clips del `.tar.gz` en `clips` y marca la carpeta como completa.
    Devuelve cuántos escribió."""
    written = 0
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            relative = str(PurePosixPath(member.name.removeprefix("./")))
            if not member.isfile() or clip_item(relative) is None:
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            save_file(clips / relative, source.read())
            written += 1
    (clips / COMPLETE_MARKER).touch()
    return written


def read_clips(clips: Path) -> Iterator[MediaItem]:
    """Los clips que hay en disco, en `clips/<palabra>/`."""
    for path in sorted(clips.glob("*/*.wav")):
        item = clip_item(path.relative_to(clips).as_posix())
        if item is not None:
            yield item


def build(
    output: Path, root: str, sizes: list[int], query_count: int, seed: int, url: str
) -> int:
    """Baja y descomprime lo que falte y escribe los CSV. Devuelve los clips escritos ahora
    (cero si ya estaban)."""
    clips = output / CLIPS_DIRECTORY
    written = 0
    if not (clips / COMPLETE_MARKER).exists():
        archive = download(url, output / ARCHIVE)
        written = extract_clips(archive, clips)
    split = partition(read_clips(clips), max(sizes), query_count, seed)
    write_partition(split, sizes, output, root, CLIPS_CSV, COLUMNS)
    print(summary_table(split.named_sets(sizes)))
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--salida", type=Path, required=True, help="carpeta del dataset")
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--consultas", type=int, default=DEFAULT_QUERIES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--url", default=DEFAULT_URL, help="el .tar.gz del dataset")
    parser.add_argument(
        "--conservar-archivo",
        action="store_true",
        help=f"no borrar {ARCHIVE} después de descomprimirlo",
    )
    arguments = parser.parse_args()
    output: Path = arguments.salida
    written = build(
        output, str(output), arguments.sizes, arguments.consultas, arguments.seed, arguments.url
    )
    if not arguments.conservar_archivo:
        (output / ARCHIVE).unlink(missing_ok=True)
    print(f"{written} clips descomprimidos ahora")
    print(f"Subconjuntos en {output}")
    print("Speech Commands v0.02, licencia CC BY 4.0: Pete Warden (Google)")


if __name__ == "__main__":
    main()
