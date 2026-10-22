"""Hash extendible: índice de igualdad que crece y encoge sin reconstruirse.

La idea: un **directorio** de `2^profundidad_global` punteros y unos **cubetas** que solo se
parten cuando se llenan. Cada cubeta recuerda con cuántos bits se llegó a ella
(*profundidad local*), así que duplicar el directorio no obliga a mover ni un registro de
las cubetas que no se partieron. Al borrar se recorre el camino inverso: dos cubetas
gemelas que quedan holgadas se funden, y el directorio se reduce a la mitad cuando ya
ninguna cubeta necesita su último bit.

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
from index.keys import KeyCodec
from storage.layout import FileHeader, FreePageList, chained_pages
from storage.page import NO_PAGE, RecordPage, slot_capacity
from storage.pager import Pager
from storage.types import Key, StorageError

HASH_MAGIC = b"EHSH"
# La 2 enlaza las páginas libres con `FreePageList` en vez de con cubetas vacías.
HASH_VERSION = 2
# Tamaño de clave y de valor, profundidad global, cabeza de páginas libres y entradas.
HEADER_FIELDS = "IIHiQ"
DIRECTORY_SUFFIX = ".dir"
DIRECTORY_ENTRY = struct.Struct("<i")
MAX_LOCAL_DEPTH = 32
INITIAL_GLOBAL_DEPTH = 1


MAX_BUCKETS_SHOWN = 32


class HashFormatError(StorageError):
    """El archivo no corresponde a este formato de índice hash."""


HASH_HEADER = FileHeader(HASH_MAGIC, HASH_VERSION, HEADER_FIELDS, HashFormatError, "un índice hash")


class ExtendibleHashIndex:
    """Índice hash dinámico con directorio duplicable y cubetas que se parten.

    Complejidad esperada (con distribución razonable de claves):

    * búsqueda, inserción y borrado por igualdad: `O(1)` accesos a página — uno al
      directorio y uno a la cubeta;
    * duplicar o reducir el directorio: `O(2^profundidad_global)` accesos al
      directorio, sin tocar las cubetas;
    * **no ordena**: no admite consultas por rango.

    Admite claves repetidas. Si muchísimas filas comparten clave, la cubeta deja de poder
    partirse (todas caen del mismo lado) y se encadenan páginas de desbordamiento.

    Raises:
        HashFormatError: si el archivo existente no encaja con la clave o el valor pedidos.
    """

    def __init__(
        self, path: Path, key_codec: KeyCodec, value_size: int, config: EngineConfig
    ) -> None:
        self._key_codec = key_codec
        self._value_size = value_size
        self._entry_size = key_codec.size + value_size
        self._bucket_capacity = slot_capacity(config.page_size, self._entry_size)
        self._pager = Pager(path, config)
        self._directory = Pager(path.with_name(path.name + DIRECTORY_SUFFIX), config)
        self._merge_limit = int(self._bucket_capacity * config.hash_merge_fill)
        self._entries_per_directory_page = config.page_size // DIRECTORY_ENTRY.size
        self._global_depth: int
        self._free_pages: FreePageList
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
            if bucket.flags >= MAX_LOCAL_DEPTH or not self._split(directory_index, entry):
                self._append_to_chain(bucket_id, bucket, entry)
                break
            directory_index = self._directory_index(key)
        self._entry_count += 1

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
        return self._remove(key, self._key_codec.pack(key))

    def delete_entry(self, key: Key, value: bytes) -> bool:
        """Borra la entrada que asocia esa clave con ese valor. Devuelve si existía."""
        self._check_value(value)
        return self._remove(key, self._key_codec.pack(key) + value) > 0

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

    def _remove(self, key: Key, prefix: bytes) -> int:
        """Quita de la cubeta de `key` las entradas que empiezan por `prefix`."""
        directory_index = self._directory_index(key)
        removed = 0
        for page_id, page in self._chain_pages(self._directory_entry(directory_index)):
            slots = [slot for slot in page.live_slots() if page.read(slot).startswith(prefix)]
            for slot in slots:
                page.free(slot)
            if slots:
                self._store(page_id, page)
                removed += len(slots)
        if removed:
            self._entry_count -= removed
            self._compact_chain(self._directory_entry(directory_index))
            self._shrink(directory_index)
        return removed

    def _compact_chain(self, bucket_id: int) -> None:
        """Reescribe una cubeta con desbordamiento para devolver las páginas que el borrado
        dejó vacías o a medio llenar."""
        bucket = self._load(bucket_id)
        if bucket.next_page == NO_PAGE:
            return
        entries = list(self._entries_of(bucket_id))
        self._release_chain(bucket_id)
        self._write_bucket(bucket_id, entries, bucket.flags)

    def _shrink(self, directory_index: int) -> None:
        """Funde la cubeta con su gemela mientras quepan holgadas, y reduce el directorio.

        Es la división al revés. La gemela de una cubeta de profundidad local `ld` es la
        que se separó de ella por el bit `ld - 1`: se llega cambiando ese bit en el
        índice del directorio. Solo se pueden fundir si la gemela no se ha vuelto a
        partir, es decir, si tiene la misma profundidad local.
        """
        merged = False
        while self._merge_with_twin(directory_index):
            merged = True
        if merged:
            self._halve_directory()

    def _merge_with_twin(self, directory_index: int) -> bool:
        """Decide contando ranuras, sin leer las entradas: casi ningún borrado acaba en
        fusión, y leer la cubeta entera para descubrirlo doblaría el coste de borrar."""
        bucket_id = self._directory_entry(directory_index)
        bucket = self._load(bucket_id)
        local_depth = bucket.flags
        if local_depth <= INITIAL_GLOBAL_DEPTH or bucket.next_page != NO_PAGE:
            return False
        room = self._merge_limit - bucket.live_count()
        if room < 0:
            return False
        twin_bit = 1 << (local_depth - 1)
        twin_id = self._directory_entry(directory_index ^ twin_bit)
        twin = self._load(twin_id)
        if twin.flags != local_depth or twin.next_page != NO_PAGE or twin.live_count() > room:
            return False
        entries = [*self._entries_of(bucket_id), *self._entries_of(twin_id)]
        self._release(twin_id)
        self._write_bucket(bucket_id, entries, local_depth - 1)
        self._point_directory(directory_index & (twin_bit - 1), twin_bit - 1, bucket_id)
        return True

    def _point_directory(self, low_bits: int, mask: int, bucket_id: int) -> None:
        """Todas las entradas cuyos bits bajos son `low_bits` pasan a apuntar a la cubeta."""
        for index in range(self.directory_size):
            if index & mask == low_bits:
                self._set_directory_entry(index, bucket_id)

    def _halve_directory(self) -> None:
        """Reduce el directorio mientras sus dos mitades sean iguales.

        Que lo sean significa que ninguna cubeta usa el bit más alto: toda entrada apunta
        a lo mismo que la que solo difiere de ella en ese bit. Las páginas del archivo
        del directorio que sobran se quedan; volver a crecer las reutiliza.
        """
        while self._global_depth > INITIAL_GLOBAL_DEPTH:
            half = self.directory_size // 2
            if any(
                self._directory_entry(index) != self._directory_entry(index + half)
                for index in range(half)
            ):
                return
            self._global_depth -= 1

    def _split(self, directory_index: int, incoming: bytes) -> bool:
        """Parte la cubeta apuntada por esa entrada. Devuelve `False` si partir no puede
        hacer sitio para `incoming`, y entonces quien llama encadena.

        Partir no sirve cuando la entrada nueva y todas las de la cubeta tienen el mismo
        hash —una clave muy repetida—: ningún bit las separa, y cada intento duplicaría el
        directorio dejando una mitad vacía, hasta `MAX_LOCAL_DEPTH`. Si hay hashes
        distintos se parte aunque este bit no los separe: `insert` vuelve a intentarlo, y
        el bit siguiente sí lo hará.
        """
        bucket_id = self._directory_entry(directory_index)
        local_depth = self._load(bucket_id).flags
        high_bit = 1 << local_depth
        entries = list(self._entries_of(bucket_id))
        if len({self._hash_of(entry) for entry in [*entries, incoming]}) == 1:
            return False
        moving = [entry for entry in entries if self._bit_is_set(entry, high_bit)]
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
        """Escribe la cubeta con sus entradas, encadenando las páginas que hagan falta.

        La cadena se arma de la cola a la cabeza, una página llena por vez: añadir las
        entradas de una en una recorrería la cadena entera por cada una.
        """
        capacity = self._bucket_capacity
        chunks = [entries[start : start + capacity] for start in range(0, len(entries), capacity)]
        following = NO_PAGE
        for chunk in reversed(chunks[1:]):
            overflow_id = self._allocate()
            self._store(overflow_id, self._filled_page(chunk, following, local_depth))
            following = overflow_id
        first = chunks[0] if chunks else []
        self._store(bucket_id, self._filled_page(first, following, local_depth))

    def _filled_page(self, entries: list[bytes], following: int, local_depth: int) -> RecordPage:
        page = RecordPage.create(self._pager.page_size, self._entry_size)
        page.flags = local_depth
        page.next_page = following
        for entry in entries:
            page.insert(entry)
        return page

    def _release_chain(self, bucket_id: int) -> None:
        """Devuelve a la lista de libres las páginas de desbordamiento de la cubeta."""
        overflow = list(self._chain_pages(self._load(bucket_id).next_page))
        for page_id, _ in overflow:
            self._release(page_id)

    def _chain_pages(self, first_page_id: int) -> Iterator[tuple[int, RecordPage]]:
        return chained_pages(
            first_page_id, self._linked, self._pager.page_count, self._pager.path.name
        )

    def _linked(self, page_id: int) -> tuple[int, RecordPage]:
        page = self._load(page_id)
        return page.next_page, page

    def _entries_of(self, bucket_id: int) -> Iterator[bytes]:
        """Entradas completas (clave + valor) de la cubeta y su cadena."""
        for _, page in self._chain_pages(bucket_id):
            for slot in page.live_slots():
                yield page.read(slot)

    def _bit_is_set(self, entry: bytes, high_bit: int) -> bool:
        return bool(self._hash_of(entry) & high_bit)

    def _hash_of(self, entry: bytes) -> int:
        return stable_hash(entry[: self._key_codec.size])

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
        return self._free_pages.allocate()

    def _release(self, page_id: int) -> None:
        self._free_pages.release(page_id)

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
        self._free_pages = FreePageList(self._pager, NO_PAGE)
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
        key_size, value_size, depth, free_head, count = HASH_HEADER.read(self._pager)
        if key_size != self._key_codec.size or value_size != self._value_size:
            raise HashFormatError(
                f"el índice guarda claves de {key_size} y valores de {value_size} bytes; "
                f"se pidieron {self._key_codec.size} y {self._value_size}"
            )
        self._global_depth = int(depth)
        self._free_pages = FreePageList(self._pager, int(free_head))
        self._entry_count = int(count)

    def _write_header(self) -> None:
        """La cabecera se escribe al volcar o cerrar el índice, no en cada operación: su
        contenido vive en memoria y solo hace falta en disco cuando lo hacen las páginas."""
        HASH_HEADER.write(
            self._pager,
            self._key_codec.size,
            self._value_size,
            self._global_depth,
            self._free_pages.head,
            self._entry_count,
        )
