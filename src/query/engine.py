"""Motor de consultas: de una sentencia SQL a un resultado.

Es la fachada del gestor. Recibe el AST del parser, valida contra el catálogo, planifica y
ejecuta. No sabe nada de transacciones ni del API: solo avisa de cada cambio al `Journal`
que le pasen, si le pasan alguno.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

from config import EngineConfig
from query.catalog import (
    Catalog,
    CatalogError,
    IndexDefinition,
    Organization,
    TableDefinition,
    field_from_column,
)
from query.expressions import ExpressionEvaluator, RowLayout
from query.journal import Journal
from query.loader import infer_schema, read_values
from query.operators import UnsupportedQueryError
from query.plan import PlanNode
from query.planner import Planner
from query.table import Table, primary_key_index_name
from sql import parse_script
from sql.nodes import (
    Assignment,
    BeginTransactionStatement,
    CommitTransactionStatement,
    CreateIndexStatement,
    CreateTableFromFileStatement,
    CreateTableStatement,
    DeleteStatement,
    DropIndexStatement,
    DropTableStatement,
    Expression,
    IndexType,
    InsertStatement,
    RollbackTransactionStatement,
    SelectStatement,
    Statement,
    UpdateStatement,
)
from storage.record import Record
from storage.schema import Schema
from storage.types import Value

MILLISECONDS = 1000.0
EMPTY_ROW: Record = ()


class EngineError(Exception):
    """La sentencia no se puede ejecutar."""


class TransactionStatementError(EngineError):
    """El control de transacciones pertenece a la sesión, no al motor."""


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Lo que devuelve una sentencia.

    Attributes:
        columns: nombres de las columnas del resultado.
        rows: filas devueltas; vacío en las sentencias que no consultan.
        plan: árbol del plan de ejecución, solo en los SELECT.
        message: descripción de lo ocurrido para las sentencias sin filas.
        affected_rows: filas creadas, borradas o modificadas.
        elapsed_ms: tiempo de ejecución medido.
    """

    columns: tuple[str, ...] = ()
    rows: tuple[Record, ...] = ()
    plan: PlanNode | None = None
    message: str = ""
    affected_rows: int = 0
    elapsed_ms: float = 0.0


@dataclass
class Engine:
    """Ejecuta sentencias SQL sobre las tablas del catálogo."""

    config: EngineConfig
    _catalog: Catalog = field(init=False)
    _tables: dict[str, Table] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.config.data_directory.mkdir(parents=True, exist_ok=True)
        self._catalog = Catalog(self.config)

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    def execute(self, sql: str, journal: Journal | None = None) -> QueryResult:
        """Ejecuta una sola sentencia.

        Raises:
            EngineError: si el texto no contiene exactamente una sentencia.
            SqlError: si el texto no se puede parsear.
        """
        statements = parse_script(sql)
        if len(statements) != 1:
            raise EngineError(f"se esperaba una sentencia y llegaron {len(statements)}")
        return self.run(statements[0], journal)

    def execute_script(self, sql: str, journal: Journal | None = None) -> list[QueryResult]:
        return [self.run(statement, journal) for statement in parse_script(sql)]

    def run(self, statement: Statement, journal: Journal | None = None) -> QueryResult:
        """Ejecuta una sentencia ya parseada y mide cuánto tarda."""
        started = time.perf_counter()
        result = self._dispatch(statement, journal)
        elapsed = (time.perf_counter() - started) * MILLISECONDS
        return QueryResult(
            columns=result.columns,
            rows=result.rows,
            plan=result.plan,
            message=result.message,
            affected_rows=result.affected_rows,
            elapsed_ms=elapsed,
        )

    def table_names(self) -> list[str]:
        return self._catalog.table_names()

    def table(self, name: str) -> Table:
        """Tabla abierta, abriéndola si hacía falta.

        Raises:
            UnknownTableError: si no está en el catálogo.
        """
        key = name.lower()
        if key not in self._tables:
            definition = self._catalog.table(name)
            self._tables[key] = Table(definition, self.config)
        return self._tables[key]

    def close(self) -> None:
        for table in self._tables.values():
            table.close()
        self._tables.clear()

    def __enter__(self) -> Engine:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _dispatch(self, statement: Statement, journal: Journal | None) -> QueryResult:
        if isinstance(statement, SelectStatement):
            return self._select(statement)
        if isinstance(statement, InsertStatement):
            return self._insert(statement, journal)
        if isinstance(statement, DeleteStatement):
            return self._delete(statement, journal)
        if isinstance(statement, UpdateStatement):
            return self._update(statement, journal)
        if isinstance(statement, CreateTableStatement):
            return self._create_table(statement)
        if isinstance(statement, CreateTableFromFileStatement):
            return self._create_table_from_file(statement)
        if isinstance(statement, CreateIndexStatement):
            return self._create_index(statement)
        if isinstance(statement, DropTableStatement):
            return self._drop_table(statement)
        if isinstance(statement, DropIndexStatement):
            return self._drop_index(statement)
        if isinstance(
            statement,
            BeginTransactionStatement | CommitTransactionStatement | RollbackTransactionStatement,
        ):
            raise TransactionStatementError(
                "BEGIN, COMMIT y ROLLBACK los atiende la sesión, no el motor"
            )
        raise EngineError(f"sentencia no soportada: {type(statement).__name__}")

    def _select(self, statement: SelectStatement) -> QueryResult:
        tables = self._tables_of(statement)
        operator = Planner(tables, self.config).plan(statement)
        rows = tuple(operator.rows())
        return QueryResult(
            columns=operator.layout.names, rows=rows, plan=operator.plan(), affected_rows=len(rows)
        )

    def _insert(self, statement: InsertStatement, journal: Journal | None) -> QueryResult:
        table = self.table(statement.table)
        for values in self._rows_to_insert(statement, table.schema):
            table.insert(values)
            if journal is not None:
                journal.record_insert(table.name, values)
        return QueryResult(
            message=f"{len(statement.rows)} fila(s) insertada(s) en '{table.name}'",
            affected_rows=len(statement.rows),
        )

    def _delete(self, statement: DeleteStatement, journal: Journal | None) -> QueryResult:
        table = self.table(statement.table)
        matches = self._predicate_of(table, statement.where)
        removed = self._collect(table, matches) if journal is not None else ()
        count = table.delete_where(matches)
        for row in removed:
            assert journal is not None
            journal.record_delete(table.name, row)
        return QueryResult(
            message=f"{count} fila(s) borrada(s) de '{table.name}'", affected_rows=count
        )

    def _update(self, statement: UpdateStatement, journal: Journal | None) -> QueryResult:
        table = self.table(statement.table)
        matches = self._predicate_of(table, statement.where)
        transform = self._assignment_of(table, statement.assignments)
        before = self._collect(table, matches) if journal is not None else ()
        count = table.update_where(matches, transform)
        for row in before:
            assert journal is not None
            journal.record_update(table.name, row, transform(row))
        return QueryResult(
            message=f"{count} fila(s) actualizada(s) en '{table.name}'", affected_rows=count
        )

    def _create_table(self, statement: CreateTableStatement) -> QueryResult:
        if statement.if_not_exists and self._catalog.has_table(statement.name):
            return QueryResult(message=f"la tabla '{statement.name}' ya existía")
        fields = [field_from_column(column, self.config) for column in statement.columns]
        primary_key = next(
            (column.name for column in statement.columns if column.primary_key), None
        )
        organization = self._organization_of(statement, primary_key)
        definition = TableDefinition(
            name=statement.name,
            schema=Schema(fields),
            organization=organization,
            primary_key=primary_key,
            indexes=self._declared_indexes(statement, organization, primary_key),
        )
        self._register(definition)
        return QueryResult(
            message=f"tabla '{definition.name}' creada ({organization.value})"
        )

    def _create_table_from_file(self, statement: CreateTableFromFileStatement) -> QueryResult:
        if statement.if_not_exists and self._catalog.has_table(statement.name):
            return QueryResult(message=f"la tabla '{statement.name}' ya existía")
        path = Path(statement.path)
        schema = infer_schema(path, self.config)
        primary_key = statement.index.columns[0] if statement.index is not None else None
        organization = (
            Organization.HEAP
            if statement.index is None
            else self._organization_for_method(statement.index.method)
        )
        definition = TableDefinition(
            name=statement.name,
            schema=schema,
            organization=organization,
            primary_key=primary_key,
            indexes=self._file_indexes(statement, organization, primary_key),
        )
        self._register(definition)
        table = self.table(definition.name)
        loaded = 0
        for values in read_values(path, schema, self.config):
            table.insert(values)
            loaded += 1
        return QueryResult(
            message=f"tabla '{definition.name}' creada desde '{path.name}' con {loaded} filas",
            affected_rows=loaded,
        )

    def _create_index(self, statement: CreateIndexStatement) -> QueryResult:
        table = self.table(statement.table)
        column = statement.spec.columns[0]
        if len(statement.spec.columns) > 1:
            raise UnsupportedQueryError("todavía no hay índices sobre varias columnas")
        if not table.schema.has_field(column):
            raise CatalogError(f"la columna '{column}' no existe en '{table.name}'")
        name = statement.name or f"idx_{table.name}_{column}"
        if statement.if_not_exists and table.definition.index_on(column) is not None:
            return QueryResult(message=f"la columna '{column}' ya tenía índice")
        definition = IndexDefinition(name=name, column=column, method=statement.spec.method)
        self._catalog.add_index(table.name, definition)
        table.build_index(definition)
        self._reopen(table.name)
        return QueryResult(
            message=f"índice '{name}' creado sobre {table.name}.{column} "
            f"({definition.method.value})"
        )

    def _drop_table(self, statement: DropTableStatement) -> QueryResult:
        if statement.if_exists and not self._catalog.has_table(statement.name):
            return QueryResult(message=f"la tabla '{statement.name}' no existía")
        table = self.table(statement.name)
        table.remove_files()
        self._tables.pop(statement.name.lower(), None)
        self._catalog.drop_table(statement.name)
        return QueryResult(message=f"tabla '{statement.name}' eliminada")

    def _drop_index(self, statement: DropIndexStatement) -> QueryResult:
        definition, index = self._catalog.find_index(statement.name)
        table = self.table(definition.name)
        table.drop_index(index.name)
        self._catalog.drop_index(definition.name, index.name)
        self._reopen(definition.name)
        return QueryResult(message=f"índice '{index.name}' eliminado")

    def _register(self, definition: TableDefinition) -> None:
        self._catalog.create_table(definition)
        self._tables[definition.name.lower()] = Table(definition, self.config)

    def _reopen(self, name: str) -> None:
        table = self._tables.pop(name.lower(), None)
        if table is not None:
            table.close()
        self._tables[name.lower()] = Table(self._catalog.table(name), self.config)

    def _organization_of(
        self, statement: CreateTableStatement, primary_key: str | None
    ) -> Organization:
        for column in statement.columns:
            if column.primary_key and column.index is not None:
                return self._organization_for_method(column.index)
        if primary_key is None:
            return Organization.HEAP
        return Organization.HEAP

    @staticmethod
    def _organization_for_method(method: IndexType) -> Organization:
        if method is IndexType.SEQUENTIAL:
            return Organization.SEQUENTIAL
        if method is IndexType.BTREE:
            return Organization.CLUSTERED_BTREE
        return Organization.HEAP

    def _declared_indexes(
        self,
        statement: CreateTableStatement,
        organization: Organization,
        primary_key: str | None,
    ) -> tuple[IndexDefinition, ...]:
        indexes = []
        if organization is Organization.HEAP and primary_key is not None:
            indexes.append(
                IndexDefinition(
                    name=primary_key_index_name(statement.name),
                    column=primary_key,
                    method=IndexType.BTREE,
                )
            )
        for column in statement.columns:
            if column.index is None or column.primary_key:
                continue
            if organization is not Organization.HEAP:
                raise CatalogError(
                    "los índices secundarios necesitan que la tabla sea un heap file"
                )
            indexes.append(
                IndexDefinition(
                    name=f"idx_{statement.name}_{column.name}",
                    column=column.name,
                    method=column.index,
                )
            )
        return tuple(indexes)

    def _file_indexes(
        self,
        statement: CreateTableFromFileStatement,
        organization: Organization,
        primary_key: str | None,
    ) -> tuple[IndexDefinition, ...]:
        if organization is not Organization.HEAP or primary_key is None:
            return ()
        method = statement.index.method if statement.index is not None else IndexType.BTREE
        return (
            IndexDefinition(
                name=primary_key_index_name(statement.name),
                column=primary_key,
                method=method,
            ),
        )

    def _tables_of(self, statement: SelectStatement) -> dict[str, Table]:
        names = [statement.source.name, *(join.table.name for join in statement.joins)]
        return {name.lower(): self.table(name) for name in names}

    def _rows_to_insert(
        self, statement: InsertStatement, schema: Schema
    ) -> Iterator[tuple[Value, ...]]:
        evaluator = ExpressionEvaluator(RowLayout.of_names(schema.names))
        for row in statement.rows:
            values = [evaluator.evaluate(expression, EMPTY_ROW) for expression in row]
            yield tuple(self._arrange(values, statement.columns, schema))

    @staticmethod
    def _arrange(
        values: Sequence[Value], columns: tuple[str, ...] | None, schema: Schema
    ) -> list[Value]:
        if columns is None:
            if len(values) != len(schema):
                raise EngineError(
                    f"la fila tiene {len(values)} valores y la tabla {len(schema)} columnas"
                )
            return list(values)
        arranged: list[Value] = [None] * len(schema)
        for name, value in zip(columns, values, strict=True):
            arranged[schema.position_of(name)] = value
        return arranged

    @staticmethod
    def _predicate_of(table: Table, where: Expression | None) -> Callable[[Record], bool]:
        evaluator = ExpressionEvaluator(RowLayout.of_table(table.schema, table.name))

        def matches(row: Record) -> bool:
            return evaluator.matches(where, row)

        return matches

    @staticmethod
    def _assignment_of(
        table: Table, assignments: Sequence[Assignment]
    ) -> Callable[[Record], Record]:
        evaluator = ExpressionEvaluator(RowLayout.of_table(table.schema, table.name))
        positions = [(table.schema.position_of(item.column), item.value) for item in assignments]

        def transform(row: Record) -> Record:
            updated = list(row)
            for position, expression in positions:
                updated[position] = evaluator.evaluate(expression, row)
            return tuple(updated)

        return transform

    @staticmethod
    def _collect(table: Table, matches: Callable[[Record], bool]) -> tuple[Record, ...]:
        return tuple(row for row in table.scan() if matches(row))
