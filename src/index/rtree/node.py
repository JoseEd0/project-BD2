"""Nodos del R-Tree y su serialización en una página.

Un nodo ocupa exactamente una página. Las hojas guardan los puntos indexados con la
dirección de su fila; los nodos internos guardan, por cada hijo, el menor rectángulo que
encierra todo lo que cuelga de él (su MBR).

    interno   ┌──────┬──────────────────────────────────────────────┐
              │ hdr  │ (MBR0, hijo0) (MBR1, hijo1) (MBR2, hijo2) …  │
              └──────┴──────────────────────────────────────────────┘

    hoja      ┌──────┬──────────────────────────────────────────────┐
              │ hdr  │ (punto0, fila0) (punto1, fila1) …            │
              └──────┴──────────────────────────────────────────────┘

Invariante: el MBR de una entrada interna contiene a todas las entradas de su hijo. A
diferencia del B+, los MBR de dos hermanos pueden solaparse, así que una búsqueda puede
tener que bajar por más de una rama.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum, unique
from typing import ClassVar, NamedTuple, Protocol

from spatial.geometry import Point, Rectangle
from storage.record_id import RecordId
from storage.types import StorageError

HEADER_FORMAT = struct.Struct("<BHi")
HEADER_SIZE = HEADER_FORMAT.size
LEAF_ENTRY_FORMAT = struct.Struct("<ddIH")
BRANCH_ENTRY_FORMAT = struct.Struct("<ddddi")
NO_NODE = -1
# Con menos de cuatro entradas no se puede dividir un nodo en dos grupos de al menos dos.
MINIMUM_CAPACITY = 4
MINIMUM_ENTRIES_PER_NODE = 2


class NodeCapacityError(StorageError):
    """La página es demasiado pequeña para que el árbol funcione."""


class CorruptNodeError(StorageError):
    """Los bytes de la página no describen un nodo válido."""


@unique
class NodeKind(IntEnum):
    BRANCH = 0
    LEAF = 1


class Bounded(Protocol):
    """Cualquier entrada de un nodo: lo único que los algoritmos necesitan es su MBR."""

    @property
    def bounds(self) -> Rectangle: ...


class LeafEntry(NamedTuple):
    """Un punto indexado y la dirección de su fila en el heap file."""

    lat: float
    lon: float
    page_id: int
    slot: int

    @property
    def bounds(self) -> Rectangle:
        return Rectangle(self.lat, self.lon, self.lat, self.lon)

    @property
    def point(self) -> Point:
        return Point(self.lat, self.lon)

    @property
    def record_id(self) -> RecordId:
        return RecordId(self.page_id, self.slot)


class BranchEntry(NamedTuple):
    """El MBR de un hijo y el número de la página donde vive."""

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float
    child: int

    @property
    def bounds(self) -> Rectangle:
        return Rectangle(self.min_lat, self.min_lon, self.max_lat, self.max_lon)


@dataclass(slots=True)
class LeafNode:
    entries: list[LeafEntry] = field(default_factory=list)
    next_free: int = NO_NODE
    kind: ClassVar[NodeKind] = NodeKind.LEAF


@dataclass(slots=True)
class BranchNode:
    entries: list[BranchEntry] = field(default_factory=list)
    next_free: int = NO_NODE
    kind: ClassVar[NodeKind] = NodeKind.BRANCH


Node = LeafNode | BranchNode


def leaf_entry(point: Point, record_id: RecordId) -> LeafEntry:
    return LeafEntry(point.lat, point.lon, record_id.page_id, record_id.slot)


def branch_entry(bounds: Rectangle, child: int) -> BranchEntry:
    return BranchEntry(bounds.min_lat, bounds.min_lon, bounds.max_lat, bounds.max_lon, child)


class NodeCodec:
    """Convierte nodos en páginas y calcula la capacidad de cada tipo de nodo.

    Raises:
        NodeCapacityError: si en una página no caben al menos cuatro entradas.
    """

    def __init__(self, page_size: int) -> None:
        self._page_size = page_size
        self.leaf_capacity = (page_size - HEADER_SIZE) // LEAF_ENTRY_FORMAT.size
        self.branch_capacity = (page_size - HEADER_SIZE) // BRANCH_ENTRY_FORMAT.size
        if min(self.leaf_capacity, self.branch_capacity) < MINIMUM_CAPACITY:
            raise NodeCapacityError(
                f"en una página de {page_size} bytes caben {self.leaf_capacity} entradas de "
                f"hoja y {self.branch_capacity} internas; hacen falta {MINIMUM_CAPACITY}"
            )

    def pack(self, node: Node) -> bytes:
        entry_format = LEAF_ENTRY_FORMAT if isinstance(node, LeafNode) else BRANCH_ENTRY_FORMAT
        header = HEADER_FORMAT.pack(int(node.kind), len(node.entries), node.next_free)
        body = b"".join(entry_format.pack(*entry) for entry in node.entries)
        return (header + body).ljust(self._page_size, b"\x00")

    def unpack(self, raw: bytes) -> Node:
        kind_value, count, next_free = HEADER_FORMAT.unpack_from(raw, 0)
        if kind_value == NodeKind.LEAF:
            leaf_end = HEADER_SIZE + count * LEAF_ENTRY_FORMAT.size
            leaves = map(LeafEntry._make, LEAF_ENTRY_FORMAT.iter_unpack(raw[HEADER_SIZE:leaf_end]))
            return LeafNode(entries=list(leaves), next_free=next_free)
        if kind_value == NodeKind.BRANCH:
            branch_end = HEADER_SIZE + count * BRANCH_ENTRY_FORMAT.size
            branches = map(
                BranchEntry._make, BRANCH_ENTRY_FORMAT.iter_unpack(raw[HEADER_SIZE:branch_end])
            )
            return BranchNode(entries=list(branches), next_free=next_free)
        raise CorruptNodeError(f"tipo de nodo desconocido: {kind_value}")
