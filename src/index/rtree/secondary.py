"""Índice espacial sobre una columna POINT de una tabla.

El R-Tree solo sabe de puntos y direcciones; esta clase es la que lo conecta con las filas:
saca el punto de cada registro, deja fuera los NULL y traduce una consulta por polígono a
lo que el árbol sabe hacer, que es buscar por rectángulo.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from config import EngineConfig
from index.secondary import ColumnIndex
from spatial.geometry import Point, Polygon, Rectangle
from spatial.metrics import Metric
from storage.record import FieldReader
from storage.record_id import RecordId
from storage.types import FieldType, Key, StorageError

from .tree import Neighbor, RTree, SearchStats


class NotSpatialColumnError(StorageError):
    """La columna no guarda puntos, así que no se puede indexar con un R-Tree."""


class SpatialIndex(ColumnIndex):
    """Índice secundario R-Tree: apunta a las filas de un heap file por su ubicación.

    Raises:
        NotSpatialColumnError: si la columna no es de tipo POINT.
    """

    def __init__(
        self,
        path: Path,
        serializer: FieldReader,
        column: str,
        config: EngineConfig,
    ) -> None:
        super().__init__(serializer, column)
        field = serializer.schema.fields[self._position]
        if field.type is not FieldType.POINT:
            raise NotSpatialColumnError(
                f"un índice RTREE necesita una columna POINT y '{field.name}' es "
                f"{field.type.value}"
            )
        self._tree = RTree(path, config)

    @property
    def entry_count(self) -> int:
        return self._tree.entry_count

    @property
    def node_count(self) -> int:
        return self._tree.node_count

    def build(self, rows: Iterable[tuple[bytes, RecordId]]) -> None:
        """Indexa de una vez todas las filas de la tabla, con carga masiva."""
        self._tree.bulk_load(self._located(rows))

    def within_radius(
        self, center: Point, radius: float, metric: Metric, stats: SearchStats | None = None
    ) -> Iterator[Neighbor]:
        """Filas a distancia menor o igual que `radius` del centro."""
        return self._tree.search_radius(center, radius, metric, stats)

    def nearest(
        self, center: Point, metric: Metric, stats: SearchStats | None = None
    ) -> Iterator[Neighbor]:
        """Filas de la más cercana a la más lejana, calculadas bajo demanda."""
        return self._tree.nearest(center, metric, stats)

    def within_polygon(
        self, polygon: Polygon, stats: SearchStats | None = None
    ) -> Iterator[RecordId]:
        """Filas dentro del polígono o sobre su borde.

        Filtrar y refinar: el árbol descarta por el rectángulo que encierra al polígono,
        que es barato, y solo los puntos que pasan ese filtro pagan la comprobación exacta.
        """
        for point, record_id in self._tree.search_rectangle(polygon.bounding_box, stats):
            if polygon.contains(point):
                yield record_id

    def describe(self) -> dict[str, Any]:
        return self._tree.describe()

    def flush(self) -> None:
        self._tree.flush()

    def close(self) -> None:
        self._tree.close()

    def _add(self, value: Key, record_id: RecordId) -> None:
        self._tree.insert(Point(*value), record_id)

    def _remove(self, value: Key, record_id: RecordId) -> bool:
        return self._tree.delete(Point(*value), record_id)

    def _find(self, value: Key) -> list[RecordId]:
        """Direcciones de las filas situadas exactamente en ese punto."""
        exact = Rectangle.around(Point(*value))
        return [record_id for _, record_id in self._tree.search_rectangle(exact)]

    def _located(self, rows: Iterable[tuple[bytes, RecordId]]) -> Iterator[tuple[Point, RecordId]]:
        for record, record_id in rows:
            value = self._value_of(record)
            if value is not None:
                yield Point(*value), record_id
