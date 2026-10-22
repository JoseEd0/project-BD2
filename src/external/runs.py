"""Archivos temporales de registros, leídos y escritos página a página.

Los algoritmos externos —ordenamiento y hashing— generan montones de archivos intermedios
(*runs* y particiones). Todos comparten este par de clases, que los recorren página a
página. Esas páginas pasan por el buffer pool común, así que los temporales no gastan
memoria fuera del presupuesto del motor, por muchos que haya abiertos a la vez.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from types import TracebackType

from config import EngineConfig
from storage.page import RecordPage, slot_capacity
from storage.pager import Pager
from storage.types import Key

Pending = tuple[Key, int, bytes]


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


def merge_sorted_runs(
    paths: Sequence[Path],
    record_size: int,
    key_of: Callable[[bytes], Key],
    config: EngineConfig,
) -> Iterator[bytes]:
    """Mezcla k-vías de archivos ya ordenados por `key_of`, en un solo recorrido ordenado.

    Un montículo guarda el registro pendiente más pequeño de cada archivo, así que la
    mezcla lee una página de cada uno a la vez y hace `O(log k)` comparaciones por
    registro. A igual clave sale antes el del archivo que va primero en `paths`: la mezcla
    es estable si los archivos lo son.
    """
    readers = [RunReader(path, record_size, config) for path in paths]
    streams = [iter(reader) for reader in readers]
    pending: list[Pending] = []
    try:
        for position, stream in enumerate(streams):
            _push_next(pending, stream, position, key_of)
        while pending:
            _, position, record = heapq.heappop(pending)
            yield record
            _push_next(pending, streams[position], position, key_of)
    finally:
        for reader in readers:
            reader.close()


def _push_next(
    pending: list[Pending], stream: Iterator[bytes], position: int, key_of: Callable[[bytes], Key]
) -> None:
    record = next(stream, None)
    if record is not None:
        heapq.heappush(pending, (key_of(record), position, record))


def buffered_records(config: EngineConfig, record_size: int) -> int:
    """Registros de ese tamaño que un algoritmo externo puede tener a la vez en memoria."""
    return config.sort_buffer_pages * slot_capacity(config.page_size, record_size)


def remove_if_empty(directory: Path) -> None:
    """Borra el directorio temporal de un operador cuando ya no le queda ningún archivo.

    Cada `ORDER BY`, `GROUP BY` o `JOIN` trabaja en su propio directorio; sin esto el
    directorio de datos acumularía uno vacío por consulta.
    """
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()
