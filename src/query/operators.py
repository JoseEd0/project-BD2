"""Operadores de ejecución en modelo Volcano.

Cada operador es un iterador que pide filas a sus hijos y produce las suyas. Nadie
materializa la tabla entera salvo los que el algoritmo obliga —el ordenamiento y el hashing
externos, que además se apoyan en disco—, así que una consulta consume memoria acotada.

Cada operador también sabe describirse: `plan()` construye el nodo que el panel de plan de
ejecución del frontend muestra al usuario.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path
from typing import Any

from config import EngineConfig
from external.hash import ExternalHashGrouper, ExternalHashJoin
from external.sort import ExternalSorter
from hashing import canonical_key_bytes
from index.keys import Key
from query.expressions import ColumnSlot, ExpressionError, ExpressionEvaluator, RowLayout
from query.plan import PlanNode
from query.table import Table
from sql.nodes import ColumnRef, Expression, SortDirection
from storage.record import Record, RecordSerializer
from storage.schema import Field, Schema
from storage.types import FieldType, Value

SORT_DIRECTORY = "sort"
GROUP_DIRECTORY = "group"
JOIN_DIRECTORY = "join"
INTERNAL_COLUMN_PREFIX = "c"


class UnsupportedQueryError(Exception):
    """La consulta usa algo que el ejecutor todavía no implementa."""


@unique
class AggregateKind(Enum):
    COUNT = "COUNT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


AGGREGATE_NAMES = {kind.value: kind for kind in AggregateKind}


@dataclass(frozen=True, slots=True)
class Aggregate:
    """Una función de agregación aplicada a una expresión (o a `*` en el caso de COUNT)."""

    kind: AggregateKind
    argument: Expression | None
    label: str


def internal_schema(fields: Sequence[Field]) -> Schema:
    """Esquema con nombres artificiales, para que un JOIN no choque por columnas homónimas."""
    return Schema(
        [
            Field(
                name=f"{INTERNAL_COLUMN_PREFIX}{position}",
                type=item.type,
                length=item.length,
                nullable=True,
            )
            for position, item in enumerate(fields)
        ]
    )


class Operator(ABC):
    """Fuente de filas con esquema conocido."""

    @property
    @abstractmethod
    def layout(self) -> RowLayout:
        """Nombres y cualificadores de las columnas que produce."""

    @property
    @abstractmethod
    def schema(self) -> Schema:
        """Tipos de las columnas, para poder serializar las filas a disco."""

    @abstractmethod
    def rows(self) -> Iterator[Record]:
        """Filas producidas por el operador."""

    @abstractmethod
    def plan(self) -> PlanNode:
        """Descripción de este paso y de sus hijos."""


class TableOperator(Operator):
    """Base de los accesos a una tabla."""

    def __init__(self, table: Table, alias: str) -> None:
        self._table = table
        self._alias = alias
        self._layout = RowLayout.of_table(table.schema, alias)
        self._schema = internal_schema(table.schema.fields)

    @property
    def layout(self) -> RowLayout:
        return self._layout

    @property
    def schema(self) -> Schema:
        return self._schema


class SequentialScan(TableOperator):
    """Recorre la tabla entera. Es el plan de referencia contra el que se comparan los índices."""

    def rows(self) -> Iterator[Record]:
        return self._table.scan()

    def plan(self) -> PlanNode:
        return PlanNode(
            "SequentialScan",
            f"{self._table.name} ({self._table.organization.value}, {self._table.row_count} filas)",
        )


class PrimaryKeyLookup(TableOperator):
    """Busca por clave primaria usando la organización de la tabla."""

    def __init__(self, table: Table, alias: str, key: Key) -> None:
        super().__init__(table, alias)
        self._key = key

    def rows(self) -> Iterator[Record]:
        return self._table.search_primary_key(self._key)

    def plan(self) -> PlanNode:
        return PlanNode(
            "PrimaryKeyLookup",
            f"{self._table.name}.{self._table.primary_key} = {self._key!r} "
            f"({self._table.organization.value})",
        )


class PrimaryKeyRange(TableOperator):
    """Recorre un rango de claves primarias aprovechando que la tabla está ordenada."""

    def __init__(self, table: Table, alias: str, low: Key | None, high: Key | None) -> None:
        super().__init__(table, alias)
        self._low = low
        self._high = high

    def rows(self) -> Iterator[Record]:
        return self._table.range_primary_key(self._low, self._high)

    def plan(self) -> PlanNode:
        return PlanNode(
            "PrimaryKeyRange",
            f"{self._table.name}.{self._table.primary_key} entre {self._low!r} y {self._high!r} "
            f"({self._table.organization.value})",
        )


class IndexLookup(TableOperator):
    """Busca por igualdad usando un índice secundario."""

    def __init__(self, table: Table, alias: str, index_name: str, value: Key) -> None:
        super().__init__(table, alias)
        self._index_name = index_name
        self._value = value

    def rows(self) -> Iterator[Record]:
        return self._table.search_index(self._index_name, self._value)

    def plan(self) -> PlanNode:
        definition = self._table.definition.index_on_name(self._index_name)
        return PlanNode(
            "IndexLookup",
            f"{self._table.name}.{definition.column} = {self._value!r} "
            f"(índice {self._index_name}, {definition.method.value})",
        )


class IndexRange(TableOperator):
    """Recorre un rango de valores usando un índice B+ secundario."""

    def __init__(
        self, table: Table, alias: str, index_name: str, low: Key | None, high: Key | None
    ) -> None:
        super().__init__(table, alias)
        self._index_name = index_name
        self._low = low
        self._high = high

    def rows(self) -> Iterator[Record]:
        return self._table.range_index(self._index_name, self._low, self._high)

    def plan(self) -> PlanNode:
        definition = self._table.definition.index_on_name(self._index_name)
        return PlanNode(
            "IndexRange",
            f"{self._table.name}.{definition.column} entre {self._low!r} y {self._high!r} "
            f"(índice {self._index_name}, {definition.method.value})",
        )


class Filter(Operator):
    """Deja pasar las filas que cumplen el predicado."""

    def __init__(self, child: Operator, predicate: Expression) -> None:
        self._child = child
        self._predicate = predicate
        self._evaluator = ExpressionEvaluator(child.layout)

    @property
    def layout(self) -> RowLayout:
        return self._child.layout

    @property
    def schema(self) -> Schema:
        return self._child.schema

    def rows(self) -> Iterator[Record]:
        for row in self._child.rows():
            if self._evaluator.matches(self._predicate, row):
                yield row

    def plan(self) -> PlanNode:
        return PlanNode("Filter", "condición del WHERE", (self._child.plan(),))


class Sort(Operator):
    """Ordena con ordenamiento externo, apoyándose en disco."""

    def __init__(
        self,
        child: Operator,
        keys: Sequence[tuple[Expression, SortDirection]],
        directory: Path,
        config: EngineConfig,
    ) -> None:
        self._child = child
        self._keys = tuple(keys)
        self._directory = directory
        self._config = config
        self._serializer = RecordSerializer(child.schema)
        self._evaluator = ExpressionEvaluator(child.layout)
        self._runs = 0
        self._passes = 0

    @property
    def layout(self) -> RowLayout:
        return self._child.layout

    @property
    def schema(self) -> Schema:
        return self._child.schema

    def rows(self) -> Iterator[Record]:
        sorter = ExternalSorter(
            self._directory, self._serializer.size, self._sort_key, self._config
        )
        with sorter:
            packed = (self._serializer.pack(row) for row in self._child.rows())
            for record in sorter.sort(packed):
                yield self._serializer.unpack(record)
            self._runs = sorter.run_count
            self._passes = sorter.merge_passes

    def plan(self) -> PlanNode:
        detail = ", ".join(
            f"{_describe(expression)} {direction.value}" for expression, direction in self._keys
        )
        return PlanNode("ExternalSort", detail, (self._child.plan(),))

    def _sort_key(self, record: bytes) -> Key:
        row = self._serializer.unpack(record)
        return tuple(
            _sort_component(self._evaluator.evaluate(expression, row), direction)
            for expression, direction in self._keys
        )


class HashAggregate(Operator):
    """Agrupa con hashing externo y calcula las funciones de agregación."""

    def __init__(
        self,
        child: Operator,
        group_by: Sequence[Expression],
        aggregates: Sequence[Aggregate],
        directory: Path,
        config: EngineConfig,
    ) -> None:
        self._child = child
        self._group_by = tuple(group_by)
        self._aggregates = tuple(aggregates)
        self._directory = directory
        self._config = config
        self._input_serializer = RecordSerializer(child.schema)
        self._evaluator = ExpressionEvaluator(child.layout)
        self._layout = self._build_layout()
        self._schema = self._build_schema()

    @property
    def layout(self) -> RowLayout:
        return self._layout

    @property
    def schema(self) -> Schema:
        return self._schema

    def rows(self) -> Iterator[Record]:
        if not self._group_by:
            yield self._aggregate_all()
            return
        grouper = ExternalHashGrouper(
            self._directory, self._input_serializer.size, self._group_key, self._config
        )
        with grouper:
            packed = (self._input_serializer.pack(row) for row in self._child.rows())
            for key, records in grouper.group(packed):
                group = [self._input_serializer.unpack(record) for record in records]
                yield (*_as_tuple(key), *self._compute(group))

    def plan(self) -> PlanNode:
        keys = ", ".join(_describe(expression) for expression in self._group_by) or "(sin GROUP BY)"
        functions = ", ".join(item.label for item in self._aggregates)
        return PlanNode("HashAggregate", f"{keys} → {functions}", (self._child.plan(),))

    def _aggregate_all(self) -> Record:
        return tuple(self._compute(list(self._child.rows())))

    def _compute(self, group: list[Record]) -> tuple[Value, ...]:
        return tuple(self._apply(aggregate, group) for aggregate in self._aggregates)

    def _apply(self, aggregate: Aggregate, group: list[Record]) -> Value:
        if aggregate.kind is AggregateKind.COUNT and aggregate.argument is None:
            return len(group)
        values = [
            self._evaluator.evaluate(aggregate.argument, row)
            for row in group
            if aggregate.argument is not None
        ]
        present = [value for value in values if value is not None]
        if aggregate.kind is AggregateKind.COUNT:
            return len(present)
        if not present:
            return None
        if aggregate.kind is AggregateKind.MIN:
            return min(present)
        if aggregate.kind is AggregateKind.MAX:
            return max(present)
        numbers = [_as_number(value, aggregate.label) for value in present]
        total = sum(numbers)
        return total / len(numbers) if aggregate.kind is AggregateKind.AVG else total

    def _group_key(self, record: bytes) -> Key:
        row = self._input_serializer.unpack(record)
        return tuple(self._evaluator.evaluate(expression, row) for expression in self._group_by)

    def _build_layout(self) -> RowLayout:
        slots = [ColumnSlot(None, _describe(expression)) for expression in self._group_by]
        slots.extend(ColumnSlot(None, aggregate.label) for aggregate in self._aggregates)
        return RowLayout(slots)

    def _build_schema(self) -> Schema:
        fields = [self._field_of(expression) for expression in self._group_by]
        fields.extend(self._aggregate_field(aggregate) for aggregate in self._aggregates)
        return internal_schema(fields)

    def _field_of(self, expression: Expression) -> Field:
        if not isinstance(expression, ColumnRef):
            raise UnsupportedQueryError("GROUP BY solo admite columnas, no expresiones")
        position = self._child.layout.position_of(expression.name, expression.qualifier)
        return self._child.schema.fields[position]

    def _aggregate_field(self, aggregate: Aggregate) -> Field:
        if aggregate.kind is AggregateKind.COUNT:
            return Field(aggregate.label, FieldType.INT)
        if aggregate.kind is AggregateKind.AVG:
            return Field(aggregate.label, FieldType.FLOAT)
        if isinstance(aggregate.argument, ColumnRef):
            position = self._child.layout.position_of(
                aggregate.argument.name, aggregate.argument.qualifier
            )
            source = self._child.schema.fields[position]
            return Field(aggregate.label, source.type, source.length)
        return Field(aggregate.label, FieldType.FLOAT)


class HashJoin(Operator):
    """Reunión por igualdad con hashing externo (*grace hash join*)."""

    def __init__(
        self,
        left: Operator,
        right: Operator,
        left_key: Expression,
        right_key: Expression,
        directory: Path,
        config: EngineConfig,
    ) -> None:
        self._left = left
        self._right = right
        self._left_key = left_key
        self._right_key = right_key
        self._directory = directory
        self._config = config
        self._left_serializer = RecordSerializer(left.schema)
        self._right_serializer = RecordSerializer(right.schema)
        self._left_evaluator = ExpressionEvaluator(left.layout)
        self._right_evaluator = ExpressionEvaluator(right.layout)
        self._layout = left.layout.concat(right.layout)
        self._schema = internal_schema([*left.schema.fields, *right.schema.fields])

    @property
    def layout(self) -> RowLayout:
        return self._layout

    @property
    def schema(self) -> Schema:
        return self._schema

    def rows(self) -> Iterator[Record]:
        joiner = ExternalHashJoin(
            self._directory,
            self._left_serializer.size,
            self._right_serializer.size,
            self._key_of(self._left_serializer, self._left_evaluator, self._left_key),
            self._key_of(self._right_serializer, self._right_evaluator, self._right_key),
            self._config,
        )
        with joiner:
            left_rows = (self._left_serializer.pack(row) for row in self._left.rows())
            right_rows = (self._right_serializer.pack(row) for row in self._right.rows())
            for left_record, right_record in joiner.join(left_rows, right_rows):
                yield (
                    *self._left_serializer.unpack(left_record),
                    *self._right_serializer.unpack(right_record),
                )

    def plan(self) -> PlanNode:
        detail = f"{_describe(self._left_key)} = {_describe(self._right_key)}"
        return PlanNode("HashJoin", detail, (self._left.plan(), self._right.plan()))

    @staticmethod
    def _key_of(
        serializer: RecordSerializer, evaluator: ExpressionEvaluator, expression: Expression
    ) -> Callable[[bytes], Key]:
        def extract(record: bytes) -> Key:
            return evaluator.evaluate(expression, serializer.unpack(record))

        return extract


class Projection(Operator):
    """Calcula las expresiones del SELECT y da nombre a cada columna del resultado."""

    def __init__(
        self, child: Operator, expressions: Sequence[Expression], names: Sequence[str]
    ) -> None:
        self._child = child
        self._expressions = tuple(expressions)
        self._names = tuple(names)
        self._evaluator = ExpressionEvaluator(child.layout)
        self._layout = RowLayout.of_names(self._names)

    @property
    def layout(self) -> RowLayout:
        return self._layout

    @property
    def schema(self) -> Schema:
        raise UnsupportedQueryError("una proyección no se puede volcar a disco")

    def rows(self) -> Iterator[Record]:
        for row in self._child.rows():
            yield tuple(
                self._evaluator.evaluate(expression, row) for expression in self._expressions
            )

    def plan(self) -> PlanNode:
        return PlanNode("Projection", ", ".join(self._names), (self._child.plan(),))


class Distinct(Operator):
    """Elimina filas repetidas comparando su representación canónica."""

    def __init__(self, child: Operator) -> None:
        self._child = child

    @property
    def layout(self) -> RowLayout:
        return self._child.layout

    @property
    def schema(self) -> Schema:
        return self._child.schema

    def rows(self) -> Iterator[Record]:
        seen: set[bytes] = set()
        for row in self._child.rows():
            signature = canonical_key_bytes(row)
            if signature in seen:
                continue
            seen.add(signature)
            yield row

    def plan(self) -> PlanNode:
        return PlanNode("Distinct", "", (self._child.plan(),))


class LimitOffset(Operator):
    """Descarta las primeras `offset` filas y corta en `limit`."""

    def __init__(self, child: Operator, limit: int | None, offset: int | None) -> None:
        self._child = child
        self._limit = limit
        self._offset = offset or 0

    @property
    def layout(self) -> RowLayout:
        return self._child.layout

    @property
    def schema(self) -> Schema:
        return self._child.schema

    def rows(self) -> Iterator[Record]:
        produced = 0
        for position, row in enumerate(self._child.rows()):
            if position < self._offset:
                continue
            if self._limit is not None and produced >= self._limit:
                return
            produced += 1
            yield row

    def plan(self) -> PlanNode:
        detail = f"limit={self._limit}, offset={self._offset}"
        return PlanNode("Limit", detail, (self._child.plan(),))


@dataclass(frozen=True, slots=True)
class _Descending:
    """Envoltorio que invierte la comparación, para ordenar de mayor a menor."""

    value: Any

    def __lt__(self, other: _Descending) -> bool:
        if self.value is None:
            return False
        if other.value is None:
            return True
        return bool(other.value < self.value)


def _sort_component(value: Value, direction: SortDirection) -> Any:
    if direction is SortDirection.DESCENDING:
        return _Descending(value)
    return _NullsFirst(value)


@dataclass(frozen=True, slots=True)
class _NullsFirst:
    """Los NULL se ordenan antes que cualquier valor, y no se comparan entre sí."""

    value: Any

    def __lt__(self, other: _NullsFirst) -> bool:
        if self.value is None:
            return other.value is not None
        if other.value is None:
            return False
        return bool(self.value < other.value)


def _as_tuple(key: Key) -> tuple[Value, ...]:
    return key if isinstance(key, tuple) else (key,)


def _as_number(value: Value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ExpressionError(f"{label} necesita valores numéricos y llegó {value!r}")
    return value


def _describe(expression: Expression) -> str:
    if not isinstance(expression, ColumnRef):
        return type(expression).__name__
    if expression.qualifier is None:
        return expression.name
    return f"{expression.qualifier}.{expression.name}"
