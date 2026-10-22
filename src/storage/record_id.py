"""Dirección física de un registro dentro de un archivo paginado."""

from __future__ import annotations

import struct
from dataclasses import dataclass

PAGE_FIELD = "I"
SLOT_FIELD = "H"
# Página y ranura. Quien guarde una dirección dentro de otro formato usa estos campos.
RECORD_ID_FIELDS = PAGE_FIELD + SLOT_FIELD
RECORD_ID_FORMAT = struct.Struct("<" + RECORD_ID_FIELDS)
RECORD_ID_SIZE = RECORD_ID_FORMAT.size
BITS_PER_BYTE = 8


@dataclass(frozen=True, order=True, slots=True)
class RecordId:
    """Par (página, ranura) que localiza un registro sin ambigüedad.

    Es lo que guardan las hojas de un índice no agrupado para llegar al heap file.
    """

    page_id: int
    slot: int

    def pack(self) -> bytes:
        return RECORD_ID_FORMAT.pack(self.page_id, self.slot)

    @classmethod
    def unpack(cls, raw: bytes) -> RecordId:
        page_id, slot = RECORD_ID_FORMAT.unpack(raw)
        return cls(page_id=page_id, slot=slot)

    def __str__(self) -> str:
        return f"({self.page_id}:{self.slot})"


LOWEST_RECORD_ID = RecordId(page_id=0, slot=0)
HIGHEST_RECORD_ID = RecordId(
    page_id=(1 << (BITS_PER_BYTE * struct.calcsize(PAGE_FIELD))) - 1,
    slot=(1 << (BITS_PER_BYTE * struct.calcsize(SLOT_FIELD))) - 1,
)
