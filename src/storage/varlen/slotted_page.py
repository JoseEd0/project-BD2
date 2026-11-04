"""Página ranurada de fragmentos de longitud variable.

Formato:

    ┌──────────┬──────────┬──────────┬─────┬─────────────┬─────────────┬─────────────┐
    │ cabecera │ ranura 0 │ ranura 1 │ ... │    libre    │ fragmento 1 │ fragmento 0 │
    └──────────┴──────────┴──────────┴─────┴─────────────┴─────────────┴─────────────┘
                 el directorio crece →        ← los datos crecen desde el final

La cabecera guarda cuántas ranuras tiene el directorio, dónde empieza la zona de datos y
cuántos bytes de huecos quedaron dentro de ella. Cada ranura dice dónde está su fragmento,
cuánto mide y de qué tipo es. Mover un fragmento cambia solo su ranura: el número de ranura,
que es lo que guarda una dirección, no cambia nunca mientras el fragmento viva.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from enum import IntEnum, unique

from storage.page import EmptySlotError, PageOverflowError, SlotOutOfRangeError

# Número de ranuras, inicio de la zona de datos y bytes de huecos dentro de ella.
PAGE_HEADER = struct.Struct("<HHH")
PAGE_HEADER_SIZE = PAGE_HEADER.size
# Desplazamiento y longitud del fragmento, seguidos de su tipo.
SLOT_PLACEMENT = "HH"
SLOT_ENTRY = struct.Struct("<" + SLOT_PLACEMENT + "B")
SLOT_ENTRY_SIZE = SLOT_ENTRY.size
KIND_POSITION = struct.calcsize("<" + SLOT_PLACEMENT)


@unique
class FragmentKind(IntEnum):
    """Qué parte de un valor guarda un fragmento.

    `WHOLE` es un valor entero; `HEAD` es el primero de una cadena y `PART` cualquiera de los
    siguientes. `FREE` marca una ranura que otra inserción puede reutilizar.
    """

    FREE = 0
    WHOLE = 1
    HEAD = 2
    PART = 3


def max_fragment_bytes(page_size: int) -> int:
    """El fragmento más grande que cabe en una página vacía de ese tamaño."""
    return page_size - PAGE_HEADER_SIZE - SLOT_ENTRY_SIZE


class SlottedPage:
    """Vista mutable sobre los bytes de una página ranurada.

    Invariantes: el directorio termina antes de que empiece la zona de datos; ningún par de
    fragmentos vivos se solapa; los huecos de la cabecera suman exactamente los bytes de la
    zona de datos que no pertenecen a ningún fragmento vivo; la última ranura del directorio
    nunca está libre.
    """

    def __init__(self, raw: bytearray) -> None:
        self._raw = raw

    @classmethod
    def create(cls, page_size: int) -> SlottedPage:
        raw = bytearray(page_size)
        PAGE_HEADER.pack_into(raw, 0, 0, page_size, 0)
        return cls(raw)

    @classmethod
    def from_bytes(cls, raw: bytes) -> SlottedPage:
        return cls(bytearray(raw))

    def to_bytes(self) -> bytes:
        return bytes(self._raw)

    @property
    def slot_count(self) -> int:
        return self._header()[0]

    def insertable_bytes(self) -> int:
        """El fragmento más grande que `insert` acepta, contando con compactar la página."""
        directory_growth = 0 if self._first_free_slot() is not None else SLOT_ENTRY_SIZE
        return max(0, self._free_bytes() - directory_growth)

    def rewrite_capacity(self, slot: int) -> int:
        """El fragmento más grande que `rewrite` acepta en esa ranura.

        Raises:
            SlotOutOfRangeError, EmptySlotError: si la ranura no tiene un fragmento.
        """
        _, length, _ = self._live_entry(slot)
        return self._free_bytes() + length

    def fragment(self, slot: int) -> tuple[FragmentKind, bytes]:
        """Tipo y bytes del fragmento de la ranura.

        Raises:
            SlotOutOfRangeError: si la ranura no está en el directorio.
            EmptySlotError: si la ranura está libre.
        """
        offset, length, kind = self._live_entry(slot)
        return kind, bytes(self._raw[offset : offset + length])

    def fragments(self) -> Iterator[tuple[int, FragmentKind, bytes]]:
        """Los fragmentos vivos con su ranura, en orden de ranura."""
        for slot in range(self.slot_count):
            offset, length, kind = SLOT_ENTRY.unpack_from(self._raw, _entry_offset(slot))
            if kind != FragmentKind.FREE:
                yield slot, FragmentKind(kind), bytes(self._raw[offset : offset + length])

    def insert(self, kind: FragmentKind, payload: bytes) -> int:
        """Guarda el fragmento en la primera ranura libre, o en una nueva, y devuelve su número.

        Raises:
            PageOverflowError: si no cabe ni compactando la página.
        """
        slot = self._first_free_slot()
        directory_growth = SLOT_ENTRY_SIZE if slot is None else 0
        self._require_room(len(payload) + directory_growth, self._free_bytes())
        self._make_contiguous(len(payload) + directory_growth)
        if slot is None:
            slot = self.slot_count
            self._set_header(slot_count=slot + 1)
        self._place(slot, kind, payload)
        return slot

    def rewrite(self, slot: int, kind: FragmentKind, payload: bytes) -> None:
        """Reemplaza el fragmento de la ranura sin cambiar el número de ranura.

        Raises:
            SlotOutOfRangeError, EmptySlotError: si la ranura no tiene un fragmento.
            PageOverflowError: si el fragmento nuevo no cabe; la página queda como estaba.
        """
        offset, length, _ = self._live_entry(slot)
        self._require_room(len(payload), self._free_bytes() + length)
        if len(payload) <= length:
            self._raw[offset : offset + len(payload)] = payload
            self._write_entry(slot, offset, len(payload), kind)
            self._set_header(hole_bytes=self._header()[2] + length - len(payload))
            return
        self._release(slot)
        self._make_contiguous(len(payload))
        self._place(slot, kind, payload)

    def free(self, slot: int) -> None:
        """Libera la ranura y el espacio de su fragmento.

        Raises:
            SlotOutOfRangeError, EmptySlotError: si la ranura no tiene un fragmento.
        """
        self._live_entry(slot)
        self._release(slot)
        self._trim_directory()

    def _release(self, slot: int) -> None:
        """Deja la ranura libre. Si el fragmento era el primero de la zona de datos, la zona
        se encoge; si no, sus bytes pasan a ser hueco hasta la próxima compactación."""
        offset, length, _ = self._entry(slot)
        _, data_start, hole_bytes = self._header()
        if offset == data_start:
            self._set_header(data_start=data_start + length)
        else:
            self._set_header(hole_bytes=hole_bytes + length)
        self._write_entry(slot, 0, 0, FragmentKind.FREE)

    def _trim_directory(self) -> None:
        """Las ranuras libres del final vuelven al espacio libre: ninguna dirección viva las
        nombra."""
        slot_count = self.slot_count
        while slot_count > 0 and self._kind_at(slot_count - 1) == FragmentKind.FREE:
            slot_count -= 1
        self._set_header(slot_count=slot_count)

    def _make_contiguous(self, size: int) -> None:
        if self._contiguous_bytes() < size:
            self._compact()

    def _compact(self) -> None:
        """Junta los fragmentos vivos contra el final de la página, sin huecos entre ellos.

        Se recorren del más alto al más bajo: cada uno solo puede moverse hacia arriba, sobre
        bytes que ya no son de nadie, así que ninguno pisa a otro que aún no se ha movido.
        """
        live = sorted(self._live_entries(), reverse=True)
        cursor = len(self._raw)
        for offset, slot, length, kind in live:
            cursor -= length
            self._raw[cursor : cursor + length] = self._raw[offset : offset + length]
            self._write_entry(slot, cursor, length, kind)
        self._set_header(data_start=cursor, hole_bytes=0)

    def _place(self, slot: int, kind: FragmentKind, payload: bytes) -> None:
        """Escribe el fragmento al principio de la zona de datos; el espacio contiguo ya se
        comprobó."""
        offset = self._header()[1] - len(payload)
        self._raw[offset : offset + len(payload)] = payload
        self._write_entry(slot, offset, len(payload), kind)
        self._set_header(data_start=offset)

    def _first_free_slot(self) -> int | None:
        """Los tipos de todas las ranuras salen de una vez con un salto del tamaño de la
        ranura: buscar una libre no recorre el directorio en Python."""
        first_kind = PAGE_HEADER_SIZE + KIND_POSITION
        kinds = bytes(self._raw[first_kind : self._directory_end() : SLOT_ENTRY_SIZE])
        slot = kinds.find(FragmentKind.FREE)
        return None if slot < 0 else slot

    def _free_bytes(self) -> int:
        return self._contiguous_bytes() + self._header()[2]

    def _contiguous_bytes(self) -> int:
        return self._header()[1] - self._directory_end()

    def _directory_end(self) -> int:
        return _entry_offset(self.slot_count)

    def _live_entries(self) -> list[tuple[int, int, int, FragmentKind]]:
        """Desplazamiento, ranura, longitud y tipo de cada fragmento vivo."""
        entries = [(self._entry(slot), slot) for slot in range(self.slot_count)]
        return [
            (offset, slot, length, kind)
            for (offset, length, kind), slot in entries
            if kind != FragmentKind.FREE
        ]

    def _live_entry(self, slot: int) -> tuple[int, int, FragmentKind]:
        offset, length, kind = self._entry(slot)
        if kind == FragmentKind.FREE:
            raise EmptySlotError(f"la ranura {slot} no contiene un fragmento")
        return offset, length, kind

    def _entry(self, slot: int) -> tuple[int, int, FragmentKind]:
        if not 0 <= slot < self.slot_count:
            raise SlotOutOfRangeError(f"la ranura {slot} no está en el directorio")
        offset, length, kind = SLOT_ENTRY.unpack_from(self._raw, _entry_offset(slot))
        return int(offset), int(length), FragmentKind(kind)

    def _kind_at(self, slot: int) -> int:
        return self._raw[_entry_offset(slot) + KIND_POSITION]

    def _write_entry(self, slot: int, offset: int, length: int, kind: FragmentKind) -> None:
        SLOT_ENTRY.pack_into(self._raw, _entry_offset(slot), offset, length, kind)

    def _header(self) -> tuple[int, int, int]:
        slot_count, data_start, hole_bytes = PAGE_HEADER.unpack_from(self._raw, 0)
        return int(slot_count), int(data_start), int(hole_bytes)

    def _set_header(
        self,
        slot_count: int | None = None,
        data_start: int | None = None,
        hole_bytes: int | None = None,
    ) -> None:
        current_count, current_start, current_holes = self._header()
        PAGE_HEADER.pack_into(
            self._raw,
            0,
            current_count if slot_count is None else slot_count,
            current_start if data_start is None else data_start,
            current_holes if hole_bytes is None else hole_bytes,
        )

    @staticmethod
    def _require_room(size: int, available: int) -> None:
        if size > available:
            raise PageOverflowError(
                f"un fragmento de {size} bytes no cabe en los {available} libres de la página"
            )


def _entry_offset(slot: int) -> int:
    return PAGE_HEADER_SIZE + slot * SLOT_ENTRY_SIZE
