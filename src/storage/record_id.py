"""Dirección física de un registro dentro de un archivo paginado."""

from __future__ import annotations

import struct
from dataclasses import dataclass

RECORD_ID_FORMAT = struct.Struct("<IH")
RECORD_ID_SIZE = RECORD_ID_FORMAT.size


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
