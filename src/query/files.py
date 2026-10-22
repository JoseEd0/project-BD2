"""Archivos que nombra el usuario: dónde se le permite leer.

`CREATE TABLE ... FROM FILE` recibe una ruta escrita en el SQL. Sin límite, cualquiera que
pueda mandar una consulta al API leería cualquier archivo del servidor que parezca un CSV.
Toda ruta que llega del usuario pasa por aquí y solo se acepta si, ya resueltos los
enlaces simbólicos y los `..`, cae dentro del directorio de datos o de uno de los
directorios de importación de la configuración.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO

from config import EngineConfig
from query.loader import LoaderError

LOCK_FILE_NAME = ".motor.lock"

if sys.platform == "win32":
    import msvcrt

    def _try_lock(handle: IO[bytes]) -> bool:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(handle: IO[bytes]) -> None:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: IO[bytes]) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ForbiddenFileError(LoaderError):
    """La ruta pedida queda fuera de los directorios de los que se permite leer."""


def resolve_user_file(text: str, config: EngineConfig) -> Path:
    """Ruta absoluta del archivo que nombra el usuario.

    Raises:
        ForbiddenFileError: si queda fuera de `data_directory` y de `import_directories`.
        LoaderError: si no es una ruta válida o no nombra un archivo.
    """
    try:
        path = Path(text).resolve()
    except (OSError, ValueError) as error:
        raise LoaderError(f"'{text}' no es una ruta válida: {error}") from error
    allowed = [config.data_directory, *config.import_directories]
    if any(path.is_relative_to(root.resolve()) for root in allowed):
        if not path.is_file():
            raise LoaderError(f"'{text}' no es un archivo")
        return path
    places = ", ".join(str(root) for root in allowed)
    raise ForbiddenFileError(
        f"'{text}' está fuera de los directorios de los que se permite cargar: {places}"
    )


class DirectoryLock:
    """Candado exclusivo sobre un directorio de datos: lo abre un solo motor a la vez.

    Dos motores sobre el mismo directorio —el API y un guion que lo puebla— se pisarían el
    catálogo: cada uno lo reescribe con las tablas que conoce. Es un candado del sistema
    operativo sobre un archivo del directorio, que se suelta solo si el proceso termina:
    una caída no deja el directorio bloqueado.
    """

    def __init__(self, directory: Path) -> None:
        self._path = directory / LOCK_FILE_NAME
        self._handle: IO[bytes] | None = None

    def acquire(self) -> bool:
        """Toma el candado; `False` si otro motor ya lo tiene."""
        handle = self._path.open("a+b")
        if not _try_lock(handle):
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        _unlock(self._handle)
        self._handle.close()
        self._handle = None
