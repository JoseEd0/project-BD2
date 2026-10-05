"""R-Tree sobre disco para puntos en dos dimensiones (Guttman, 1984).

Cada nodo es una página. Las hojas guardan puntos; cada nodo interno guarda el rectángulo
que encierra a cada hijo. Buscar es bajar solo por los hijos cuyo rectángulo puede contener
resultados, que es lo que evita recorrer todos los puntos.

El árbol no sabe de métricas: las búsquedas por radio y por cercanía reciben la métrica y
solo le piden dos cosas, la distancia entre dos puntos y la menor distancia de un punto a
un rectángulo. Por eso el mismo índice sirve para la distancia euclidiana y para Haversine.

Ver `README.md` para el recorrido paso a paso de la inserción, la división y el k-NN.
"""

from __future__ import annotations

import heapq
import struct
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, NamedTuple

from config import EngineConfig
from external.sort import ExternalSorter
from spatial.geometry import Point, Rectangle
from spatial.metrics import Metric
from storage.pager import HEADER_PAGE_ID, Pager
from storage.record_id import RecordId
from storage.types import StorageError

from .bulk import tile, tile_sorted
from .node import (
    LEAF_ENTRY_FORMAT,
    MINIMUM_ENTRIES_PER_NODE,
    NO_NODE,
    BranchEntry,
    BranchNode,
    CorruptNodeError,
    LeafEntry,
    LeafNode,
    Node,
    NodeCodec,
    branch_entry,
    leaf_entry,
)
from .split import choose_subtree, quadratic_split

HEADER_FORMAT = struct.Struct("<4sHiiQII")
TREE_MAGIC = b"RTR1"
TREE_VERSION = 1
LEAF_LEVEL = 0
BUILD_SUFFIX = ".build"
MAX_NODES_SHOWN_PER_LEVEL = 400
# Página que un nodo lleva en la cola del k-NN donde un punto lleva la de su fila:
# ordena antes que cualquier página real, para que a igual distancia el nodo salga primero.
BEFORE_ANY_PAGE = -1

Entry = LeafEntry | BranchEntry


class TreeFormatError(StorageError):
    """El archivo no corresponde a este formato de R-Tree."""


@dataclass(slots=True)
class SearchStats:
    """Trabajo que costó una búsqueda, para enseñarlo en el plan de ejecución."""

    nodes_visited: int = 0


class Neighbor(NamedTuple):
    """Un punto encontrado, con su distancia al punto de la consulta."""

    distance: float
    point: Point
    record_id: RecordId


@dataclass(slots=True)
class _Step:
    """Un nodo interno del camino de bajada y la entrada por la que se siguió."""

    page_id: int
    node: BranchNode
    chosen: int


class RTree:
    """Índice espacial de puntos con direcciones de fila.

    Complejidad, con `N` puntos y `M` entradas por nodo:

    * inserción y borrado: `O(log_M N)` accesos a página, más `O(M²)` de cómputo cuando
      un nodo se divide;
    * búsqueda por rectángulo, radio o polígono: `O(log_M N + k)` en el caso típico, y
      hasta `O(N)` si los MBR se solapan mucho;
    * k vecinos más cercanos: los nodos se abren por orden de cercanía, así que se
      visitan pocos más de los que contienen la respuesta;
    * espacio: `O(N/M)` páginas.

    Admite el mismo punto varias veces con direcciones distintas.

    Raises:
        TreeFormatError: si el archivo existente no es un R-Tree de este formato.
    """

    def __init__(self, path: Path, config: EngineConfig) -> None:
        self._config = config
        self._codec = NodeCodec(config.page_size)
        self._pager = Pager(path, config)
        self._minimum_fill = config.rtree_min_fill
        self._root: int
        self._free_head: int
        self._entry_count: int
        self._height: int
        self._node_count: int
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
    def node_count(self) -> int:
        return self._node_count

    @property
    def page_count(self) -> int:
        return self._pager.page_count

    @property
    def leaf_capacity(self) -> int:
        return self._codec.leaf_capacity

    @property
    def branch_capacity(self) -> int:
        return self._codec.branch_capacity

    def insert(self, point: Point, record_id: RecordId) -> None:
        """Añade el punto al árbol, dividiendo los nodos que se desborden."""
        self._place(leaf_entry(point, record_id), LEAF_LEVEL)
        self._entry_count += 1
        self._write_header()

    def bulk_load(self, entries: Iterable[tuple[Point, RecordId]]) -> None:
        """Construye el árbol de una vez a partir de todos sus puntos (STR).

        Los puntos se ordenan por latitud con el ordenamiento externo, de modo que nunca
        hay más de una franja de hojas en memoria. Las hojas se llenan a `rtree_bulk_fill`
        para que las inserciones posteriores no las dividan de inmediato. Coste
        `O(N log N)` de ordenar, frente a las `N` bajadas por el árbol de insertar uno a uno.

        Raises:
            StorageError: si el árbol ya tiene puntos.
        """
        if self._entry_count > 0:
            raise StorageError("la carga masiva solo se puede hacer sobre un R-Tree vacío")
        self._release(self._root)
        level = self._build_leaves(entries)
        self._height = 1
        while len(level) > 1:
            groups = tile(level, *self._bulk_limits(self._codec.branch_capacity))
            level = [self._add_node(BranchNode(group)) for group in groups]
            self._height += 1
        self._root = level[0].child if level else self._new_empty_leaf()
        self._write_header()

    def delete(self, point: Point, record_id: RecordId) -> bool:
        """Quita la entrada y condensa el árbol. Devuelve si existía.

        Un nodo que queda por debajo de su ocupación mínima se disuelve y sus entradas se
        reinsertan, cada una en el nivel del que venía.
        """
        target = leaf_entry(point, record_id)
        found = self._find_leaf(self._root, target, [])
        if found is None:
            return False
        ancestors, page_id, leaf = found
        leaf.entries.remove(target)
        self._entry_count -= 1
        for level, orphans in self._condense(ancestors, page_id, leaf):
            for orphan in orphans:
                self._place(orphan, level)
        self._shrink_root()
        self._write_header()
        return True

    def search_rectangle(
        self, rectangle: Rectangle, stats: SearchStats | None = None
    ) -> Iterator[tuple[Point, RecordId]]:
        """Puntos que caen dentro del rectángulo, bordes incluidos."""
        pending = [self._root]
        while pending:
            node = self._visit(pending.pop(), stats)
            if isinstance(node, BranchNode):
                pending.extend(
                    entry.child for entry in node.entries if rectangle.intersects(entry.bounds)
                )
                continue
            for entry in node.entries:
                if rectangle.contains_point(entry.point):
                    yield entry.point, entry.record_id

    def search_radius(
        self, center: Point, radius: float, metric: Metric, stats: SearchStats | None = None
    ) -> Iterator[Neighbor]:
        """Puntos a distancia menor o igual que `radius` del centro, sin orden.

        Un subárbol se descarta cuando ni el punto más cercano de su MBR entra en el radio.
        """
        pending = [self._root]
        while pending:
            node = self._visit(pending.pop(), stats)
            if isinstance(node, BranchNode):
                pending.extend(
                    entry.child
                    for entry in node.entries
                    if metric.min_distance(center, entry.bounds) <= radius
                )
                continue
            for entry in node.entries:
                distance = metric.distance(center, entry.point)
                if distance <= radius:
                    yield Neighbor(distance, entry.point, entry.record_id)

    def nearest(
        self, center: Point, metric: Metric, stats: SearchStats | None = None
    ) -> Iterator[Neighbor]:
        """Todos los puntos, del más cercano al más lejano, calculados bajo demanda.

        Búsqueda primero-el-mejor (Hjaltason y Samet, 1999): una cola de prioridad mezcla
        nodos y puntos. Un nodo entra con la menor distancia posible a su MBR, que es una
        cota inferior de todo lo que contiene; un punto, con su distancia real. Cuando sale
        un punto, nada de lo que queda en la cola puede estar más cerca. Quien solo quiere
        `k` vecinos deja de pedir tras el k-ésimo y el resto del árbol no se llega a abrir.

        Los puntos a la misma distancia salen en orden de dirección, que es el orden en
        que los dejaría un ordenamiento estable de la tabla: así un `LIMIT` que corta en
        medio de un empate devuelve las mismas filas con índice que sin él. Para eso, a
        igual distancia un nodo sale antes que un punto: cuando sale el primer punto de
        un empate, todos los demás ya están en la cola.
        """
        queue: list[tuple[float, int, int, int | LeafEntry]] = [
            (0.0, BEFORE_ANY_PAGE, self._root, self._root)
        ]
        while queue:
            distance, _, _, item = heapq.heappop(queue)
            if isinstance(item, LeafEntry):
                yield Neighbor(distance, item.point, item.record_id)
                continue
            node = self._visit(item, stats)
            if isinstance(node, BranchNode):
                for branch in node.entries:
                    bound = metric.min_distance(center, branch.bounds)
                    heapq.heappush(queue, (bound, BEFORE_ANY_PAGE, branch.child, branch.child))
                continue
            for leaf in node.entries:
                reached = metric.distance(center, leaf.point)
                heapq.heappush(queue, (reached, leaf.page_id, leaf.slot, leaf))

    def scan(self) -> Iterator[tuple[Point, RecordId]]:
        """Todos los puntos indexados, sin orden garantizado."""
        pending = [self._root]
        while pending:
            node = self._load(pending.pop())
            if isinstance(node, BranchNode):
                pending.extend(entry.child for entry in node.entries)
                continue
            for entry in node.entries:
                yield entry.point, entry.record_id

    def describe(self) -> dict[str, Any]:
        """Forma real del árbol, nivel por nivel, con el MBR de cada nodo.

        Recorre el árbol en anchura, así que cuesta leer todas sus páginas: es una
        herramienta de inspección, no algo que use una consulta. De cada nivel se detallan
        los primeros nodos; los totales son exactos.
        """
        levels: list[dict[str, Any]] = []
        frontier = [self._root]
        while frontier:
            nodes = [self._load(page_id) for page_id in frontier]
            levels.append(_describe_level(nodes))
            frontier = [
                entry.child
                for node in nodes
                if isinstance(node, BranchNode)
                for entry in node.entries
            ]
        return {
            "kind": "rtree",
            "height": self._height,
            "entries": self._entry_count,
            "nodes": self._node_count,
            "pages": self._pager.page_count,
            "leaf_capacity": self._codec.leaf_capacity,
            "branch_capacity": self._codec.branch_capacity,
            "minimum_fill": self._minimum_fill,
            "levels": levels,
        }

    def close(self) -> None:
        self._write_header()
        self._pager.close()

    def __enter__(self) -> RTree:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _place(self, entry: Entry, level: int) -> None:
        """Inserta la entrada en un nodo del nivel dado y repara el árbol hacia arriba.

        `level` cuenta desde las hojas. Los puntos van al nivel 0; al condensar tras un
        borrado, una entrada interna vuelve al nivel del que salió para que todas las
        hojas sigan a la misma profundidad.
        """
        ancestors, page_id, node = self._descend(entry.bounds, level)
        _append(node, entry)
        while True:
            sibling = self._store_or_split(page_id, node)
            if not ancestors:
                if sibling is not None:
                    self._grow_root(page_id, node, sibling)
                return
            step = ancestors.pop()
            current = step.node.entries[step.chosen]
            cover = current.bounds.union(entry.bounds) if sibling is None else _cover(node)
            updated = branch_entry(cover, page_id)
            if sibling is None and updated == current:
                return
            step.node.entries[step.chosen] = updated
            if sibling is not None:
                step.node.entries.append(sibling)
            page_id, node = step.page_id, step.node

    def _descend(self, bounds: Rectangle, level: int) -> tuple[list[_Step], int, Node]:
        """Baja desde la raíz hasta un nodo del nivel pedido, anotando el camino."""
        ancestors: list[_Step] = []
        page_id = self._root
        node = self._load(page_id)
        for _ in range(self._height - 1 - level):
            branch = _as_branch(node)
            chosen = choose_subtree(branch.entries, bounds)
            ancestors.append(_Step(page_id, branch, chosen))
            page_id = branch.entries[chosen].child
            node = self._load(page_id)
        return ancestors, page_id, node

    def _store_or_split(self, page_id: int, node: Node) -> BranchEntry | None:
        """Guarda el nodo; si se desbordó lo divide y devuelve la entrada del nodo nuevo."""
        if len(node.entries) <= self._capacity(node):
            self._store(page_id, node)
            return None
        sibling = self._split_off(node)
        sibling_id = self._allocate()
        self._store(page_id, node)
        self._store(sibling_id, sibling)
        return branch_entry(_cover(sibling), sibling_id)

    def _split_off(self, node: Node) -> Node:
        minimum = self._minimum_entries(node)
        if isinstance(node, LeafNode):
            node.entries, moved_leaves = quadratic_split(node.entries, minimum)
            return LeafNode(moved_leaves)
        node.entries, moved_branches = quadratic_split(node.entries, minimum)
        return BranchNode(moved_branches)

    def _grow_root(self, left_id: int, left: Node, sibling: BranchEntry) -> None:
        root_id = self._allocate()
        self._store(root_id, BranchNode([branch_entry(_cover(left), left_id), sibling]))
        self._root = root_id
        self._height += 1

    def _build_leaves(self, entries: Iterable[tuple[Point, RecordId]]) -> list[BranchEntry]:
        """Escribe el nivel de hojas y devuelve una entrada por cada hoja creada."""
        workspace = self._pager.path.with_name(self._pager.path.name + BUILD_SUFFIX)
        sorter = ExternalSorter(workspace, LEAF_ENTRY_FORMAT.size, _latitude_of, self._config)
        with sorter:
            packed = (LEAF_ENTRY_FORMAT.pack(*leaf_entry(*entry)) for entry in entries)
            ordered = sorter.sort(self._counting(packed))
            leaves = map(LeafEntry._make, map(LEAF_ENTRY_FORMAT.unpack, ordered))
            limits = self._bulk_limits(self._codec.leaf_capacity)
            groups = tile_sorted(leaves, self._entry_count, *limits)
            return [self._add_node(LeafNode(group)) for group in groups]

    def _counting(self, records: Iterable[bytes]) -> Iterator[bytes]:
        """El ordenamiento consume toda la entrada antes de devolver nada, así que al
        empezar a leer su salida ya se sabe cuántos puntos hay."""
        for record in records:
            self._entry_count += 1
            yield record

    def _bulk_limits(self, capacity: int) -> tuple[int, int]:
        """Entradas por nodo y mínimo admisible durante la carga masiva.

        El tamaño nunca baja del doble del mínimo: es lo que garantiza que, al repartir el
        último grupo con el anterior, las dos mitades sigan por encima del mínimo.
        """
        minimum = self._minimum_for(capacity)
        wanted = int(capacity * self._config.rtree_bulk_fill)
        return min(capacity, max(2 * minimum, wanted)), minimum

    def _add_node(self, node: Node) -> BranchEntry:
        page_id = self._allocate()
        self._store(page_id, node)
        return branch_entry(_cover(node), page_id)

    def _new_empty_leaf(self) -> int:
        page_id = self._allocate()
        self._store(page_id, LeafNode())
        return page_id

    def _find_leaf(
        self, page_id: int, target: LeafEntry, ancestors: list[_Step]
    ) -> tuple[list[_Step], int, LeafNode] | None:
        """Hoja que contiene la entrada. Puede haber que probar varias ramas: los MBR de
        dos hermanos se solapan, y el punto puede caer dentro de ambos."""
        node = self._load(page_id)
        if isinstance(node, LeafNode):
            return (list(ancestors), page_id, node) if target in node.entries else None
        for position, entry in enumerate(node.entries):
            if not entry.bounds.contains_point(target.point):
                continue
            ancestors.append(_Step(page_id, node, position))
            found = self._find_leaf(entry.child, target, ancestors)
            if found is not None:
                return found
            ancestors.pop()
        return None

    def _condense(
        self, ancestors: list[_Step], page_id: int, node: Node
    ) -> list[tuple[int, Sequence[Entry]]]:
        """Sube desde la hoja disolviendo los nodos que quedaron bajo el mínimo.

        Devuelve las entradas huérfanas con el nivel al que hay que reinsertarlas.
        """
        orphans: list[tuple[int, Sequence[Entry]]] = []
        level = LEAF_LEVEL
        while ancestors:
            step = ancestors.pop()
            if len(node.entries) < self._minimum_entries(node):
                orphans.append((level, node.entries))
                del step.node.entries[step.chosen]
                self._release(page_id)
            else:
                self._store(page_id, node)
                step.node.entries[step.chosen] = branch_entry(_cover(node), page_id)
            page_id, node, level = step.page_id, step.node, level + 1
        self._store(page_id, node)
        return orphans

    def _shrink_root(self) -> None:
        root = self._load(self._root)
        while isinstance(root, BranchNode) and len(root.entries) == 1:
            obsolete = self._root
            self._root = root.entries[0].child
            self._height -= 1
            self._release(obsolete)
            root = self._load(self._root)

    def _capacity(self, node: Node) -> int:
        if isinstance(node, LeafNode):
            return self._codec.leaf_capacity
        return self._codec.branch_capacity

    def _minimum_entries(self, node: Node) -> int:
        return self._minimum_for(self._capacity(node))

    def _minimum_for(self, capacity: int) -> int:
        return max(MINIMUM_ENTRIES_PER_NODE, int(capacity * self._minimum_fill))

    def _visit(self, page_id: int, stats: SearchStats | None) -> Node:
        if stats is not None:
            stats.nodes_visited += 1
        return self._load(page_id)

    def _allocate(self) -> int:
        self._node_count += 1
        if self._free_head == NO_NODE:
            return self._pager.allocate()
        page_id = self._free_head
        self._free_head = self._load(page_id).next_free
        return page_id

    def _release(self, page_id: int) -> None:
        self._store(page_id, LeafNode(next_free=self._free_head))
        self._free_head = page_id
        self._node_count -= 1

    def _load(self, page_id: int) -> Node:
        return self._codec.unpack(self._pager.read(page_id))

    def _store(self, page_id: int, node: Node) -> None:
        self._pager.write(page_id, self._codec.pack(node))

    def _create(self) -> None:
        self._pager.allocate()
        self._free_head = NO_NODE
        self._entry_count = 0
        self._height = 1
        self._node_count = 0
        self._root = self._new_empty_leaf()
        self._write_header()

    def _read_header(self) -> None:
        raw = self._pager.read(HEADER_PAGE_ID)
        magic, version, root, free_head, count, height, nodes = HEADER_FORMAT.unpack_from(raw, 0)
        if magic != TREE_MAGIC or version != TREE_VERSION:
            raise TreeFormatError(f"{self._pager.path.name} no es un R-Tree válido")
        self._root = int(root)
        self._free_head = int(free_head)
        self._entry_count = int(count)
        self._height = int(height)
        self._node_count = int(nodes)

    def _write_header(self) -> None:
        raw = bytearray(self._pager.page_size)
        HEADER_FORMAT.pack_into(
            raw,
            0,
            TREE_MAGIC,
            TREE_VERSION,
            self._root,
            self._free_head,
            self._entry_count,
            self._height,
            self._node_count,
        )
        self._pager.write(HEADER_PAGE_ID, bytes(raw))


def _append(node: Node, entry: Entry) -> None:
    if isinstance(node, LeafNode) and isinstance(entry, LeafEntry):
        node.entries.append(entry)
        return
    if isinstance(node, BranchNode) and isinstance(entry, BranchEntry):
        node.entries.append(entry)
        return
    raise CorruptNodeError("la entrada no corresponde al tipo del nodo donde se inserta")


def _as_branch(node: Node) -> BranchNode:
    if isinstance(node, LeafNode):
        raise CorruptNodeError("se esperaba un nodo interno y la página contiene una hoja")
    return node


def _latitude_of(record: bytes) -> float:
    latitude: float = LEAF_ENTRY_FORMAT.unpack_from(record, 0)[0]
    return latitude


def _cover(node: Node) -> Rectangle:
    return Rectangle.enclosing(entry.bounds for entry in node.entries)


def _describe_level(nodes: Sequence[Node]) -> dict[str, Any]:
    return {
        "kind": "hojas" if isinstance(nodes[0], LeafNode) else "internos",
        "node_count": len(nodes),
        "entry_count": sum(len(node.entries) for node in nodes),
        "nodes": [
            {
                "bounds": list(_cover(node)) if node.entries else None,
                "entry_count": len(node.entries),
            }
            for node in nodes[:MAX_NODES_SHOWN_PER_LEVEL]
        ],
    }
