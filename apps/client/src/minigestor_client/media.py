"""El archivo de una consulta multimedia, tal como se sube al gestor."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

FALLBACK_CONTENT_TYPE = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class MediaFile:
    """Una imagen o un audio en memoria. El gestor reconoce el tipo por la extensión de
    `name`; el tamaño lo limita su `max_upload_bytes`, así que tenerlo entero no pesa."""

    name: str
    content: bytes
    content_type: str = FALLBACK_CONTENT_TYPE

    @classmethod
    def from_path(cls, path: Path) -> MediaFile:
        """Lee el archivo en `path`.

        Raises:
            OSError: si no se puede leer.
        """
        content_type = mimetypes.guess_type(path.name)[0] or FALLBACK_CONTENT_TYPE
        return cls(path.name, path.read_bytes(), content_type)


def as_media_file(file: MediaFile | Path) -> MediaFile:
    return file if isinstance(file, MediaFile) else MediaFile.from_path(file)
