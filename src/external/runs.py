"""Archivos temporales de registros, leídos y escritos página a página.

Los algoritmos externos —ordenamiento y hashing— generan montones de archivos intermedios
(*runs* y particiones). Todos comparten este par de clases, que garantizan que nunca haya
más de una página en memoria por archivo abierto.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from types import TracebackType

from config import EngineConfig
from storage.page import RecordPage, slot_capacity
from storage.pager import Pager


class RunWriter:
    """Escribe registros consecutivos en un archivo temporal, llenando página por página."""

    def __init__(self, path: Path, record_size: int, config: EngineConfig) -> None:
        path.unlink(missing_ok=True)
        self._pager = Pager(path, config)
        self._record_size = record_size
        self._capacity = slot_capacity(config.page_size, record_size)
        self._page = RecordPage.create(config.page_size, record_size)
        self._page_id: int | None = None
        self.record_count = 0

    @property
    def path(self) -> Path:
        return self._pager.path

    def append(self, record: bytes) -> None:
        if self._page.used_slots >= self._capacity:
            self._flush_page()
        if self._page_id is None:
            self._page_id = self._pager.allocate()
        self._page.insert_at(self._page.used_slots, record)
        self.record_count += 1

    def extend(self, records: Iterable[bytes]) -> None:
        for record in records:
            self.append(record)

    def close(self) -> None:
        self._flush_page()
        self._pager.close()

    def __enter__(self) -> RunWriter:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _flush_page(self) -> None:
        if self._page_id is None or self._page.used_slots == 0:
            return
        self._pager.write(self._page_id, self._page.to_bytes())
        self._page = RecordPage.create(self._pager.page_size, self._record_size)
        self._page_id = None


class RunReader:
    """Recorre un archivo temporal trayendo una página cada vez."""

    def __init__(self, path: Path, record_size: int, config: EngineConfig) -> None:
        self._pager = Pager(path, config)
        self._record_size = record_size

    @property
    def path(self) -> Path:
        return self._pager.path

    def __iter__(self) -> Iterator[bytes]:
        for page_id in range(self._pager.page_count):
            page = RecordPage.from_bytes(self._pager.read(page_id), self._record_size)
            for slot in page.live_slots():
                yield page.read(slot)

    def close(self) -> None:
        self._pager.close()

    def __enter__(self) -> RunReader:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
