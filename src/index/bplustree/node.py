"""Nodos del árbol B+ y su serialización en una página.

Un nodo ocupa exactamente una página. Las hojas guardan las entradas reales y un puntero a
la hoja siguiente, que es lo que permite recorrer el índice en orden sin volver a bajar por
el árbol. Los nodos internos solo guardan separadores y punteros a hijos.

    interno   ┌──────┬────────────────┬──────────────────────┐
              │ hdr  │ k0  k1  k2     │ c0  c1  c2  c3       │
              └──────┴────────────────┴──────────────────────┘
                        claves de corte      hijos (n+1)

    hoja      ┌──────┬──────────────────────────┬─────────────►  hoja siguiente
              │ hdr  │ (k0,v0) (k1,v1) (k2,v2)  │
              └──────┴──────────────────────────┘

Invariante de los separadores: toda clave del hijo `c_i` cumple `k_(i-1) <= clave < k_i`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum, unique

from index.keys import Key, KeyCodec
from storage.types import StorageError

HEADER_FORMAT = struct.Struct("<BHi")
HEADER_SIZE = HEADER_FORMAT.size
CHILD_FORMAT = struct.Struct("<i")
CHILD_SIZE = CHILD_FORMAT.size
NO_NODE = -1
MINIMUM_CAPACITY = 3


class NodeCapacityError(StorageError):
    """La página es demasiado pequeña para que el árbol funcione."""


class CorruptNodeError(StorageError):
    """Los bytes de la página no describen un nodo válido."""


@unique
class NodeKind(IntEnum):
    INTERNAL = 0
    LEAF = 1


@dataclass(slots=True)
class Node:
    """Contenido de un nodo ya deserializado.

    En un nodo interno se usan `keys` y `children`; en una hoja, `keys`, `values` y
    `next_leaf`. El campo `next_leaf` también encadena las páginas libres del árbol.
    """

    kind: NodeKind
    keys: list[Key] = field(default_factory=list)
    children: list[int] = field(default_factory=list)
    values: list[bytes] = field(default_factory=list)
    next_leaf: int = NO_NODE

    @property
    def is_leaf(self) -> bool:
        return self.kind is NodeKind.LEAF


class NodeCodec:
    """Convierte nodos en páginas y calcula la capacidad de cada tipo de nodo.

    Raises:
        NodeCapacityError: si en una página no caben al menos tres entradas o separadores,
            que es el mínimo para que dividir y fusionar tengan sentido.
    """

    def __init__(self, key_codec: KeyCodec, value_size: int, page_size: int) -> None:
        self._key_codec = key_codec
        self._value_size = value_size
        self._page_size = page_size
        self._entry_size = key_codec.size + value_size
        self.leaf_capacity = (page_size - HEADER_SIZE) // self._entry_size
        self.internal_capacity = (page_size - HEADER_SIZE - CHILD_SIZE) // (
            key_codec.size + CHILD_SIZE
        )
        self._validate_capacity()

    @property
    def key_codec(self) -> KeyCodec:
        return self._key_codec

    @property
    def value_size(self) -> int:
        return self._value_size

    def pack(self, node: Node) -> bytes:
        raw = bytearray(self._page_size)
        HEADER_FORMAT.pack_into(raw, 0, int(node.kind), len(node.keys), node.next_leaf)
        offset = HEADER_SIZE
        for key in node.keys:
            raw[offset : offset + self._key_codec.size] = self._key_codec.pack(key)
            offset += self._key_codec.size
        if node.is_leaf:
            self._pack_values(raw, offset, node)
        else:
            self._pack_children(raw, offset, node)
        return bytes(raw)

    def unpack(self, raw: bytes) -> Node:
        kind_value, count, next_leaf = HEADER_FORMAT.unpack_from(raw, 0)
        try:
            kind = NodeKind(kind_value)
        except ValueError as error:
            raise CorruptNodeError(f"tipo de nodo desconocido: {kind_value}") from error
        offset = HEADER_SIZE
        keys: list[Key] = []
        for _ in range(count):
            keys.append(self._key_codec.unpack(raw[offset : offset + self._key_codec.size]))
            offset += self._key_codec.size
        if kind is NodeKind.LEAF:
            return Node(kind=kind, keys=keys, values=self._unpack_values(raw, offset, count),
                        next_leaf=next_leaf)
        return Node(kind=kind, keys=keys, children=self._unpack_children(raw, offset, count + 1))

    def _pack_values(self, raw: bytearray, offset: int, node: Node) -> None:
        for value in node.values:
            raw[offset : offset + self._value_size] = value
            offset += self._value_size

    def _pack_children(self, raw: bytearray, offset: int, node: Node) -> None:
        for child in node.children:
            CHILD_FORMAT.pack_into(raw, offset, child)
            offset += CHILD_SIZE

    def _unpack_values(self, raw: bytes, offset: int, count: int) -> list[bytes]:
        values: list[bytes] = []
        for _ in range(count):
            values.append(bytes(raw[offset : offset + self._value_size]))
            offset += self._value_size
        return values

    def _unpack_children(self, raw: bytes, offset: int, count: int) -> list[int]:
        children: list[int] = []
        for _ in range(count):
            children.append(int(CHILD_FORMAT.unpack_from(raw, offset)[0]))
            offset += CHILD_SIZE
        return children

    def _validate_capacity(self) -> None:
        if self.leaf_capacity < MINIMUM_CAPACITY:
            raise NodeCapacityError(
                f"en una página de {self._page_size} bytes solo caben {self.leaf_capacity} "
                f"entradas de {self._entry_size} bytes; hacen falta {MINIMUM_CAPACITY}"
            )
        if self.internal_capacity < MINIMUM_CAPACITY:
            raise NodeCapacityError(
                f"en una página de {self._page_size} bytes solo caben "
                f"{self.internal_capacity} separadores; hacen falta {MINIMUM_CAPACITY}"
            )
