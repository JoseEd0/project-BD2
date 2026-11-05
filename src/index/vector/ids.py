"""La dirección de una fila como identificador de vector.

FAISS identifica cada vector con un entero de 64 bits. La dirección de una fila es una
página de 32 bits y una ranura de 16, así que cabe entera en él: el índice devuelve
directamente dónde está la fila, sin una tabla de traducción que guardar aparte.
"""

from __future__ import annotations

import struct

from storage.record_id import BITS_PER_BYTE, SLOT_FIELD, RecordId

SLOT_BITS = BITS_PER_BYTE * struct.calcsize(SLOT_FIELD)
SLOT_MASK = (1 << SLOT_BITS) - 1


def vector_id_of(record_id: RecordId) -> int:
    """El identificador de vector de la fila en `record_id`."""
    return (record_id.page_id << SLOT_BITS) | record_id.slot


def record_id_of(vector_id: int) -> RecordId:
    """La dirección de la fila cuyo vector tiene ese identificador."""
    return RecordId(page_id=vector_id >> SLOT_BITS, slot=vector_id & SLOT_MASK)
