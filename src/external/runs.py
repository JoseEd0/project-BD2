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
from typing import TypeVar

from config import EngineConfig
from storage.layout import RecordAppender
from storage.page import RecordPage, slot_capacity
from storage.pager import Pager
from storage.types import Key

Item = TypeVar("Item")


class RunWriter:
    """Escribe registros consecutivos en un archivo temporal, llenando página por página."""

    def __init__(self, path: Path, record_size: int, config: EngineConfig) -> None:
        path.unlink(missing_ok=True)
        self._pager = Pager(path, config)
        self._appender = RecordAppender(self._pager, record_size)

    @property
    def record_count(self) -> int:
        return self._appender.record_count

    def append(self, record: bytes) -> None:
        self._appender.append(record)

    def extend(self, records: Iterable[bytes]) -> None:
        self._appender.extend(records)

    def close(self) -> None:
        self._appender.finish()
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


class RunReader:
    """Recorre un archivo temporal trayendo una página cada vez."""

    def __init__(self, path: Path, record_size: int, config: EngineConfig) -> None:
        self._pager = Pager(path, config)
        self._record_size = record_size

    def __iter__(self) -> Iterator[bytes]:
        for page_id in range(self._pager.page_count):
            page = RecordPage.from_bytes(self._pager.read(page_id), self._record_size)
            yield from (record for _, record in page.live_records())

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

    Lee una página de cada archivo a la vez; el orden y el desempate son los de
    `merge_sorted_streams`.
    """
    readers = [RunReader(path, record_size, config) for path in paths]
    try:
        streams = [iter(reader) for reader in readers]
        for _, record in merge_sorted_streams(streams, key_of):
            yield record
    finally:
        for reader in readers:
            reader.close()


def merge_sorted_streams(
    streams: Sequence[Iterator[Item]], key_of: Callable[[Item], Key]
) -> Iterator[tuple[int, Item]]:
    """Mezcla k-vías de secuencias ya ordenadas por `key_of`: cada elemento sale con la
    posición en `streams` de la secuencia de la que viene.

    Un montículo guarda el elemento pendiente más pequeño de cada secuencia, así que hay
    a lo sumo `k` elementos en memoria y cada uno cuesta `O(log k)` comparaciones. A igual
    clave sale antes el de la secuencia que va primero: la mezcla es estable si las
    secuencias lo son. El siguiente elemento de una secuencia se pide recién cuando quien
    consume pide el siguiente de la mezcla.
    """
    pending: list[tuple[Key, int, Item]] = []
    for position, stream in enumerate(streams):
        _push_next(pending, stream, position, key_of)
    while pending:
        _, position, item = heapq.heappop(pending)
        yield position, item
        _push_next(pending, streams[position], position, key_of)


def _push_next(
    pending: list[tuple[Key, int, Item]],
    stream: Iterator[Item],
    position: int,
    key_of: Callable[[Item], Key],
) -> None:
    for item in stream:
        heapq.heappush(pending, (key_of(item), position, item))
        return


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
