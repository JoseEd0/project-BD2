"""Operadores de acceso que resuelven una consulta espacial con el R-Tree.

Son caminos de acceso, igual que `IndexLookup` o `PrimaryKeyRange`: sustituyen al recorrido
completo cuando el planificador reconoce una búsqueda por radio, por cercanía o por
polígono sobre una columna con índice `RTREE`. Tras ejecutarse, cada uno informa de cuántos
nodos del árbol tuvo que abrir, que es la medida de cuánto se ahorró.
"""

from __future__ import annotations

from collections.abc import Iterator

from index.rtree import SearchStats
from query.operators import TableOperator
from query.plan import PlanNode
from query.spatial import DistanceTarget, PolygonSearch, RadiusSearch
from query.table import Table
from storage.record import Record


class SpatialScan(TableOperator):
    """Base de los accesos por R-Tree: lleva la cuenta de los nodos visitados."""

    def __init__(self, table: Table, alias: str, index_name: str) -> None:
        super().__init__(table, alias)
        self._index_name = index_name
        self._stats = SearchStats()

    def _describe(self, what: str) -> str:
        """Detalle del plan; tras ejecutar, con los nodos del árbol que se abrieron."""
        column = self._table.definition.index_on_name(self._index_name).column
        detail = f"{self._table.name}.{column} {what} (índice {self._index_name}, RTREE)"
        if self._actual_rows is None:
            return detail
        total = self._table.spatial_index(self._index_name).node_count
        return f"{detail} · {self._stats.nodes_visited} de {total} nodos visitados"


class SpatialRangeScan(SpatialScan):
    """Filas dentro de un radio alrededor de un punto."""

    def __init__(self, table: Table, alias: str, index_name: str, search: RadiusSearch) -> None:
        super().__init__(table, alias, index_name)
        self._search = search

    def _produce(self) -> Iterator[Record]:
        target = self._search.target
        return self._table.rows_within_radius(
            self._index_name, target.center, self._search.radius, target.metric, self._stats
        )

    def _plan_node(self) -> PlanNode:
        target = self._search.target
        what = (
            f"a {self._search.radius:g} {target.metric.unit} o menos de {target.center} "
            f"[{target.metric.name}]"
        )
        return PlanNode("SpatialRangeScan", self._describe(what))


class SpatialNearestScan(SpatialScan):
    """Filas en orden creciente de distancia a un punto: el k-NN del R-Tree.

    Entrega las filas ya ordenadas, así que sustituye a la vez al recorrido y al
    ordenamiento. Con un `LIMIT k` encima se le dejan de pedir filas tras la k-ésima.
    """

    def __init__(self, table: Table, alias: str, index_name: str, target: DistanceTarget) -> None:
        super().__init__(table, alias, index_name)
        self._target = target

    def _produce(self) -> Iterator[Record]:
        return self._table.rows_by_distance(
            self._index_name, self._target.center, self._target.metric, self._stats
        )

    def _plan_node(self) -> PlanNode:
        what = f"por cercanía a {self._target.center} [{self._target.metric.name}]"
        return PlanNode("SpatialNearestScan", self._describe(what))


class SpatialPolygonScan(SpatialScan):
    """Filas cuyo punto cae dentro de un polígono."""

    def __init__(self, table: Table, alias: str, index_name: str, search: PolygonSearch) -> None:
        super().__init__(table, alias, index_name)
        self._search = search

    def _produce(self) -> Iterator[Record]:
        return self._table.rows_within_polygon(self._index_name, self._search.polygon, self._stats)

    def _plan_node(self) -> PlanNode:
        what = f"dentro de un polígono de {len(self._search.polygon.vertices)} vértices"
        return PlanNode("SpatialPolygonScan", self._describe(what))
