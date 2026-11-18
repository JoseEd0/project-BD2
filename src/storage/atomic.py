"""Escribir un archivo entero o no tocarlo.

Lo que el motor reescribe de una vez —el manifiesto del índice invertido, un índice de
vectores, el modelo de un almacén de características— se escribe en un archivo aparte, se
lleva al disco y se renombra encima del anterior. Renombrar dentro de un directorio es
atómico, así que un corte a mitad deja el archivo viejo entero, nunca uno a medias.

Una marca —un archivo vacío cuya sola existencia dice algo, como que un índice quedó
viejo— se crea y se borra llevando al disco también el directorio: sin eso, un corte
justo después podría perder la entrada del directorio aunque el archivo esté escrito.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

STAGED_SUFFIX = ".tmp"


def staged_path(path: Path) -> Path:
    """Donde se escribe lo que después reemplaza a `path`."""
    return path.with_name(path.name + STAGED_SUFFIX)


def write_atomically(path: Path, write: Callable[[BinaryIO], object]) -> None:
    """Escribe `path` con `write` y lo reemplaza de una vez. Si `write` falla, `path`
    queda como estaba y no queda ningún archivo a medias."""
    staged = staged_path(path)
    try:
        with staged.open("wb") as file:
            write(file)
            file.flush()
            os.fsync(file.fileno())
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def mark_durably(path: Path) -> None:
    """Crea `path` vacío y no vuelve hasta que el archivo y su entrada están en el disco."""
    write_atomically(path, lambda _: None)
    _sync_directory(path.parent)


def remove_durably(path: Path) -> None:
    """Borra `path`, si está, y no vuelve hasta que el borrado está en el disco."""
    path.unlink(missing_ok=True)
    _sync_directory(path.parent)


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
