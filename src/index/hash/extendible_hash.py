"""Hash extendible: índice de igualdad que crece sin reconstruirse.

La idea: un **directorio** de `2^profundidad_global` punteros y unos **cubetas** que solo se
parten cuando se llenan. Cada cubeta recuerda con cuántos bits se llegó a ella
(*profundidad local*), así que duplicar el directorio no obliga a mover ni un registro de
las cubetas que no se partieron.

Ver `README.md` para el recorrido paso a paso de una división y una duplicación.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

from config import EngineConfig
from hashing import stable_hash
from index.keys import Key, KeyCodec
from storage.page import NO_PAGE, RecordPage, slot_capacity
from storage.pager import HEADER_PAGE_ID, Pager
from storage.types import StorageError

HEADER_FORMAT = struct.Struct("<4sHIIHiQ")
HASH_MAGIC = b"EHSH"
HASH_VERSION = 1
DIRECTORY_SUFFIX = ".dir"
DIRECTORY_ENTRY = struct.Struct("<i")
MAX_LOCAL_DEPTH = 32
INITIAL_GLOBAL_DEPTH = 1


MAX_BUCKETS_SHOWN = 32


class HashFormatError(StorageError):
    """El archivo no corresponde a este formato de índice hash."""


class ExtendibleHashIndex:
    """Índice hash dinámico con directorio duplicable y cubetas que se parten.

    Complejidad esperada (con distribución razonable de claves):

    * búsqueda, inserción y borrado por igualdad: `O(1)` accesos a página — uno al
      directorio y uno a la cubeta;
    * duplicar el directorio: `O(2^profundidad_global)` escrituras, sin tocar las cubetas;
    * **no ordena**: no admite consultas por rango.

    Admite claves repetidas. Si muchísimas filas comparten clave, la cubeta deja de poder
    partirse (todas caen del mismo lado) y se encadenan páginas de desbordamiento.

    Raises:
        HashFormatError: si el archivo existente no encaja con la clave o el valor pedidos.
    """

    def __init__(
        self, path: Path, key_codec: KeyCodec, value_size: int, config: EngineConfig
    ) -> None:
        self._pager = Pager(path, config)
        self._directory = Pager(path.with_name(path.name + DIRECTORY_SUFFIX), config)
        self._key_codec = key_codec
        self._value_size = value_size
        self._entry_size = key_codec.size + value_size
        self._bucket_capacity = slot_capacity(config.page_size, self._entry_size)
        self._entries_per_directory_page = config.page_size // DIRECTORY_ENTRY.size
        self._global_depth: int
        self._free_head: int
        self._entry_count: int
        if self._pager.page_count == 0:
            self._create()
        else:
            self._read_header()

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def global_depth(self) -> int:
        return self._global_depth

    @property
    def directory_size(self) -> int:
        return 1 << self._global_depth

    @property
    def bucket_capacity(self) -> int:
        return self._bucket_capacity

    @property
    def bucket_count(self) -> int:
        """Cubetas distintas, sin contar las páginas de desbordamiento."""
        return len({self._directory_entry(index) for index in range(self.directory_size)})

    @property
    def page_count(self) -> int:
        return self._pager.page_count

    def insert(self, key: Key, value: bytes) -> None:
        """Añade la entrada, partiendo la cubeta o duplicando el directorio si hace falta."""
        self._check_value(value)
        entry = self._key_codec.pack(key) + value
        directory_index = self._directory_index(key)
        while True:
            bucket_id = self._directory_entry(directory_index)
            bucket = self._load(bucket_id)
            if bucket.first_free_slot() is not None:
                bucket.insert(entry)
                self._store(bucket_id, bucket)
                break
            if bucket.flags >= MAX_LOCAL_DEPTH or not self._split(directory_index):
                self._append_to_chain(bucket_id, bucket, entry)
                break
            directory_index = self._directory_index(key)
        self._entry_count += 1
        self._write_header()

    def search(self, key: Key) -> list[bytes]:
        """Valores asociados a la clave; lista vacía si no está."""
        raw_key = self._key_codec.pack(key)
        bucket_id = self._directory_entry(self._directory_index(key))
        return [
            entry[self._key_codec.size :]
            for entry in self._entries_of(bucket_id)
            if entry[: self._key_codec.size] == raw_key
        ]

    def delete(self, key: Key) -> int:
        """Borra todas las entradas con esa clave y devuelve cuántas."""
        raw_key = self._key_codec.pack(key)
        bucket_id = self._directory_entry(self._directory_index(key))
        removed = 0
        for page_id, page in self._chain_pages(bucket_id):
            slots = [
                slot
                for slot in page.live_slots()
                if page.read(slot)[: self._key_codec.size] == raw_key
            ]
            for slot in slots:
                page.free(slot)
            if slots:
                self._store(page_id, page)
                removed += len(slots)
        self._entry_count -= removed
        self._write_header()
        return removed

    def scan(self) -> Iterator[tuple[Key, bytes]]:
        """Todas las entradas, sin ningún orden garantizado."""
        buckets = {self._directory_entry(index) for index in range(self.directory_size)}
        for bucket_id in sorted(buckets):
            for entry in self._entries_of(bucket_id):
                key_size = self._key_codec.size
                yield self._key_codec.unpack(entry[:key_size]), entry[key_size:]

    def describe(self) -> dict[str, Any]:
        """Directorio y cubetas tal como están en disco.

        Por cada cubeta: su profundidad local, cuántas entradas guarda, cuántos punteros del
        directorio la apuntan (siempre `2^(gd - ld)`) y cuántas páginas de desbordamiento
        encadena.
        """
        pointers: dict[int, list[int]] = {}
        for index in range(self.directory_size):
            pointers.setdefault(self._directory_entry(index), []).append(index)
        buckets = []
        for bucket_id, indexes in sorted(pointers.items(), key=lambda item: item[1][0]):
            chain = list(self._chain_pages(bucket_id))
            buckets.append(
                {
                    "bits": format(indexes[0], f"0{self._global_depth}b"),
                    "local_depth": chain[0][1].flags,
                    "entries": sum(len(list(page.live_slots())) for _, page in chain),
                    "pointers": len(indexes),
                    "overflow_pages": len(chain) - 1,
                }
            )
        return {
            "kind": "hash",
            "global_depth": self._global_depth,
            "directory_size": self.directory_size,
            "bucket_capacity": self._bucket_capacity,
            "bucket_count": len(buckets),
            "entries": self._entry_count,
            "buckets": buckets[:MAX_BUCKETS_SHOWN],
        }

    def flush(self) -> None:
        self._write_header()
        self._pager.flush()
        self._directory.flush()

    def close(self) -> None:
        self._write_header()
        self._pager.close()
        self._directory.close()

    def __enter__(self) -> ExtendibleHashIndex:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _split(self, directory_index: int) -> bool:
        """Parte la cubeta apuntada por esa entrada. Devuelve si el reparto sirvió de algo.

        Comprobar antes de duplicar es lo que evita que un montón de claves repetidas
        —que siempre caen del mismo lado— haga crecer el directorio sin repartir nada.
        """
        bucket_id = self._directory_entry(directory_index)
        local_depth = self._load(bucket_id).flags
        high_bit = 1 << local_depth
        entries = list(self._entries_of(bucket_id))
        moving = [entry for entry in entries if self._bit_is_set(entry, high_bit)]
        if not moving:
            return False
        staying = [entry for entry in entries if not self._bit_is_set(entry, high_bit)]
        if local_depth == self._global_depth:
            self._double_directory()
        new_bucket_id = self._allocate()
        self._release_chain(bucket_id)
        self._write_bucket(bucket_id, staying, local_depth + 1)
        self._write_bucket(new_bucket_id, moving, local_depth + 1)
        self._repoint_directory(directory_index & (high_bit - 1), high_bit, new_bucket_id)
        return True

    def _repoint_directory(self, low_bits: int, high_bit: int, new_bucket_id: int) -> None:
        """Las entradas que ahora tienen el bit nuevo encendido pasan a la cubeta nueva."""
        for index in range(self.directory_size):
            if (index & (high_bit - 1)) == low_bits and index & high_bit:
                self._set_directory_entry(index, new_bucket_id)

    def _append_to_chain(self, bucket_id: int, bucket: RecordPage, entry: bytes) -> None:
        for page_id, page in self._chain_pages(bucket_id):
            if page.first_free_slot() is not None:
                page.insert(entry)
                self._store(page_id, page)
                return
        overflow_id = self._allocate()
        overflow = RecordPage.create(self._pager.page_size, self._entry_size)
        overflow.insert(entry)
        overflow.next_page = bucket.next_page
        self._store(overflow_id, overflow)
        bucket.next_page = overflow_id
        self._store(bucket_id, bucket)

    def _write_bucket(self, bucket_id: int, entries: list[bytes], local_depth: int) -> None:
        page = RecordPage.create(self._pager.page_size, self._entry_size)
        page.flags = local_depth
        overflow = entries[self._bucket_capacity :]
        for entry in entries[: self._bucket_capacity]:
            page.insert(entry)
        self._store(bucket_id, page)
        for entry in overflow:
            self._append_to_chain(bucket_id, self._load(bucket_id), entry)

    def _release_chain(self, bucket_id: int) -> None:
        page_id = self._load(bucket_id).next_page
        while page_id != NO_PAGE:
            page = self._load(page_id)
            next_page = page.next_page
            self._release(page_id)
            page_id = next_page

    def _chain_pages(self, bucket_id: int) -> Iterator[tuple[int, RecordPage]]:
        page_id = bucket_id
        while page_id != NO_PAGE:
            page = self._load(page_id)
            yield page_id, page
            page_id = page.next_page

    def _entries_of(self, bucket_id: int) -> Iterator[bytes]:
        """Entradas completas (clave + valor) de la cubeta y su cadena."""
        for _, page in self._chain_pages(bucket_id):
            for slot in page.live_slots():
                yield page.read(slot)

    def _bit_is_set(self, entry: bytes, high_bit: int) -> bool:
        return bool(stable_hash(entry[: self._key_codec.size]) & high_bit)

    def _directory_index(self, key: Key) -> int:
        return stable_hash(self._key_codec.pack(key)) & (self.directory_size - 1)

    def _directory_entry(self, index: int) -> int:
        page_id, offset = divmod(index, self._entries_per_directory_page)
        raw = self._directory.read(page_id)
        return int(DIRECTORY_ENTRY.unpack_from(raw, offset * DIRECTORY_ENTRY.size)[0])

    def _set_directory_entry(self, index: int, bucket_id: int) -> None:
        page_id, offset = divmod(index, self._entries_per_directory_page)
        raw = bytearray(self._directory.read(page_id))
        DIRECTORY_ENTRY.pack_into(raw, offset * DIRECTORY_ENTRY.size, bucket_id)
        self._directory.write(page_id, bytes(raw))

    def _double_directory(self) -> None:
        """Duplica el directorio copiando cada puntero: las cubetas no se tocan."""
        old_size = self.directory_size
        self._global_depth += 1
        self._grow_directory_pages()
        for index in range(old_size):
            self._set_directory_entry(old_size + index, self._directory_entry(index))

    def _grow_directory_pages(self) -> None:
        needed = -(-self.directory_size // self._entries_per_directory_page)
        while self._directory.page_count < needed:
            self._directory.allocate()

    def _allocate(self) -> int:
        if self._free_head == NO_PAGE:
            return self._pager.allocate()
        page_id = self._free_head
        self._free_head = self._load(page_id).next_page
        return page_id

    def _release(self, page_id: int) -> None:
        page = RecordPage.create(self._pager.page_size, self._entry_size)
        page.next_page = self._free_head
        self._store(page_id, page)
        self._free_head = page_id

    def _load(self, page_id: int) -> RecordPage:
        return RecordPage.from_bytes(self._pager.read(page_id), self._entry_size)

    def _store(self, page_id: int, page: RecordPage) -> None:
        self._pager.write(page_id, page.to_bytes())

    def _check_value(self, value: bytes) -> None:
        if len(value) != self._value_size:
            raise StorageError(
                f"el valor ocupa {len(value)} bytes y el índice espera {self._value_size}"
            )

    def _create(self) -> None:
        self._pager.allocate()
        self._global_depth = INITIAL_GLOBAL_DEPTH
        self._free_head = NO_PAGE
        self._entry_count = 0
        self._grow_directory_pages()
        for index in range(self.directory_size):
            bucket_id = self._pager.allocate()
            page = RecordPage.create(self._pager.page_size, self._entry_size)
            page.flags = INITIAL_GLOBAL_DEPTH
            self._store(bucket_id, page)
            self._set_directory_entry(index, bucket_id)
        self._write_header()

    def _read_header(self) -> None:
        raw = self._pager.read(HEADER_PAGE_ID)
        magic, version, key_size, value_size, depth, free_head, count = HEADER_FORMAT.unpack_from(
            raw, 0
        )
        if magic != HASH_MAGIC or version != HASH_VERSION:
            raise HashFormatError(f"{self._pager.path.name} no es un índice hash válido")
        if key_size != self._key_codec.size or value_size != self._value_size:
            raise HashFormatError(
                f"el índice guarda claves de {key_size} y valores de {value_size} bytes; "
                f"se pidieron {self._key_codec.size} y {self._value_size}"
            )
        self._global_depth = int(depth)
        self._free_head = int(free_head)
        self._entry_count = int(count)

    def _write_header(self) -> None:
        raw = bytearray(self._pager.page_size)
        HEADER_FORMAT.pack_into(
            raw,
            0,
            HASH_MAGIC,
            HASH_VERSION,
            self._key_codec.size,
            self._value_size,
            self._global_depth,
            self._free_head,
            self._entry_count,
        )
        self._pager.write(HEADER_PAGE_ID, bytes(raw))
