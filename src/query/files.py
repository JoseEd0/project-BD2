"""Archivos que nombra el usuario: dónde se le permite leer.

`CREATE TABLE ... FROM FILE` recibe una ruta escrita en el SQL. Sin límite, cualquiera que
pueda mandar una consulta al API leería cualquier archivo del servidor que parezca un CSV.
Toda ruta que llega del usuario pasa por aquí y solo se acepta si, ya resueltos los
enlaces simbólicos y los `..`, cae dentro del directorio de datos o de uno de los
directorios de importación de la configuración.
"""

from __future__ import annotations

from pathlib import Path

from config import EngineConfig
from query.loader import LoaderError


class ForbiddenFileError(LoaderError):
    """La ruta pedida queda fuera de los directorios de los que se permite leer."""


def resolve_user_file(text: str, config: EngineConfig) -> Path:
    """Ruta absoluta del archivo que nombra el usuario.

    Raises:
        ForbiddenFileError: si queda fuera de `data_directory` y de `import_directories`.
    """
    path = Path(text).resolve()
    allowed = [config.data_directory, *config.import_directories]
    if any(path.is_relative_to(root.resolve()) for root in allowed):
        return path
    places = ", ".join(str(root) for root in allowed)
    raise ForbiddenFileError(
        f"'{text}' está fuera de los directorios de los que se permite cargar: {places}"
    )
