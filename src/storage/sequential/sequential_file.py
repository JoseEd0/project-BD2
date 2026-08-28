"""Archivo secuencial paginado con espacio auxiliar y reorganización.

Dos archivos trabajan juntos:

* **Espacio principal** (`tabla.seq`): páginas consecutivas con los registros ordenados
  por clave. La página `p` guarda claves menores que las de la página `p + 1`, así que
  buscar una clave es una búsqueda binaria sobre las páginas.
* **Espacio auxiliar** (`tabla.seq.overflow`): cuando la página que le toca a un registro
  está llena, el registro va a una cadena de desbordamiento colgada de esa página.

La eliminación es lazy: marca una lápida y deja el registro donde está. Cuando el
desperdicio (lápidas + registros desplazados al auxiliar) supera el umbral configurado,
la reorganización reescribe todo en orden y deja las páginas al factor de llenado.

Ver `README.md` para el análisis de complejidad.
"""

from __future__ import annotations

import struct
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

from config import EngineConfig
from storage.page import NO_PAGE, RecordPage, SlotState, slot_capacity
from storage.pager import HEADER_PAGE_ID, Pager
from storage.record import RecordSerializer
from storage.types import StorageError

HEADER_FORMAT = struct.Struct("<4sHIIQQQ")
SEQUENTIAL_MAGIC = b"SEQF"
SEQUENTIAL_VERSION = 1
OVERFLOW_SUFFIX = ".overflow"
REBUILD_SUFFIX = ".rebuild"
FIRST_MAIN_PAGE = HEADER_PAGE_ID + 1

Key = Any


class SequentialFormatError(StorageError):
    """El archivo no corresponde a este formato de archivo secuencial."""


class NullKeyError(StorageError):
    """La clave de ordenamiento no admite NULL."""


class SequentialFile:
    """Registros ordenados por una clave, con inserción ordenada y borrado lazy.

    Complejidad (con `P` páginas principales y `c` registros por página):

    * búsqueda por igualdad: `O(log P)` lecturas de página más la cadena de desbordamiento;
    * inserción: `O(log P)` para localizar y `O(c)` bytes movidos dentro de la página;
    * recorrido ordenado: `O(P)` lecturas;
    * reorganización: `O(N)` lecturas y escrituras.

    Raises:
        SequentialFormatError: si el archivo existente no es de este formato.
    """

    def __init__(
        self,
        path: Path,
        serializer: RecordSerializer,
        key_field: str,
        config: EngineConfig,
    ) -> None:
        self._path = path
        self._serializer = serializer
        self._key_position = serializer.schema.position_of(key_field)
        self._key_field = serializer.schema.fields[self._key_position].name
        self._config = config
        self._record_size = serializer.size
        self._capacity = slot_capacity(config.page_size, self._record_size)
        self._main = Pager(path, config)
        self._overflow = Pager(self._overflow_path(path), config)
        if self._main.page_count == 0:
            self._create_header()
        self._read_header()

    @property
    def record_count(self) -> int:
        return self._live_count

    @property
    def deleted_count(self) -> int:
        return self._deleted_count

    @property
    def overflow_count(self) -> int:
        return self._overflow_count

    @property
    def main_page_count(self) -> int:
        return self._main_page_count

    @property
    def slots_per_page(self) -> int:
        return self._capacity

    @property
    def waste_ratio(self) -> float:
        """Fracción de registros almacenados que están de más o fuera de sitio."""
        stored = self._live_count + self._deleted_count
        if stored == 0:
            return 0.0
        return (self._deleted_count + self._overflow_count) / stored

    def insert(self, record: bytes) -> None:
        """Inserta manteniendo el orden por clave.

        Raises:
            NullKeyError: si la clave del registro es NULL.
        """
        key = self._key_of(record)
        if self._main_page_count == 0:
            self._append_main_page(record)
        else:
            self._insert_into_main(key, record)
        self._live_count += 1
        self._write_header()
        self._reorganize_if_wasteful()

    def search(self, key: Key) -> list[bytes]:
        """Todos los registros vigentes con esa clave."""
        if self._main_page_count == 0:
            return []
        page_id = self._locate_page(key)
        page = self._load_main(page_id)
        matches = [record for _, record in self._page_matches(page, key)]
        matches.extend(record for _, record in self._chain_matches(page.next_page, key))
        return matches

    def range_search(self, low: Key, high: Key) -> Iterator[bytes]:
        """Registros vigentes con `low <= clave <= high`, en orden de clave."""
        if self._main_page_count == 0 or low > high:
            return
        for page_id in range(self._locate_page(low), self._main_page_count + 1):
            page = self._load_main(page_id)
            first_key = self._first_key(page)
            if first_key is not None and first_key > high:
                return
            for key, record in self._sorted_page_records(page):
                if key > high:
                    return
                if key >= low:
                    yield record

    def scan(self) -> Iterator[bytes]:
        """Todos los registros vigentes en orden de clave."""
        for page_id in range(FIRST_MAIN_PAGE, self._main_page_count + 1):
            page = self._load_main(page_id)
            for _, record in self._sorted_page_records(page):
                yield record

    def delete(self, key: Key) -> int:
        """Marca como borrados todos los registros con esa clave y devuelve cuántos."""
        if self._main_page_count == 0:
            return 0
        page_id = self._locate_page(key)
        page = self._load_main(page_id)
        removed = self._tombstone_in_page(page, key)
        if removed:
            self._store_main(page_id, page)
        removed += self._tombstone_in_chain(page.next_page, key)
        if removed == 0:
            return 0
        self._live_count -= removed
        self._deleted_count += removed
        self._write_header()
        self._reorganize_if_wasteful()
        return removed

    def reorganize(self) -> None:
        """Reescribe el archivo en orden, sin lápidas ni desbordamiento."""
        records_per_page = max(1, int(self._capacity * self._config.sequential_fill_factor))
        rebuild_path = self._path.with_name(self._path.name + REBUILD_SUFFIX)
        rebuild_path.unlink(missing_ok=True)
        written_pages = self._write_rebuilt_file(rebuild_path, records_per_page)
        self._replace_files(rebuild_path, written_pages)

    def flush(self) -> None:
        self._write_header()
        self._main.flush()
        self._overflow.flush()

    def close(self) -> None:
        self._write_header()
        self._main.close()
        self._overflow.close()

    def __enter__(self) -> SequentialFile:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _insert_into_main(self, key: Key, record: bytes) -> None:
        page_id = self._locate_page(key)
        page = self._load_main(page_id)
        if page.used_slots < self._capacity:
            page.insert_at(self._insert_position(page, key), record)
            self._store_main(page_id, page)
            return
        if page_id == self._main_page_count and key >= self._last_key(page):
            self._append_main_page(record)
            return
        self._insert_into_overflow(page_id, page, record)

    def _insert_into_overflow(self, page_id: int, page: RecordPage, record: bytes) -> None:
        chain_page_id = page.next_page
        while chain_page_id != NO_PAGE:
            chain_page = self._load_overflow(chain_page_id)
            if chain_page.first_free_slot() is not None:
                chain_page.insert(record)
                self._store_overflow(chain_page_id, chain_page)
                self._overflow_count += 1
                return
            chain_page_id = chain_page.next_page
        new_page_id = self._overflow.allocate()
        new_page = RecordPage.create(self._overflow.page_size, self._record_size)
        new_page.next_page = page.next_page
        new_page.insert(record)
        self._store_overflow(new_page_id, new_page)
        page.next_page = new_page_id
        self._store_main(page_id, page)
        self._overflow_count += 1

    def _append_main_page(self, record: bytes) -> None:
        page_id = self._main.allocate()
        page = RecordPage.create(self._main.page_size, self._record_size)
        page.insert_at(0, record)
        self._store_main(page_id, page)
        self._main_page_count += 1

    def _locate_page(self, key: Key) -> int:
        """Última página principal cuya primera clave no supera a `key`."""
        low, high = FIRST_MAIN_PAGE, self._main_page_count
        result = FIRST_MAIN_PAGE
        while low <= high:
            middle = (low + high) // 2
            first_key = self._first_key(self._load_main(middle))
            if first_key is None or first_key <= key:
                result = middle
                low = middle + 1
            else:
                high = middle - 1
        return result

    def _insert_position(self, page: RecordPage, key: Key) -> int:
        return bisect_right(self._page_keys(page), key)

    def _page_matches(self, page: RecordPage, key: Key) -> Iterator[tuple[Key, bytes]]:
        keys = self._page_keys(page)
        for slot in range(bisect_left(keys, key), bisect_right(keys, key)):
            if page.state_of(slot) is SlotState.USED:
                yield keys[slot], page.read(slot)

    def _chain_matches(self, first_page_id: int, key: Key) -> Iterator[tuple[Key, bytes]]:
        for _, page in self._chain(first_page_id):
            for slot in page.live_slots():
                record = page.read(slot)
                record_key = self._key_of(record)
                if record_key == key:
                    yield record_key, record

    def _sorted_page_records(self, page: RecordPage) -> list[tuple[Key, bytes]]:
        """Registros vigentes de la página y su cadena, ordenados por clave.

        La cadena de una página es corta por construcción, así que ordenarla en memoria
        no rompe la regla de no cargar el archivo entero.
        """
        records = [self._keyed(page, slot) for slot in page.live_slots()]
        for _, chain_page in self._chain(page.next_page):
            records.extend(self._keyed(chain_page, slot) for slot in chain_page.live_slots())
        records.sort(key=lambda entry: entry[0])
        return records

    def _keyed(self, page: RecordPage, slot: int) -> tuple[Key, bytes]:
        record = page.read(slot)
        return self._key_of(record), record

    def _tombstone_in_page(self, page: RecordPage, key: Key) -> int:
        keys = self._page_keys(page)
        removed = 0
        for slot in range(bisect_left(keys, key), bisect_right(keys, key)):
            if page.state_of(slot) is SlotState.USED:
                page.tombstone(slot)
                removed += 1
        return removed

    def _tombstone_in_chain(self, first_page_id: int, key: Key) -> int:
        removed = 0
        for page_id, page in self._chain(first_page_id):
            hits = [slot for slot in page.live_slots() if self._key_of(page.read(slot)) == key]
            for slot in hits:
                page.tombstone(slot)
            if hits:
                self._store_overflow(page_id, page)
                removed += len(hits)
                self._overflow_count -= len(hits)
        return removed

    def _chain(self, first_page_id: int) -> Iterator[tuple[int, RecordPage]]:
        page_id = first_page_id
        while page_id != NO_PAGE:
            page = self._load_overflow(page_id)
            yield page_id, page
            page_id = page.next_page

    def _page_keys(self, page: RecordPage) -> list[Key]:
        """Claves de todas las ranuras ocupadas, incluidas las lápidas, en orden."""
        return [self._key_of(page.record_at(slot)) for slot in range(page.used_slots)]

    def _first_key(self, page: RecordPage) -> Key | None:
        if page.used_slots == 0:
            return None
        return self._key_of(page.record_at(0))

    def _last_key(self, page: RecordPage) -> Key:
        return self._key_of(page.record_at(page.used_slots - 1))

    def _key_of(self, record: bytes) -> Key:
        key = self._serializer.unpack_field(record, self._key_position)
        if key is None:
            raise NullKeyError(f"la clave '{self._key_field}' no admite NULL")
        return key

    def _reorganize_if_wasteful(self) -> None:
        if self.waste_ratio > self._config.sequential_waste_ratio:
            self.reorganize()

    def _write_rebuilt_file(self, rebuild_path: Path, records_per_page: int) -> int:
        with Pager(rebuild_path, self._config) as writer:
            writer.allocate()
            written_pages = 0
            page: RecordPage | None = None
            page_id = 0
            for record in self.scan():
                if page is None or page.used_slots >= records_per_page:
                    if page is not None:
                        writer.write(page_id, page.to_bytes())
                    page_id = writer.allocate()
                    page = RecordPage.create(writer.page_size, self._record_size)
                    written_pages += 1
                page.insert_at(page.used_slots, record)
            if page is not None:
                writer.write(page_id, page.to_bytes())
        return written_pages

    def _replace_files(self, rebuild_path: Path, written_pages: int) -> None:
        self._main.close()
        self._overflow.close()
        rebuild_path.replace(self._path)
        self._overflow_path(self._path).unlink(missing_ok=True)
        self._main = Pager(self._path, self._config)
        self._overflow = Pager(self._overflow_path(self._path), self._config)
        self._main_page_count = written_pages
        self._deleted_count = 0
        self._overflow_count = 0
        self._write_header()

    def _load_main(self, page_id: int) -> RecordPage:
        return RecordPage.from_bytes(self._main.read(page_id), self._record_size)

    def _store_main(self, page_id: int, page: RecordPage) -> None:
        self._main.write(page_id, page.to_bytes())

    def _load_overflow(self, page_id: int) -> RecordPage:
        return RecordPage.from_bytes(self._overflow.read(page_id), self._record_size)

    def _store_overflow(self, page_id: int, page: RecordPage) -> None:
        self._overflow.write(page_id, page.to_bytes())

    @staticmethod
    def _overflow_path(path: Path) -> Path:
        return path.with_name(path.name + OVERFLOW_SUFFIX)

    def _create_header(self) -> None:
        self._main.allocate()
        self._main_page_count = 0
        self._live_count = 0
        self._deleted_count = 0
        self._overflow_count = 0
        self._write_header()

    def _read_header(self) -> None:
        raw = self._main.read(HEADER_PAGE_ID)
        magic, version, record_size, pages, live, deleted, overflow = HEADER_FORMAT.unpack_from(
            raw, 0
        )
        if magic != SEQUENTIAL_MAGIC or version != SEQUENTIAL_VERSION:
            raise SequentialFormatError(f"{self._path.name} no es un archivo secuencial válido")
        if record_size != self._record_size:
            raise SequentialFormatError(
                f"el archivo guarda registros de {record_size} bytes y se pidieron "
                f"{self._record_size}"
            )
        self._main_page_count = int(pages)
        self._live_count = int(live)
        self._deleted_count = int(deleted)
        self._overflow_count = int(overflow)

    def _write_header(self) -> None:
        raw = bytearray(self._main.page_size)
        HEADER_FORMAT.pack_into(
            raw,
            0,
            SEQUENTIAL_MAGIC,
            SEQUENTIAL_VERSION,
            self._record_size,
            self._main_page_count,
            self._live_count,
            self._deleted_count,
            self._overflow_count,
        )
        self._main.write(HEADER_PAGE_ID, bytes(raw))
