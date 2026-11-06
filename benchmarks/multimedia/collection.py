"""Las colecciones del experimento: la base de un tamaño y las consultas de un dataset.

Se leen de los CSV que escriben `benchmarks.multimedia.abo` y
`benchmarks.multimedia.speech_commands`; el experimento no arma subconjuntos por su
cuenta, para que no haya dos sitios donde se decide qué se mide.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from benchmarks.multimedia import abo, speech_commands
from benchmarks.multimedia.names import AUDIO, IMAGES
from benchmarks.multimedia.subsets import (
    PATH_COLUMN,
    QUERIES_CSV,
    SUBSETS_DIRECTORY,
    TITLE_COLUMN,
    SubsetError,
)
from config import DEFAULT_CSV_ENCODING


class MissingCollectionError(SubsetError):
    """Falta el CSV de un subconjunto o de las consultas: hay que bajar el dataset antes."""


@dataclass(frozen=True, slots=True)
class MediaFile:
    """Un archivo de la colección: su ruta tal como está en el CSV y su clase."""

    path: str
    title: str
    label: str


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    """Dónde está un dataset y cómo se llaman en su CSV el archivo y la columna de la clase.

    `name` es el que llevan las medidas: `imágenes` o `audio`.
    """

    name: str
    directory: Path
    csv_name: str
    label_column: str
    download_command: str

    def base(self, size: int) -> tuple[MediaFile, ...]:
        """Los `size` archivos del subconjunto de ese tamaño, en el orden del CSV.

        Raises:
            MissingCollectionError: si el subconjunto no está escrito.
        """
        return self._read(self.directory / SUBSETS_DIRECTORY / str(size) / self.csv_name, None)

    def queries(self, limit: int | None) -> tuple[MediaFile, ...]:
        """Las primeras `limit` consultas (todas si es `None`), que no están en la base.

        Raises:
            MissingCollectionError: si el CSV de las consultas no está escrito.
        """
        return self._read(self.directory / QUERIES_CSV, limit)

    def _read(self, path: Path, limit: int | None) -> tuple[MediaFile, ...]:
        if not path.is_file():
            raise MissingCollectionError(
                f"no está {path}: baja el dataset con `{self.download_command}`"
            )
        return tuple(islice(self._rows(path), limit))

    def _rows(self, path: Path) -> Iterator[MediaFile]:
        with path.open(newline="", encoding=DEFAULT_CSV_ENCODING) as handle:
            for row in csv.DictReader(handle):
                yield MediaFile(row[PATH_COLUMN], row[TITLE_COLUMN], row[self.label_column])


def images_dataset(directory: Path) -> DatasetSpec:
    """Las fotos de ABO, con su `categoria` como clase."""
    return DatasetSpec(
        IMAGES,
        directory,
        abo.IMAGES_CSV,
        abo.COLUMNS.category,
        f"python -m benchmarks.multimedia.abo --salida {directory}",
    )


def audio_dataset(directory: Path) -> DatasetSpec:
    """Los clips de Speech Commands, con su `palabra` como clase."""
    return DatasetSpec(
        AUDIO,
        directory,
        speech_commands.CLIPS_CSV,
        speech_commands.COLUMNS.category,
        f"python -m benchmarks.multimedia.speech_commands --salida {directory}",
    )
