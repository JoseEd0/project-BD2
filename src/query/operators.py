"""Operadores de ejecución en modelo Volcano.

Cada operador es un iterador que pide filas a sus hijos y produce las suyas. Nadie
materializa la tabla entera salvo los que el algoritmo obliga —el ordenamiento y el hashing
externos, que además se apoyan en disco—, así que una consulta consume memoria acotada.

Cada operador también sabe describirse: `plan()` construye el nodo que el panel de plan de
ejecución del frontend muestra al usuario.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from functools import reduce
from itertools import chain
from pathlib import Path
from typing import Any

from config import EngineConfig
from external.hash import ExternalHashGrouper, ExternalHashJoin
from external.joining import Pair, Unmatched, any_pair
from external.loop import BlockNestedLoopJoin
from external.runs import buffered_records
from external.sort import ExternalSorter
from hashing import canonical_key_bytes
from index.keys import Key
from query.aggregation import Accumulator, Aggregate, accumulator_for, check_argument_type
from query.comparison import describe_value
from query.expressions import (
    ColumnSlot,
    ExpressionEvaluator,
    RowLayout,
    describe_expression,
    result_field,
)
from query.functions import AggregateKind
from query.plan import PlanNode
from query.table import Table
from sql.nodes import ColumnRef, Expression, JoinKind, SortDirection
from storage.record import Record, RecordSerializer
from storage.schema import Field, Schema
from storage.types import FieldType, Value

SORT_DIRECTORY = "sort"
GROUP_DIRECTORY = "group"
JOIN_DIRECTORY = "join"
DISTINCT_DIRECTORY = "distinct"
ARRIVAL_COLUMN = "arrival"
INTERNAL_COLUMN_PREFIX = "c"
UNMATCHED_BY_KIND = {
    JoinKind.INNER: Unmatched.NONE,
    JoinKind.LEFT: Unmatched.LEFT,
    JoinKind.RIGHT: Unmatched.RIGHT,
    JoinKind.FULL: Unmatched.BOTH,
}
MILLISECONDS = 1000.0
# `COUNT(*)` cuenta filas, no valores: lo que recibe su acumulador es indiferente.
COUNTED_ROW = True


class UnsupportedQueryError(Exception):
    """La consulta usa algo que el ejecutor todavía no implementa."""


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

    _actual_rows: int | None = None
    _elapsed_seconds: float = 0.0

    def rows(self) -> Iterator[Record]:
        """Filas del operador, contándolas y midiendo el tiempo que cuesta producirlas.

        El tiempo es inclusivo, como el `actual time` de PostgreSQL: incluye lo que tardan
        los hijos, porque se mide alrededor de cada petición de fila. No incluye el tiempo
        que el consumidor pasa entre una fila y la siguiente.
        """
        produced = iter(self._produce())
        self._actual_rows = 0
        while True:
            started = time.perf_counter()
            try:
                row = next(produced)
            except StopIteration:
                self._elapsed_seconds += time.perf_counter() - started
                return
            self._elapsed_seconds += time.perf_counter() - started
            self._actual_rows += 1
            yield row

    def plan(self) -> PlanNode:
        """Nodo del plan; tras ejecutar, con las filas y el tiempo reales del operador."""
        node = self._plan_node()
        if self._actual_rows is None:
            return node
        return replace(
            node,
            actual_rows=self._actual_rows,
            actual_ms=self._elapsed_seconds * MILLISECONDS,
        )

    @abstractmethod
    def _produce(self) -> Iterator[Record]:
        """Filas producidas por el operador."""

    @abstractmethod
    def _plan_node(self) -> PlanNode:
        """Descripción de este paso y de sus hijos."""


class TableOperator(Operator):
    """Base de los accesos a una tabla."""

    def __init__(self, table: Table, alias: str) -> None:
        self._table = table
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

    def _produce(self) -> Iterator[Record]:
        return self._table.scan()

    def _plan_node(self) -> PlanNode:
        return PlanNode(
            "SequentialScan",
            f"{self._table.name} ({self._table.organization.value}, {self._table.row_count} filas)",
        )


class OrderedScan(SequentialScan):
    """Recorre una tabla que se guarda ordenada por su clave, contando con ese orden.

    Es el mismo recorrido completo, pero resuelve un `ORDER BY` por la clave sin ordenar
    nada: en un archivo secuencial o en un B+ agrupado las filas ya salen así.
    """

    def _plan_node(self) -> PlanNode:
        return PlanNode(
            "OrderedScan",
            f"{self._table.name} en orden de {self._table.primary_key} "
            f"({self._table.organization.value}, {self._table.row_count} filas) · sin ordenar",
        )


class PrimaryKeyLookup(TableOperator):
    """Busca por clave primaria usando la organización de la tabla."""

    def __init__(self, table: Table, alias: str, key: Key) -> None:
        super().__init__(table, alias)
        self._key = key

    def _produce(self) -> Iterator[Record]:
        return self._table.search_primary_key(self._key)

    def _plan_node(self) -> PlanNode:
        return PlanNode(
            "PrimaryKeyLookup",
            f"{self._table.name}.{self._table.primary_key} = {describe_value(self._key)} "
            f"({self._table.organization.value})",
        )


class PrimaryKeyRange(TableOperator):
    """Recorre un rango de claves primarias aprovechando que la tabla está ordenada."""

    def __init__(self, table: Table, alias: str, low: Key | None, high: Key | None) -> None:
        super().__init__(table, alias)
        self._low = low
        self._high = high

    def _produce(self) -> Iterator[Record]:
        return self._table.range_primary_key(self._low, self._high)

    def _plan_node(self) -> PlanNode:
        bounds = _described_range(self._low, self._high)
        return PlanNode(
            "PrimaryKeyRange",
            f"{self._table.name}.{self._table.primary_key} {bounds} "
            f"({self._table.organization.value})",
        )


class IndexLookup(TableOperator):
    """Busca por igualdad usando un índice secundario."""

    def __init__(self, table: Table, alias: str, index_name: str, value: Key) -> None:
        super().__init__(table, alias)
        self._index_name = index_name
        self._value = value

    def _produce(self) -> Iterator[Record]:
        return self._table.search_index(self._index_name, self._value)

    def _plan_node(self) -> PlanNode:
        definition = self._table.definition.index_on_name(self._index_name)
        return PlanNode(
            "IndexLookup",
            f"{self._table.name}.{definition.column} = {describe_value(self._value)} "
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

    def _produce(self) -> Iterator[Record]:
        return self._table.range_index(self._index_name, self._low, self._high)

    def _plan_node(self) -> PlanNode:
        definition = self._table.definition.index_on_name(self._index_name)
        bounds = _described_range(self._low, self._high)
        return PlanNode(
            "IndexRange",
            f"{self._table.name}.{definition.column} {bounds} "
            f"(índice {self._index_name}, {definition.method.value})",
        )


class Filter(Operator):
    """Deja pasar las filas que cumplen el predicado."""

    def __init__(self, child: Operator, predicate: Expression) -> None:
        self._child = child
        self._predicate = predicate
        self._evaluator = ExpressionEvaluator(child.layout)
        self._evaluator.validate(predicate)

    @property
    def layout(self) -> RowLayout:
        return self._child.layout

    @property
    def schema(self) -> Schema:
        return self._child.schema

    def _produce(self) -> Iterator[Record]:
        for row in self._child.rows():
            if self._evaluator.matches(self._predicate, row):
                yield row

    def _plan_node(self) -> PlanNode:
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
        for expression, _ in self._keys:
            self._evaluator.validate(expression)
        self._runs = 0
        self._passes = 0

    @property
    def layout(self) -> RowLayout:
        return self._child.layout

    @property
    def schema(self) -> Schema:
        return self._child.schema

    def _produce(self) -> Iterator[Record]:
        sorter = ExternalSorter(
            self._directory, self._serializer.size, self._sort_key, self._config
        )
        with sorter:
            packed = (self._serializer.pack(row) for row in self._child.rows())
            merged = sorter.sort(packed)
            self._runs = sorter.run_count
            self._passes = sorter.merge_passes
            for record in merged:
                yield self._serializer.unpack(record)

    def _plan_node(self) -> PlanNode:
        """Tras ejecutar, el detalle incluye cuántos runs se generaron y cuántas pasadas hubo.

        Los runs se conocen en cuanto `sort` termina de repartir la entrada, antes de emitir
        la primera fila, así que el dato es correcto aunque un `LIMIT` corte la mezcla.
        """
        keys = ", ".join(
            f"{describe_expression(expression)} {direction.value}"
            for expression, direction in self._keys
        )
        if self._actual_rows is None:
            return PlanNode("ExternalSort", keys, (self._child.plan(),))
        detail = f"{keys} · {self._runs} run(s), {self._passes} pasada(s) de mezcla"
        return PlanNode("ExternalSort", detail, (self._child.plan(),))

    def _sort_key(self, record: bytes) -> Key:
        row = self._serializer.unpack(record)
        return tuple(
            _sort_component(self._evaluator.evaluate(expression, row), direction)
            for expression, direction in self._keys
        )


class HashAggregate(Operator):
    """Agrupa con hashing externo y calcula las funciones de agregación.

    De cada grupo guarda un acumulador por función, no sus filas: la memoria que ocupa
    depende de cuántos grupos hay, y sin `GROUP BY` es constante.
    """

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
        for expression in (*self._group_by, *(item.argument for item in self._aggregates)):
            self._evaluator.validate(expression)
        self._layout = self._build_layout()
        self._schema = self._build_schema()

    @property
    def layout(self) -> RowLayout:
        return self._layout

    @property
    def schema(self) -> Schema:
        return self._schema

    def _produce(self) -> Iterator[Record]:
        if not self._group_by:
            yield _results_of(reduce(self._absorb, self._child.rows(), self._accumulators()))
            return
        grouper = ExternalHashGrouper(
            self._directory, self._input_serializer.size, self._group_key, self._config
        )
        with grouper:
            packed = (self._input_serializer.pack(row) for row in self._child.rows())
            groups = grouper.reduce(packed, self._accumulators, self._absorb_record)
            for key, accumulators in groups:
                yield (*_as_tuple(key), *_results_of(accumulators))

    def _plan_node(self) -> PlanNode:
        keys = ", ".join(map(describe_expression, self._group_by)) or "(sin GROUP BY)"
        functions = ", ".join(item.label for item in self._aggregates)
        return PlanNode("HashAggregate", f"{keys} → {functions}", (self._child.plan(),))

    def _accumulators(self) -> list[Accumulator]:
        return [accumulator_for(aggregate) for aggregate in self._aggregates]

    def _absorb(self, accumulators: list[Accumulator], row: Record) -> list[Accumulator]:
        for aggregate, accumulator in zip(self._aggregates, accumulators, strict=True):
            if aggregate.argument is None:
                accumulator.add(COUNTED_ROW)
                continue
            value = self._evaluator.evaluate(aggregate.argument, row)
            if value is not None:
                accumulator.add(value)
        return accumulators

    def _absorb_record(self, accumulators: list[Accumulator], record: bytes) -> list[Accumulator]:
        return self._absorb(accumulators, self._input_serializer.unpack(record))

    def _group_key(self, record: bytes) -> Key:
        row = self._input_serializer.unpack(record)
        return tuple(self._evaluator.evaluate(expression, row) for expression in self._group_by)

    def _build_layout(self) -> RowLayout:
        """Una columna por clave y otra por agregación.

        Una clave que es una columna conserva su tabla, para que `SELECT a.ciudad`
        resuelva; una que es una expresión se llama como se escribe (`grouping_label`).
        """
        slots = [
            ColumnSlot(key.qualifier, key.name)
            if isinstance(key, ColumnRef)
            else ColumnSlot(None, grouping_label(key))
            for key in self._group_by
        ]
        slots.extend(ColumnSlot(None, aggregate.label) for aggregate in self._aggregates)
        return RowLayout(slots)

    def _build_schema(self) -> Schema:
        fields = [self._field_of(expression) for expression in self._group_by]
        fields.extend(self._aggregate_field(aggregate) for aggregate in self._aggregates)
        return internal_schema(fields)

    def _field_of(self, expression: Expression) -> Field:
        return result_field(
            grouping_label(expression), expression, self._child.layout, self._child.schema
        )

    def _aggregate_field(self, aggregate: Aggregate) -> Field:
        if aggregate.kind is AggregateKind.COUNT or aggregate.argument is None:
            return Field(aggregate.label, FieldType.INT)
        argument = result_field(
            aggregate.label, aggregate.argument, self._child.layout, self._child.schema
        )
        check_argument_type(aggregate, argument.type)
        if aggregate.kind is AggregateKind.AVG:
            return Field(aggregate.label, FieldType.FLOAT)
        return argument


class JoinOperator(Operator):
    """Lo que comparten las reuniones: dos entradas, una condición y las filas sin pareja.

    Las filas de la salida son las de la izquierda seguidas de las de la derecha. En una
    reunión externa, la fila que no encontró pareja sale con NULL en las columnas del otro
    lado.
    """

    def __init__(
        self, left: Operator, right: Operator, kind: JoinKind, condition: Expression | None
    ) -> None:
        self._left = left
        self._right = right
        self._kind = kind
        self._condition = condition
        self._left_serializer = RecordSerializer(left.schema)
        self._right_serializer = RecordSerializer(right.schema)
        self._layout = left.layout.concat(right.layout)
        self._schema = internal_schema([*left.schema.fields, *right.schema.fields])
        self._evaluator = ExpressionEvaluator(self._layout)
        self._evaluator.validate(condition)
        self._no_left: Record = (None,) * len(left.schema)
        self._no_right: Record = (None,) * len(right.schema)

    @property
    def layout(self) -> RowLayout:
        return self._layout

    @property
    def schema(self) -> Schema:
        return self._schema

    def _inputs(self) -> tuple[Iterator[bytes], Iterator[bytes]]:
        return (
            (self._left_serializer.pack(row) for row in self._left.rows()),
            (self._right_serializer.pack(row) for row in self._right.rows()),
        )

    def _rows_of(self, pairs: Iterable[Pair]) -> Iterator[Record]:
        for left, right in pairs:
            yield (
                *(self._no_left if left is None else self._left_serializer.unpack(left)),
                *(self._no_right if right is None else self._right_serializer.unpack(right)),
            )

    def _accepts(self, left: bytes, right: bytes) -> bool:
        """Si el par de filas cumple la condición de la reunión."""
        row = (*self._left_serializer.unpack(left), *self._right_serializer.unpack(right))
        return self._evaluator.matches(self._condition, row)

    def _described(self, condition: str) -> str:
        """Detalle del plan: la condición, precedida del tipo de reunión si es externa."""
        return condition if self._kind is JoinKind.INNER else f"{self._kind.value} · {condition}"


class HashJoin(JoinOperator):
    """Reunión por igualdad con hashing externo (*grace hash join*).

    `left_keys` y `right_keys` son las columnas que el `ON` iguala; `residual`, el resto de
    la condición, que se comprueba sobre cada par de filas con la misma clave.
    """

    def __init__(
        self,
        left: Operator,
        right: Operator,
        kind: JoinKind,
        left_keys: Sequence[Expression],
        right_keys: Sequence[Expression],
        residual: Expression | None,
        directory: Path,
        config: EngineConfig,
    ) -> None:
        super().__init__(left, right, kind, residual)
        self._left_keys = tuple(left_keys)
        self._right_keys = tuple(right_keys)
        self._directory = directory
        self._config = config
        self._left_evaluator = ExpressionEvaluator(left.layout)
        self._right_evaluator = ExpressionEvaluator(right.layout)

    def _produce(self) -> Iterator[Record]:
        joiner = ExternalHashJoin(
            self._directory,
            self._left_serializer.size,
            self._right_serializer.size,
            self._key_of(self._left_serializer, self._left_evaluator, self._left_keys),
            self._key_of(self._right_serializer, self._right_evaluator, self._right_keys),
            self._config,
            any_pair if self._condition is None else self._accepts,
            UNMATCHED_BY_KIND[self._kind],
        )
        with joiner:
            yield from self._rows_of(joiner.join(*self._inputs()))

    def _plan_node(self) -> PlanNode:
        equalities = [
            f"{describe_expression(left)} = {describe_expression(right)}"
            for left, right in zip(self._left_keys, self._right_keys, strict=True)
        ]
        if self._condition is not None:
            equalities.append(describe_expression(self._condition))
        detail = self._described(" AND ".join(equalities))
        return PlanNode("HashJoin", detail, (self._left.plan(), self._right.plan()))

    @staticmethod
    def _key_of(
        serializer: RecordSerializer,
        evaluator: ExpressionEvaluator,
        expressions: Sequence[Expression],
    ) -> Callable[[bytes], Key]:
        """Clave de reunión de una fila: un valor, o una tupla si el `ON` iguala varias."""
        if len(expressions) == 1:
            single = expressions[0]
            return lambda record: evaluator.evaluate(single, serializer.unpack(record))

        def composite(record: bytes) -> Key:
            row = serializer.unpack(record)
            return tuple(evaluator.evaluate(expression, row) for expression in expressions)

        return composite


class NestedLoopJoin(JoinOperator):
    """Reunión por una condición sin igualdades, con bucles anidados en bloques."""

    def __init__(
        self,
        left: Operator,
        right: Operator,
        kind: JoinKind,
        condition: Expression,
        directory: Path,
        config: EngineConfig,
    ) -> None:
        super().__init__(left, right, kind, condition)
        self._directory = directory
        self._config = config

    def _produce(self) -> Iterator[Record]:
        joiner = BlockNestedLoopJoin(
            self._directory,
            self._left_serializer.size,
            self._right_serializer.size,
            self._accepts,
            self._config,
            UNMATCHED_BY_KIND[self._kind],
        )
        with joiner:
            yield from self._rows_of(joiner.join(*self._inputs()))

    def _plan_node(self) -> PlanNode:
        detail = self._described(
            "sin condición" if self._condition is None else describe_expression(self._condition)
        )
        return PlanNode("NestedLoopJoin", detail, (self._left.plan(), self._right.plan()))


class Projection(Operator):
    """Calcula las expresiones del SELECT y da nombre a cada columna del resultado."""

    def __init__(
        self, child: Operator, expressions: Sequence[Expression], names: Sequence[str]
    ) -> None:
        self._child = child
        self._expressions = tuple(expressions)
        self._names = tuple(names)
        self._evaluator = ExpressionEvaluator(child.layout)
        for expression in self._expressions:
            self._evaluator.validate(expression)
        self._layout = RowLayout.of_names(self._names)

    @property
    def layout(self) -> RowLayout:
        return self._layout

    @property
    def schema(self) -> Schema:
        raise UnsupportedQueryError("una proyección no se puede volcar a disco")

    def _produce(self) -> Iterator[Record]:
        for row in self._child.rows():
            yield tuple(
                self._evaluator.evaluate(expression, row) for expression in self._expressions
            )

    def _plan_node(self) -> PlanNode:
        return PlanNode("Projection", ", ".join(self._names), (self._child.plan(),))


class Distinct(Operator):
    """Deja pasar, de las filas que darían el mismo resultado en el SELECT, solo la primera.

    Va debajo de la proyección porque ahí las filas tienen tipos conocidos y se pueden
    volcar a disco; lo que compara es el valor de las expresiones del SELECT.

    Mientras las filas distintas caben en el buffer se entregan según llegan, y un `LIMIT`
    encima corta pronto. Si no caben, el resto de la entrada se resuelve en disco: hashing
    externo para quedarse con la primera aparición de cada resultado y ordenamiento
    externo para devolverlas en el orden en que llegaron.
    """

    def __init__(
        self,
        child: Operator,
        expressions: Sequence[Expression],
        directory: Path,
        config: EngineConfig,
    ) -> None:
        self._child = child
        self._expressions = tuple(expressions)
        self._directory = directory
        self._config = config
        self._evaluator = ExpressionEvaluator(child.layout)
        for expression in self._expressions:
            self._evaluator.validate(expression)
        self._numbered = RecordSerializer(
            internal_schema([*child.schema.fields, Field(ARRIVAL_COLUMN, FieldType.INT)])
        )
        self._memory_rows = buffered_records(config, self._numbered.size)
        self._used_disk = False

    @property
    def layout(self) -> RowLayout:
        return self._child.layout

    @property
    def schema(self) -> Schema:
        return self._child.schema

    def _produce(self) -> Iterator[Record]:
        delivered: set[bytes] = set()
        rows = self._child.rows()
        for row in rows:
            signature = self._signature(row)
            if signature in delivered:
                continue
            if len(delivered) == self._memory_rows:
                yield from self._distinct_on_disk(chain((row,), rows), delivered)
                return
            delivered.add(signature)
            yield row

    def _plan_node(self) -> PlanNode:
        if self._actual_rows is None:
            return PlanNode("Distinct", "", (self._child.plan(),))
        where = "hashing y ordenamiento externos" if self._used_disk else "en memoria"
        return PlanNode("Distinct", where, (self._child.plan(),))

    def _distinct_on_disk(self, rows: Iterable[Record], delivered: set[bytes]) -> Iterator[Record]:
        """Filas de `rows` cuyo resultado no se entregó ya, sin repetir y en su orden."""
        self._used_disk = True
        pending = (
            self._numbered.pack((*row, arrival))
            for arrival, row in enumerate(rows)
            if self._signature(row) not in delivered
        )
        grouper = ExternalHashGrouper(
            self._directory, self._numbered.size, self._signature_of_record, self._config
        )
        sorter = ExternalSorter(
            self._directory, self._numbered.size, self._arrival_of, self._config
        )
        with grouper, sorter:
            firsts = (first for _, first in grouper.reduce(pending, bytes, _first_record))
            for record in sorter.sort(firsts):
                yield self._numbered.unpack(record)[:-1]

    def _signature(self, row: Record) -> bytes:
        return canonical_key_bytes(
            tuple(self._evaluator.evaluate(expression, row) for expression in self._expressions)
        )

    def _signature_of_record(self, record: bytes) -> Key:
        return self._signature(self._numbered.unpack(record)[:-1])

    def _arrival_of(self, record: bytes) -> Key:
        return self._numbered.unpack_field(record, len(self._child.schema))


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

    def _produce(self) -> Iterator[Record]:
        """Corta en cuanto entrega la última fila, sin pedir una más al hijo: con un
        `LIMIT 5` el operador de abajo no debe producir la sexta."""
        if self._limit == 0:
            return
        produced = 0
        for position, row in enumerate(self._child.rows()):
            if position < self._offset:
                continue
            yield row
            produced += 1
            if self._limit is not None and produced >= self._limit:
                return

    def _plan_node(self) -> PlanNode:
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


def _described_range(low: Key | None, high: Key | None) -> str:
    """Un rango de claves como condición: `>= 5`, `<= 9` o `entre 5 y 9`."""
    if low is None and high is None:
        return "sin límites"
    if high is None:
        return f">= {describe_value(low)}"
    if low is None:
        return f"<= {describe_value(high)}"
    return f"entre {describe_value(low)} y {describe_value(high)}"


def grouping_label(expression: Expression) -> str:
    """Nombre de la columna que una agrupación produce para una de sus claves."""
    return expression.name if isinstance(expression, ColumnRef) else describe_expression(expression)


def _as_tuple(key: Key) -> tuple[Value, ...]:
    return key if isinstance(key, tuple) else (key,)


def _results_of(accumulators: list[Accumulator]) -> tuple[Value, ...]:
    return tuple(accumulator.result() for accumulator in accumulators)


def _first_record(first: bytes, record: bytes) -> bytes:
    """Paso de reducción que se queda con el primer registro de un grupo."""
    return first or record
