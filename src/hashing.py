"""Función de hash compartida por el índice hash y por el hashing externo.

`hash()` de Python está aleatorizado por proceso para las cadenas y los bytes: un índice
construido hoy no se podría releer mañana, y dos particiones de la misma consulta podrían
no coincidir. Blake2b siempre devuelve lo mismo para los mismos bytes.
"""

from __future__ import annotations

from hashlib import blake2b
from typing import Any

HASH_DIGEST_BYTES = 8


def stable_hash(raw: bytes) -> int:
    """Entero reproducible derivado de los bytes de una clave."""
    return int.from_bytes(blake2b(raw, digest_size=HASH_DIGEST_BYTES).digest(), "little")


def canonical_key_bytes(key: Any) -> bytes:
    """Bytes canónicos de una clave de agrupación o de reunión.

    Dos valores que Python considera iguales deben producir los mismos bytes, o acabarían
    en particiones distintas y no se encontrarían nunca. `1`, `1.0` y `True` son iguales
    para Python, así que aquí comparten representación.
    """
    if isinstance(key, float) and key.is_integer():
        key = int(key)
    if isinstance(key, bool | int):
        return b"i" + str(int(key)).encode()
    if isinstance(key, float):
        return b"f" + repr(key).encode()
    if isinstance(key, str):
        return b"s" + key.encode()
    if isinstance(key, bytes):
        return b"y" + key
    if isinstance(key, tuple):
        return b"t" + b"\x00".join(canonical_key_bytes(part) for part in key)
    return b"o" + repr(key).encode()
