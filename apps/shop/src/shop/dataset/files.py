"""Escritura atómica: un corte nunca deja con el nombre final un archivo a medias."""

from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import IO, Any

TEMPORARY_SUFFIX = ".tmp"
# `mkstemp` crea el archivo solo legible por su dueño; el gestor, en otro contenedor y con
# otro usuario, tiene que poder leerlo.
FILE_MODE = 0o644


def write_bytes_atomically(path: Path, data: bytes) -> None:
    """Escribe `data` en `path` de una vez, creando su carpeta."""
    _replace_with(path, "wb", lambda handle: handle.write(data))


def write_csv_atomically(
    path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]
) -> int:
    """Escribe un CSV con cabecera y devuelve cuántas filas escribió. `None` queda vacío."""
    written = 0

    def write(handle: IO[str]) -> None:
        nonlocal written
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
            written += 1

    _replace_with(path, "w", write)
    return written


def write_text_atomically(path: Path, text: str) -> None:
    _replace_with(path, "w", lambda handle: handle.write(text))


def _replace_with(path: Path, mode: str, write: Callable[[IO[Any]], object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=TEMPORARY_SUFFIX
    )
    try:
        encoding = None if "b" in mode else "utf-8"
        newline = None if "b" in mode else ""
        with os.fdopen(descriptor, mode, encoding=encoding, newline=newline) as handle:
            write(handle)
        Path(temporary).chmod(FILE_MODE)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
