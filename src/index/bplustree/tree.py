"""Árbol B+ sobre disco.

Cada nodo es una página. Las claves son únicas: quien necesite indexar una columna con
repetidos compone la clave con la dirección del registro (ver `unclustered.py`), que es lo
mismo que hacen los B+ de los gestores reales.

Ver `README.md` para el recorrido paso a paso de la división y la fusión de nodos.
"""

from __future__ import annotations

import struct
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

from config import EngineConfig
from index.keys import Key, KeyCodec
from storage.pager import HEADER_PAGE_ID, Pager
from storage.types import StorageError

from .node import NO_NODE, Node, NodeCodec, NodeKind

HEADER_FORMAT = struct.Struct("<4sHIIiiQI")
TREE_MAGIC = b"BPT1"
TREE_VERSION = 1


MAX_NODES_SHOWN_PER_LEVEL = 8
MAX_KEYS_SHOWN_PER_NODE = 6


class TreeFormatError(StorageError):
    """El archivo no corresponde a este formato de árbol B+."""


class DuplicateKeyError(StorageError):
    """La clave ya existe y el árbol no admite repetidas."""


class BPlusTree:
    """Índice B+ con claves únicas y valores de tamaño fijo.

    Complejidad, con `N` entradas y orden `m` (entradas por nodo):

    * búsqueda, inserción y borrado: `O(log_m N)` accesos a página;
    * recorrido por rango: `O(log_m N + k/m)` para `k` resultados, siguiendo la lista
      enlazada de hojas;
    * espacio: `O(N/m)` páginas.

    Raises:
        TreeFormatError: si el archivo existente no encaja con la clave o el valor pedidos.
    """

    def __init__(
        self,
        path: Path,
        key_codec: KeyCodec,
        value_size: int,
        config: EngineConfig,
    ) -> None:
        self._pager = Pager(path, config)
        self._codec = NodeCodec(key_codec, value_size, config.page_size)
        self._key_codec = key_codec
        self._value_size = value_size
        self._root: int
        self._free_head: int
        self._entry_count: int
        self._height: int
        if self._pager.page_count == 0:
            self._create()
        else:
            self._read_header()

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def height(self) -> int:
        """Niveles del árbol; una raíz hoja tiene altura 1."""
        return self._height

    @property
    def page_count(self) -> int:
        return self._pager.page_count

    @property
    def leaf_capacity(self) -> int:
        return self._codec.leaf_capacity

    @property
    def internal_capacity(self) -> int:
        return self._codec.internal_capacity

    def insert(self, key: Key, value: bytes) -> None:
        """Inserta la entrada.

        Raises:
            DuplicateKeyError: si la clave ya está en el árbol.
            StorageError: si el valor no mide lo que declara el índice.
        """
        self._check_value(value)
        path, leaf_id, leaf = self._descend(key)
        position = bisect_left(leaf.keys, key)
        if position < len(leaf.keys) and leaf.keys[position] == key:
            raise DuplicateKeyError(f"la clave {key!r} ya existe en el índice")
        leaf.keys.insert(position, key)
        leaf.values.insert(position, value)
        self._entry_count += 1
        if len(leaf.keys) <= self._codec.leaf_capacity:
            self._store(leaf_id, leaf)
        else:
            separator, right_id = self._split_leaf(leaf_id, leaf)
            self._grow(path, leaf_id, separator, right_id)
        self._write_header()

    def search(self, key: Key) -> bytes | None:
        """Valor asociado a la clave, o `None` si no está."""
        _, _, leaf = self._descend(key)
        position = bisect_left(leaf.keys, key)
        if position < len(leaf.keys) and leaf.keys[position] == key:
            return leaf.values[position]
        return None

    def range_scan(self, low: Key | None, high: Key | None) -> Iterator[tuple[Key, bytes]]:
        """Entradas con `low <= clave <= high`, en orden de clave.

        `None` en cualquiera de los extremos significa "sin límite".
        """
        leaf_id, leaf = self._leftmost_leaf(low)
        while leaf_id != NO_NODE:
            for key, value in zip(leaf.keys, leaf.values, strict=True):
                if low is not None and key < low:
                    continue
                if high is not None and key > high:
                    return
                yield key, value
            leaf_id = leaf.next_leaf
            if leaf_id != NO_NODE:
                leaf = self._load(leaf_id)

    def scan(self) -> Iterator[tuple[Key, bytes]]:
        """Todas las entradas en orden de clave."""
        return self.range_scan(None, None)

    def delete(self, key: Key) -> bool:
        """Borra la entrada y reequilibra. Devuelve si existía."""
        path, leaf_id, leaf = self._descend(key)
        position = bisect_left(leaf.keys, key)
        if position >= len(leaf.keys) or leaf.keys[position] != key:
            return False
        del leaf.keys[position]
        del leaf.values[position]
        self._entry_count -= 1
        self._store(leaf_id, leaf)
        self._repair(path, leaf_id, leaf)
        self._write_header()
        return True

    def describe(self) -> dict[str, Any]:
        """Forma real del árbol, nivel por nivel, leída de sus páginas.

        Recorre el árbol en anchura, así que cuesta leer todas sus páginas: es una
        herramienta de inspección, no algo que use una consulta. De cada nivel se muestran
        los primeros nodos y de cada nodo sus primeras claves; los totales son exactos.
        """
        levels: list[dict[str, Any]] = []
        frontier = [self._root]
        while frontier:
            nodes = [self._load(page_id) for page_id in frontier]
            levels.append(
                {
                    "kind": "hojas" if nodes[0].is_leaf else "internos",
                    "node_count": len(nodes),
                    "key_count": sum(len(node.keys) for node in nodes),
                    "nodes": [
                        {
                            "keys": [str(key) for key in node.keys[:MAX_KEYS_SHOWN_PER_NODE]],
                            "key_count": len(node.keys),
                        }
                        for node in nodes[:MAX_NODES_SHOWN_PER_LEVEL]
                    ],
                }
            )
            frontier = [] if nodes[0].is_leaf else [c for node in nodes for c in node.children]
        return {
            "kind": "bplustree",
            "height": self._height,
            "entries": self._entry_count,
            "pages": self._pager.page_count,
            "leaf_capacity": self._codec.leaf_capacity,
            "internal_capacity": self._codec.internal_capacity,
            "levels": levels,
        }

    def flush(self) -> None:
        self._write_header()
        self._pager.flush()

    def close(self) -> None:
        self._write_header()
        self._pager.close()

    def __enter__(self) -> BPlusTree:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _descend(self, key: Key) -> tuple[list[tuple[int, int]], int, Node]:
        """Baja hasta la hoja que le corresponde a la clave, anotando el camino."""
        path: list[tuple[int, int]] = []
        page_id = self._root
        node = self._load(page_id)
        while not node.is_leaf:
            child_index = bisect_right(node.keys, key)
            path.append((page_id, child_index))
            page_id = node.children[child_index]
            node = self._load(page_id)
        return path, page_id, node

    def _leftmost_leaf(self, low: Key | None) -> tuple[int, Node]:
        page_id = self._root
        node = self._load(page_id)
        while not node.is_leaf:
            child_index = 0 if low is None else bisect_right(node.keys, low)
            page_id = node.children[child_index]
            node = self._load(page_id)
        return page_id, node

    def _split_leaf(self, leaf_id: int, leaf: Node) -> tuple[Key, int]:
        middle = (len(leaf.keys) + 1) // 2
        right = Node(
            kind=NodeKind.LEAF,
            keys=leaf.keys[middle:],
            values=leaf.values[middle:],
            next_leaf=leaf.next_leaf,
        )
        del leaf.keys[middle:]
        del leaf.values[middle:]
        right_id = self._allocate()
        leaf.next_leaf = right_id
        self._store(leaf_id, leaf)
        self._store(right_id, right)
        return right.keys[0], right_id

    def _split_internal(self, node_id: int, node: Node) -> tuple[Key, int]:
        middle = len(node.keys) // 2
        separator = node.keys[middle]
        right = Node(
            kind=NodeKind.INTERNAL,
            keys=node.keys[middle + 1 :],
            children=node.children[middle + 1 :],
        )
        del node.keys[middle:]
        del node.children[middle + 1 :]
        right_id = self._allocate()
        self._store(node_id, node)
        self._store(right_id, right)
        return separator, right_id

    def _grow(
        self, path: list[tuple[int, int]], left_id: int, separator: Key, right_id: int
    ) -> None:
        """Sube el separador por el camino, dividiendo los nodos que se desborden."""
        while path:
            parent_id, child_index = path.pop()
            parent = self._load(parent_id)
            parent.keys.insert(child_index, separator)
            parent.children.insert(child_index + 1, right_id)
            if len(parent.keys) <= self._codec.internal_capacity:
                self._store(parent_id, parent)
                return
            left_id = parent_id
            separator, right_id = self._split_internal(parent_id, parent)
        self._replace_root(left_id, separator, right_id)

    def _replace_root(self, left_id: int, separator: Key, right_id: int) -> None:
        root_id = self._allocate()
        self._store(
            root_id,
            Node(kind=NodeKind.INTERNAL, keys=[separator], children=[left_id, right_id]),
        )
        self._root = root_id
        self._height += 1

    def _repair(self, path: list[tuple[int, int]], page_id: int, node: Node) -> None:
        """Devuelve el árbol a su ocupación mínima tras un borrado."""
        while path and len(node.keys) < self._minimum_keys(node):
            parent_id, child_index = path.pop()
            self._fix_child(parent_id, child_index)
            page_id = parent_id
            node = self._load(parent_id)
        if not path:
            self._shrink_root(page_id, node)

    def _fix_child(self, parent_id: int, child_index: int) -> None:
        parent = self._load(parent_id)
        child_id = parent.children[child_index]
        child = self._load(child_id)
        if self._try_borrow(parent, child_index, child_id, child):
            self._store(parent_id, parent)
            return
        if child_index > 0:
            self._merge(parent, child_index - 1)
        else:
            self._merge(parent, child_index)
        self._store(parent_id, parent)

    def _try_borrow(self, parent: Node, child_index: int, child_id: int, child: Node) -> bool:
        if child_index > 0:
            left_id = parent.children[child_index - 1]
            left = self._load(left_id)
            if len(left.keys) > self._minimum_keys(left):
                self._borrow_from_left(parent, child_index, child_id, child, left_id, left)
                return True
        if child_index + 1 < len(parent.children):
            right_id = parent.children[child_index + 1]
            right = self._load(right_id)
            if len(right.keys) > self._minimum_keys(right):
                self._borrow_from_right(parent, child_index, child_id, child, right_id, right)
                return True
        return False

    def _borrow_from_left(
        self, parent: Node, child_index: int, child_id: int, child: Node, left_id: int, left: Node
    ) -> None:
        if child.is_leaf:
            child.keys.insert(0, left.keys.pop())
            child.values.insert(0, left.values.pop())
            parent.keys[child_index - 1] = child.keys[0]
        else:
            child.keys.insert(0, parent.keys[child_index - 1])
            child.children.insert(0, left.children.pop())
            parent.keys[child_index - 1] = left.keys.pop()
        self._store(left_id, left)
        self._store(child_id, child)

    def _borrow_from_right(
        self, parent: Node, child_index: int, child_id: int, child: Node, right_id: int, right: Node
    ) -> None:
        if child.is_leaf:
            child.keys.append(right.keys.pop(0))
            child.values.append(right.values.pop(0))
            parent.keys[child_index] = right.keys[0]
        else:
            child.keys.append(parent.keys[child_index])
            child.children.append(right.children.pop(0))
            parent.keys[child_index] = right.keys.pop(0)
        self._store(right_id, right)
        self._store(child_id, child)

    def _merge(self, parent: Node, separator_index: int) -> None:
        left_id = parent.children[separator_index]
        right_id = parent.children[separator_index + 1]
        left = self._load(left_id)
        right = self._load(right_id)
        if left.is_leaf:
            left.keys.extend(right.keys)
            left.values.extend(right.values)
            left.next_leaf = right.next_leaf
        else:
            left.keys.append(parent.keys[separator_index])
            left.keys.extend(right.keys)
            left.children.extend(right.children)
        del parent.keys[separator_index]
        del parent.children[separator_index + 1]
        self._store(left_id, left)
        self._release(right_id)

    def _shrink_root(self, page_id: int, node: Node) -> None:
        if node.is_leaf or node.keys:
            return
        self._root = node.children[0]
        self._height -= 1
        self._release(page_id)

    def _minimum_keys(self, node: Node) -> int:
        if node.is_leaf:
            return (self._codec.leaf_capacity + 1) // 2
        return self._codec.internal_capacity // 2

    def _check_value(self, value: bytes) -> None:
        if len(value) != self._value_size:
            raise StorageError(
                f"el valor ocupa {len(value)} bytes y el índice espera {self._value_size}"
            )

    def _allocate(self) -> int:
        if self._free_head == NO_NODE:
            return self._pager.allocate()
        page_id = self._free_head
        self._free_head = self._load(page_id).next_leaf
        return page_id

    def _release(self, page_id: int) -> None:
        self._store(page_id, Node(kind=NodeKind.LEAF, next_leaf=self._free_head))
        self._free_head = page_id

    def _load(self, page_id: int) -> Node:
        return self._codec.unpack(self._pager.read(page_id))

    def _store(self, page_id: int, node: Node) -> None:
        self._pager.write(page_id, self._codec.pack(node))

    def _create(self) -> None:
        self._pager.allocate()
        self._free_head = NO_NODE
        self._entry_count = 0
        self._height = 1
        self._root = self._pager.allocate()
        self._store(self._root, Node(kind=NodeKind.LEAF))
        self._write_header()

    def _read_header(self) -> None:
        raw = self._pager.read(HEADER_PAGE_ID)
        magic, version, key_size, value_size, root, free_head, count, height = (
            HEADER_FORMAT.unpack_from(raw, 0)
        )
        if magic != TREE_MAGIC or version != TREE_VERSION:
            raise TreeFormatError(f"{self._pager.path.name} no es un árbol B+ válido")
        if key_size != self._key_codec.size or value_size != self._value_size:
            raise TreeFormatError(
                f"el índice guarda claves de {key_size} y valores de {value_size} bytes; "
                f"se pidieron {self._key_codec.size} y {self._value_size}"
            )
        self._root = int(root)
        self._free_head = int(free_head)
        self._entry_count = int(count)
        self._height = int(height)

    def _write_header(self) -> None:
        raw = bytearray(self._pager.page_size)
        HEADER_FORMAT.pack_into(
            raw,
            0,
            TREE_MAGIC,
            TREE_VERSION,
            self._key_codec.size,
            self._value_size,
            self._root,
            self._free_head,
            self._entry_count,
            self._height,
        )
        self._pager.write(HEADER_PAGE_ID, bytes(raw))
