"""Del resultado del motor a la respuesta JSON que consume el frontend."""

from __future__ import annotations

from typing import Any

from api.schemas import (
    OverlayInfo,
    PlanInfo,
    PointInfo,
    QueryResponse,
    SpatialInfo,
    StatementOutcome,
)
from query.engine import QueryResult
from query.plan import PlanNode
from query.spatial import SpatialView
from spatial.geometry import Point
from sql.nodes import Statement
from storage.types import Value

ELAPSED_DECIMALS = 3


def describe_statement(statement: Statement) -> str:
    return type(statement).__name__.replace("Statement", "")


def as_response(
    results: list[QueryResult], labels: list[str], in_transaction: bool, max_rows: int
) -> QueryResponse:
    """Una respuesta para todo un script: las filas y el plan son los de la última consulta.

    Viajan como mucho `max_rows` filas; `total_rows` dice cuántas devolvió la consulta, para
    que el cliente avise de que está viendo solo una parte.
    """
    last_query = next((item for item in reversed(results) if item.plan is not None), None)
    outcomes = [
        StatementOutcome(
            sql=label,
            message=item.message,
            affected_rows=item.affected_rows,
            elapsed_ms=round(item.elapsed_ms, ELAPSED_DECIMALS),
            returned_rows=len(item.rows),
        )
        for item, label in zip(results, labels, strict=True)
    ]
    total = round(sum(item.elapsed_ms for item in results), ELAPSED_DECIMALS)
    if last_query is None:
        message = " · ".join(item.message for item in results if item.message)
        return QueryResponse(
            statements=outcomes,
            message=message,
            affected_rows=sum(item.affected_rows for item in results),
            elapsed_ms=total,
            in_transaction=in_transaction,
        )
    return QueryResponse(
        statements=outcomes,
        columns=list(last_query.columns),
        rows=[[_as_json(value) for value in row] for row in last_query.rows[:max_rows]],
        total_rows=len(last_query.rows),
        plan=_as_plan(last_query.plan),
        message=last_query.message,
        affected_rows=last_query.affected_rows,
        elapsed_ms=total,
        in_transaction=in_transaction,
        spatial=_as_spatial(last_query.spatial),
    )


def _as_spatial(view: SpatialView | None) -> SpatialInfo | None:
    if view is None:
        return None
    overlays = [
        OverlayInfo(
            kind="radius",
            center=_as_point(search.target.center),
            radius=search.radius,
            metric=search.target.metric.name,
            unit=search.target.metric.unit,
        )
        for search in view.radius_searches
    ]
    overlays.extend(
        OverlayInfo(kind="nearest", center=_as_point(target.center), metric=target.metric.name)
        for target in view.nearest_searches
    )
    overlays.extend(
        OverlayInfo(kind="polygon", vertices=[_as_point(v) for v in search.polygon.vertices])
        for search in view.polygon_searches
    )
    return SpatialInfo(table=view.table, column=view.column, overlays=overlays)


def _as_point(point: Point) -> PointInfo:
    return PointInfo(lat=point.lat, lon=point.lon)


def _as_plan(node: PlanNode | None) -> PlanInfo | None:
    if node is None:
        return None
    return PlanInfo(
        operation=node.operation,
        detail=node.detail,
        children=[plan for plan in (_as_plan(child) for child in node.children) if plan],
        actual_rows=node.actual_rows,
        actual_ms=None if node.actual_ms is None else round(node.actual_ms, ELAPSED_DECIMALS),
    )


def _as_json(value: Value) -> Any:
    """Los tipos que JSON no conoce viajan como texto; un punto, como `{lat, lon}`."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Point):
        return {"lat": value.lat, "lon": value.lon}
    if isinstance(value, int | float | bool | str) or value is None:
        return value
    if isinstance(value, tuple):
        return list(value)
    return str(value)
