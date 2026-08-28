"""Página de registros de tamaño fijo.

Formato:

    ┌───────────────────────────────┬──────────┬──────────┬─────┐
    │ cabecera (8 bytes)            │ ranura 0 │ ranura 1 │ ... │
    └───────────────────────────────┴──────────┴──────────┴─────┘

La cabecera guarda cuántas ranuras están ocupadas y un puntero a otra página, que cada
estructura usa para lo suyo: el heap file encadena páginas con espacio libre y el archivo
secuencial encadena su zona de desbordamiento.

Cada ranura es un byte de estado seguido del registro empaquetado. El heap deja huecos y
los reutiliza; el archivo secuencial mantiene las ranuras contiguas y ordenadas por clave.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from enum import IntEnum, unique

from .types import StorageError

HEADER_FORMAT = struct.Struct("<HiH")
HEADER_SIZE = HEADER_FORMAT.size
STATE_SIZE = 1
NO_PAGE = -1


class PageOverflowError(StorageError):
    """No queda espacio en la página."""


class SlotOutOfRangeError(StorageError):
    """La ranura pedida no existe en esta página."""


class EmptySlotError(StorageError):
    """La ranura pedida no contiene un registro."""


@unique
class SlotState(IntEnum):
    EMPTY = 0
    USED = 1
    DELETED = 2


def slot_capacity(page_size: int, record_size: int) -> int:
    """Ranuras que caben en una página para registros de ese tamaño.

    Raises:
        PageOverflowError: si ni un solo registro cabe en la página.
    """
    capacity = (page_size - HEADER_SIZE) // (STATE_SIZE + record_size)
    if capacity <= 0:
        raise PageOverflowError(
            f"un registro de {record_size} bytes no cabe en páginas de {page_size}"
        )
    return capacity


class RecordPage:
    """Vista mutable sobre los bytes de una página de registros."""

    def __init__(self, raw: bytearray, record_size: int) -> None:
        self._raw = raw
        self._record_size = record_size
        self._slot_size = STATE_SIZE + record_size
        self._capacity = slot_capacity(len(raw), record_size)

    @classmethod
    def create(cls, page_size: int, record_size: int) -> RecordPage:
        raw = bytearray(page_size)
        HEADER_FORMAT.pack_into(raw, 0, 0, NO_PAGE, 0)
        return cls(raw, record_size)

    @classmethod
    def from_bytes(cls, raw: bytes, record_size: int) -> RecordPage:
        return cls(bytearray(raw), record_size)

    def to_bytes(self) -> bytes:
        return bytes(self._raw)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def record_size(self) -> int:
        return self._record_size

    @property
    def used_slots(self) -> int:
        """Ranuras ocupadas, incluidas las marcadas como borradas."""
        return self._header()[0]

    @property
    def next_page(self) -> int:
        return self._header()[1]

    @next_page.setter
    def next_page(self, page_id: int) -> None:
        used, _, reserved = self._header()
        HEADER_FORMAT.pack_into(self._raw, 0, used, page_id, reserved)

    @property
    def flags(self) -> int:
        """Bits libres de la cabecera; cada estructura les da su propio significado."""
        return self._header()[2]

    @flags.setter
    def flags(self, value: int) -> None:
        used, next_page, _ = self._header()
        HEADER_FORMAT.pack_into(self._raw, 0, used, next_page, value)

    @property
    def is_full(self) -> bool:
        return self.used_slots >= self._capacity and self.first_free_slot() is None

    def state_of(self, slot: int) -> SlotState:
        self._check_slot(slot)
        return SlotState(self._raw[self._offset(slot)])

    def live_slots(self) -> Iterator[int]:
        """Ranuras con un registro vigente, en orden de posición."""
        for slot in range(self.used_slots):
            if self._raw[self._offset(slot)] == SlotState.USED:
                yield slot

    def deleted_slots(self) -> int:
        states = (self._raw[self._offset(slot)] for slot in range(self.used_slots))
        return sum(1 for state in states if state == SlotState.DELETED)

    def first_free_slot(self) -> int | None:
        """Primera ranura reutilizable, o `None` si la página está llena."""
        for slot in range(self.used_slots):
            if self._raw[self._offset(slot)] == SlotState.EMPTY:
                return slot
        if self.used_slots < self._capacity:
            return self.used_slots
        return None

    def read(self, slot: int) -> bytes:
        """Registro de la ranura.

        Raises:
            EmptySlotError: si la ranura está vacía o borrada.
        """
        if self.state_of(slot) is not SlotState.USED:
            raise EmptySlotError(f"la ranura {slot} no contiene un registro vigente")
        return bytes(self._record_view(slot))

    def record_at(self, slot: int) -> bytes:
        """Bytes de la ranura sin mirar su estado.

        El archivo secuencial lo necesita para leer la clave de una lápida, que sigue
        marcando la posición del registro dentro del orden de la página.
        """
        self._check_slot(slot)
        return bytes(self._record_view(slot))

    def write(self, slot: int, record: bytes) -> None:
        """Escribe el registro en la ranura y la marca como ocupada."""
        self._check_slot(slot)
        self._check_record(record)
        offset = self._offset(slot)
        self._raw[offset] = SlotState.USED
        self._raw[offset + STATE_SIZE : offset + self._slot_size] = record
        self._grow_used_slots(slot)

    def insert(self, record: bytes) -> int:
        """Coloca el registro en la primera ranura libre y devuelve su número.

        Raises:
            PageOverflowError: si la página está llena.
        """
        slot = self.first_free_slot()
        if slot is None:
            raise PageOverflowError("la página no tiene ranuras libres")
        self.write(slot, record)
        return slot

    def insert_at(self, slot: int, record: bytes) -> None:
        """Inserta desplazando a la derecha, para mantener el orden por clave.

        Raises:
            PageOverflowError: si no queda una ranura al final para el desplazamiento.
            SlotOutOfRangeError: si la posición está fuera del rango contiguo actual.
        """
        used = self.used_slots
        if used >= self._capacity:
            raise PageOverflowError("la página no admite un desplazamiento más")
        if not 0 <= slot <= used:
            raise SlotOutOfRangeError(f"la posición {slot} rompe la secuencia de la página")
        self._check_record(record)
        source = self._offset(slot)
        end = self._offset(used)
        self._raw[source + self._slot_size : end + self._slot_size] = self._raw[source:end]
        self._set_used_slots(used + 1)
        self._raw[source] = SlotState.USED
        self._raw[source + STATE_SIZE : source + self._slot_size] = record

    def remove_at(self, slot: int) -> None:
        """Elimina desplazando a la izquierda, sin dejar hueco."""
        used = self.used_slots
        self._check_slot(slot)
        if slot >= used:
            raise SlotOutOfRangeError(f"la ranura {slot} no está ocupada")
        source = self._offset(slot + 1)
        end = self._offset(used)
        self._raw[self._offset(slot) : end - self._slot_size] = self._raw[source:end]
        self._clear_slot(used - 1)
        self._set_used_slots(used - 1)

    def free(self, slot: int) -> None:
        """Libera la ranura para que otra inserción la reutilice."""
        self._check_slot(slot)
        self._clear_slot(slot)
        self._shrink_used_slots()

    def tombstone(self, slot: int) -> None:
        """Marca el registro como borrado sin liberar la ranura (eliminación lazy)."""
        if self.state_of(slot) is not SlotState.USED:
            raise EmptySlotError(f"la ranura {slot} no contiene un registro vigente")
        self._raw[self._offset(slot)] = SlotState.DELETED

    def _header(self) -> tuple[int, int, int]:
        used, next_page, reserved = HEADER_FORMAT.unpack_from(self._raw, 0)
        return int(used), int(next_page), int(reserved)

    def _set_used_slots(self, used: int) -> None:
        _, next_page, reserved = self._header()
        HEADER_FORMAT.pack_into(self._raw, 0, used, next_page, reserved)

    def _grow_used_slots(self, slot: int) -> None:
        if slot >= self.used_slots:
            self._set_used_slots(slot + 1)

    def _shrink_used_slots(self) -> None:
        used = self.used_slots
        while used > 0 and self._raw[self._offset(used - 1)] == SlotState.EMPTY:
            used -= 1
        self._set_used_slots(used)

    def _clear_slot(self, slot: int) -> None:
        offset = self._offset(slot)
        self._raw[offset : offset + self._slot_size] = bytes(self._slot_size)

    def _record_view(self, slot: int) -> memoryview:
        offset = self._offset(slot) + STATE_SIZE
        return memoryview(self._raw)[offset : offset + self._record_size]

    def _offset(self, slot: int) -> int:
        return HEADER_SIZE + slot * self._slot_size

    def _check_slot(self, slot: int) -> None:
        if not 0 <= slot < self._capacity:
            raise SlotOutOfRangeError(f"la ranura {slot} excede la capacidad {self._capacity}")

    def _check_record(self, record: bytes) -> None:
        if len(record) != self._record_size:
            raise StorageError(
                f"el registro ocupa {len(record)} bytes y la página espera {self._record_size}"
            )
