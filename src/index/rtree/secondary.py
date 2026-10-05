"""Índice espacial sobre una columna POINT de una tabla.

El R-Tree solo sabe de puntos y direcciones; esta clase es la que lo conecta con las filas:
saca el punto de cada registro, deja fuera los NULL y traduce una consulta por polígono a
lo que el árbol sabe hacer, que es buscar por rectángulo.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from types import TracebackType
from typing import Any

from config import EngineConfig
from spatial.geometry import Point, Polygon, Rectangle
from spatial.metrics import Metric
from storage.record import RecordSerializer
from storage.record_id import RecordId
from storage.types import FieldType, StorageError

from .tree import Neighbor, RTree, SearchStats


class NotSpatialColumnError(StorageError):
    """La columna no guarda puntos, así que no se puede indexar con un R-Tree."""


class SpatialIndex:
    """Índice secundario R-Tree: apunta a las filas de un heap file por su ubicación.

    Raises:
        NotSpatialColumnError: si la columna no es de tipo POINT.
    """

    def __init__(
        self,
        path: Path,
        serializer: RecordSerializer,
        column: str,
        config: EngineConfig,
    ) -> None:
        self._serializer = serializer
        self._position = serializer.schema.position_of(column)
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

    def insert(self, record: bytes, record_id: RecordId) -> None:
        point = self._point_of(record)
        if point is not None:
            self._tree.insert(point, record_id)

    def delete(self, record: bytes, record_id: RecordId) -> bool:
        point = self._point_of(record)
        return point is not None and self._tree.delete(point, record_id)

    def search(self, value: Any) -> list[RecordId]:
        """Direcciones de las filas situadas exactamente en ese punto."""
        if value is None:
            return []
        exact = Rectangle.around(Point(*value))
        return [record_id for _, record_id in self._tree.search_rectangle(exact)]

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

    def close(self) -> None:
        self._tree.close()

    def __enter__(self) -> SpatialIndex:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _located(self, rows: Iterable[tuple[bytes, RecordId]]) -> Iterator[tuple[Point, RecordId]]:
        for record, record_id in rows:
            point = self._point_of(record)
            if point is not None:
                yield point, record_id

    def _point_of(self, record: bytes) -> Point | None:
        value = self._serializer.unpack_field(record, self._position)
        return value if isinstance(value, Point) else None
