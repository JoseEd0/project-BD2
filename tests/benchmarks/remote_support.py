"""Una fuente por rangos en memoria, para servir un `.zip` remoto sin tocar la red."""

import io
import zipfile


class MemorySource:
    """Un archivo en memoria que se lee por rangos y cuenta las peticiones, como si cada
    una fuera a la red."""

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.fetches = 0

    @property
    def size(self) -> int:
        return len(self.content)

    def fetch(self, start: int, stop: int) -> bytes:
        self.fetches += 1
        return self.content[start:stop]

    def __enter__(self) -> "MemorySource":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def zip_with_members(members: dict[str, bytes]) -> bytes:
    """Un `.zip` con los miembros dados, alternando sin comprimir y bzip2 (el de FMA)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index, (name, data) in enumerate(members.items()):
            method = zipfile.ZIP_BZIP2 if index % 2 else zipfile.ZIP_STORED
            archive.writestr(name, data, compress_type=method)
    return buffer.getvalue()
