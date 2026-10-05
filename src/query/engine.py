"""Motor de consultas: de una sentencia SQL a un resultado.

Es la fachada del gestor. Recibe el AST del parser, valida contra el catálogo, planifica y
ejecuta. No sabe nada de transacciones ni del API: solo avisa de cada cambio al `Journal`
que le pasen, si le pasan alguno.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import TracebackType
from typing import Any

from config import EngineConfig
from query.catalog import (
    Catalog,
    CatalogError,
    IndexDefinition,
    Organization,
    TableDefinition,
    field_from_column,
    organization_for,
)
from query.expressions import (
    NO_COLUMNS,
    NO_ROW,
    ExpressionEvaluator,
    RowLayout,
    UnknownColumnError,
)
from query.journal import DiscardingJournal, Journal
from query.loader import infer_schema, read_values
from query.operators import UnsupportedQueryError
from query.plan import PlanNode
from query.planner import Planner
from query.spatial import SpatialView, spatial_view
from query.table import Table, discard_table_files, primary_key_index_name
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
    ExplainStatement,
    Expression,
    IndexType,
    InsertStatement,
    RollbackTransactionStatement,
    SelectStatement,
    Statement,
    UpdateStatement,
)
from storage.record import Record
from storage.schema import Field, Schema
from storage.types import ORDERED_FIELD_TYPES, FieldType, Value

MILLISECONDS = 1000.0
# Métodos que, declarados sobre una columna al cargar un archivo, la convierten en la clave
# de la tabla. Un R-Tree no identifica filas: varias pueden compartir ubicación.
KEY_INDEX_METHODS = frozenset({IndexType.SEQUENTIAL, IndexType.BTREE, IndexType.HASH})
SECONDARY_INDEX_METHODS = frozenset({IndexType.BTREE, IndexType.HASH, IndexType.RTREE})
NO_JOURNAL: Journal = DiscardingJournal()


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
        plan: árbol del plan de ejecución, en los SELECT y los EXPLAIN.
        message: descripción de lo ocurrido para las sentencias sin filas.
        affected_rows: filas creadas, borradas o modificadas.
        elapsed_ms: tiempo de ejecución medido.
        spatial: tabla, columna y figuras que el panel de mapa debe dibujar, si la consulta
            toca alguna columna POINT.
    """

    columns: tuple[str, ...] = ()
    rows: tuple[Record, ...] = ()
    plan: PlanNode | None = None
    message: str = ""
    affected_rows: int = 0
    elapsed_ms: float = 0.0
    spatial: SpatialView | None = None


@dataclass
class Engine:
    """Ejecuta sentencias SQL sobre las tablas del catálogo."""

    config: EngineConfig
    _catalog: Catalog = field(init=False)
    _tables: dict[str, Table] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.config.data_directory.mkdir(parents=True, exist_ok=True)
        self._catalog = Catalog(self.config)

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

    def run(self, statement: Statement, journal: Journal | None = None) -> QueryResult:
        """Ejecuta una sentencia ya parseada y mide cuánto tarda."""
        started = time.perf_counter()
        result = self._dispatch(statement, NO_JOURNAL if journal is None else journal)
        elapsed = (time.perf_counter() - started) * MILLISECONDS
        return replace(result, elapsed_ms=elapsed)

    def table_names(self) -> list[str]:
        return self._catalog.table_names()

    def table_of_index(self, name: str) -> str | None:
        """Nombre de la tabla dueña de ese índice, o `None` si el índice no existe."""
        if not self._catalog.has_index(name):
            return None
        return self._catalog.find_index(name)[0].name

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

    def describe_table(self, name: str) -> dict[str, Any]:
        """Estructura física de la tabla y de sus índices (ver `Table.describe_structure`)."""
        return self.table(name).describe_structure()

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

    def _dispatch(self, statement: Statement, journal: Journal) -> QueryResult:
        if isinstance(statement, SelectStatement):
            return self._select(statement)
        if isinstance(statement, ExplainStatement):
            return self._explain(statement)
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
            columns=operator.layout.names,
            rows=rows,
            plan=operator.plan(),
            affected_rows=len(rows),
            spatial=spatial_view(statement, tables),
        )

    def _explain(self, statement: ExplainStatement) -> QueryResult:
        """`EXPLAIN` planifica sin ejecutar; `EXPLAIN ANALYZE` ejecuta, mide y descarta las filas.

        El planificador es por reglas, no por costos: el plan sin ejecutar dice qué camino se
        eligió, pero no trae una estimación de filas. Las cifras reales salen con ANALYZE.
        """
        operator = Planner(self._tables_of(statement.query), self.config).plan(statement.query)
        if not statement.analyze:
            return QueryResult(
                plan=operator.plan(), message="plan elegido, sin ejecutar la consulta"
            )
        produced = sum(1 for _ in operator.rows())
        return QueryResult(
            plan=operator.plan(),
            message=f"consulta ejecutada: {produced} fila(s); se muestran el plan y sus medidas",
            affected_rows=produced,
        )

    def _insert(self, statement: InsertStatement, journal: Journal) -> QueryResult:
        table = self.table(statement.table)
        rows = list(self._rows_to_insert(statement, table.schema))
        table.insert_all(rows, _JournalOf(journal, table.name))
        return QueryResult(
            message=f"{len(statement.rows)} fila(s) insertada(s) en '{table.name}'",
            affected_rows=len(statement.rows),
        )

    def _delete(self, statement: DeleteStatement, journal: Journal) -> QueryResult:
        table = self.table(statement.table)
        matches = self._predicate_of(table, statement.where)
        count = table.delete_where(matches, _JournalOf(journal, table.name))
        return QueryResult(
            message=f"{count} fila(s) borrada(s) de '{table.name}'", affected_rows=count
        )

    def _update(self, statement: UpdateStatement, journal: Journal) -> QueryResult:
        table = self.table(statement.table)
        matches = self._predicate_of(table, statement.where)
        transform = self._assignment_of(table, statement.assignments)
        count = table.update_where(matches, transform, _JournalOf(journal, table.name))
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
        if primary_key is not None:
            _require_orderable_key(Schema(fields).field_of(primary_key))
        key_method = _key_method_of(statement)
        organization = Organization.HEAP if key_method is None else organization_for(key_method)
        definition = TableDefinition(
            name=statement.name,
            schema=Schema(fields),
            organization=organization,
            primary_key=primary_key,
            indexes=self._declared_indexes(statement, organization, key_method),
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
        spec = statement.index
        if spec is not None and not schema.has_field(spec.columns[0]):
            raise CatalogError(f"la columna '{spec.columns[0]}' no está en '{path.name}'")
        key_spec = spec if spec is not None and spec.method in KEY_INDEX_METHODS else None
        primary_key = None if key_spec is None else key_spec.columns[0]
        if primary_key is not None:
            _require_orderable_key(schema.field_of(primary_key))
        organization = (
            Organization.HEAP if key_spec is None else organization_for(key_spec.method)
        )
        definition = TableDefinition(
            name=statement.name,
            schema=schema,
            organization=organization,
            primary_key=primary_key,
            indexes=self._file_indexes(statement, organization, primary_key),
        )
        self._register(definition)
        try:
            loaded = self._load_rows(self.table(definition.name), path, schema)
            if spec is not None and key_spec is None:
                self._add_index(self.table(definition.name), spec.columns[0], spec.method, None)
        except Exception:
            self._remove_table(definition.name)
            raise
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
        if statement.if_not_exists and table.definition.index_on(column) is not None:
            return QueryResult(message=f"la columna '{column}' ya tenía índice")
        definition = self._add_index(table, column, statement.spec.method, statement.name)
        return QueryResult(
            message=f"índice '{definition.name}' creado sobre {table.name}.{column} "
            f"({definition.method.value})"
        )

    def _add_index(
        self, table: Table, column: str, method: IndexType, name: str | None
    ) -> IndexDefinition:
        """Registra un índice secundario y lo construye sobre las filas que ya existen.

        Si la construcción falla, el índice se quita del catálogo y sus archivos se borran:
        un índice a medias respondería consultas con filas de menos.

        Raises:
            CatalogError: si la tabla no es un heap file o el método no sirve para la columna.
        """
        _require_heap_for_secondary_index(table.organization, column)
        _require_method_for_column(table.schema.field_of(column), method)
        definition = IndexDefinition(
            name=name or f"idx_{table.name}_{column}", column=column, method=method
        )
        self._catalog.add_index(table.name, definition)
        try:
            table.build_index(definition)
        except Exception:
            self._catalog.drop_index(table.name, definition.name)
            table.discard_index_files(definition.name)
            raise
        self._reopen(table.name)
        return definition

    def _drop_table(self, statement: DropTableStatement) -> QueryResult:
        if statement.if_exists and not self._catalog.has_table(statement.name):
            return QueryResult(message=f"la tabla '{statement.name}' no existía")
        self._remove_table(statement.name)
        return QueryResult(message=f"tabla '{statement.name}' eliminada")

    def _drop_index(self, statement: DropIndexStatement) -> QueryResult:
        if statement.if_exists and not self._catalog.has_index(statement.name):
            return QueryResult(message=f"el índice '{statement.name}' no existía")
        definition, index = self._catalog.find_index(statement.name)
        table = self.table(definition.name)
        table.drop_index(index.name)
        self._catalog.drop_index(definition.name, index.name)
        self._reopen(definition.name)
        return QueryResult(message=f"índice '{index.name}' eliminado")

    def _load_rows(self, table: Table, path: Path, schema: Schema) -> int:
        """Inserta las filas del archivo. Si una falla, quien llama deshace la tabla entera:
        una carga a medias dejaría una tabla que nadie pidió con la mitad de sus filas."""
        loaded = 0
        for values in read_values(path, schema, self.config):
            table.insert(values)
            loaded += 1
        return loaded

    def _remove_table(self, name: str) -> None:
        table = self.table(name)
        table.remove_files()
        self._tables.pop(name.lower(), None)
        self._catalog.drop_table(name)

    def _register(self, definition: TableDefinition) -> None:
        """Da de alta la tabla y crea sus archivos.

        Si los archivos no se pueden crear —una fila que no cabe en una página, una clave
        demasiado larga para un nodo— la tabla no queda en el catálogo: registrada y sin
        poder abrirse, no se podría consultar, volver a crear ni borrar.
        """
        self._catalog.create_table(definition)
        try:
            self._tables[definition.name.lower()] = Table(definition, self.config)
        except Exception:
            self._catalog.drop_table(definition.name)
            discard_table_files(self.config.data_directory, definition.name)
            raise

    def _reopen(self, name: str) -> None:
        table = self._tables.pop(name.lower(), None)
        if table is not None:
            table.close()
        self._tables[name.lower()] = Table(self._catalog.table(name), self.config)

    def _declared_indexes(
        self,
        statement: CreateTableStatement,
        organization: Organization,
        key_method: IndexType | None,
    ) -> tuple[IndexDefinition, ...]:
        """Índices de la tabla recién declarada.

        En un heap file la clave primaria lleva siempre un índice: el que se declaró
        sobre ella (`INDEX HASH`) o, si no se declaró ninguno, un B+.
        """
        indexes = []
        for column in statement.columns:
            if column.primary_key and organization is Organization.HEAP:
                indexes.append(
                    IndexDefinition(
                        name=primary_key_index_name(statement.name),
                        column=column.name,
                        method=key_method or IndexType.BTREE,
                    )
                )
            if column.primary_key or (column.index is None and not column.unique):
                continue
            field = field_from_column(column, self.config)
            method = column.index or _default_method_for(field)
            _require_heap_for_secondary_index(organization, column.name)
            _require_method_for_column(field, method)
            indexes.append(
                IndexDefinition(
                    name=f"idx_{statement.name}_{column.name}",
                    column=column.name,
                    method=method,
                    unique=column.unique,
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
        evaluator = ExpressionEvaluator(NO_COLUMNS)
        for row in statement.rows:
            try:
                values = [evaluator.evaluate(expression, NO_ROW) for expression in row]
            except UnknownColumnError as error:
                raise EngineError(f"VALUES solo admite valores constantes: {error}") from error
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
        evaluator.validate(where)

        def matches(row: Record) -> bool:
            return evaluator.matches(where, row)

        return matches

    @staticmethod
    def _assignment_of(
        table: Table, assignments: Sequence[Assignment]
    ) -> Callable[[Record], Record]:
        evaluator = ExpressionEvaluator(RowLayout.of_table(table.schema, table.name))
        positions = [(table.schema.position_of(item.column), item.value) for item in assignments]
        for _, expression in positions:
            evaluator.validate(expression)

        def transform(row: Record) -> Record:
            updated = list(row)
            for position, expression in positions:
                updated[position] = evaluator.evaluate(expression, row)
            return tuple(updated)

        return transform



@dataclass(frozen=True, slots=True)
class _JournalOf:
    """Lleva al journal los cambios de una tabla: es el `RowChanges` que la tabla espera."""

    journal: Journal
    table: str

    def inserted(self, row: Record) -> None:
        self.journal.record_insert(self.table, row)

    def deleted(self, row: Record) -> None:
        self.journal.record_delete(self.table, row)

    def updated(self, before: Record, after: Record) -> None:
        self.journal.record_update(self.table, before, after)


def _require_heap_for_secondary_index(organization: Organization, column: str) -> None:
    """Un índice secundario guarda direcciones `(página, ranura)`, y solo el heap file las
    mantiene estables: el secuencial y el B+ agrupado mueven las filas al reorganizar o
    dividir nodos, y las direcciones apuntarían a otra fila.

    Raises:
        CatalogError: si la tabla no es un heap file.
    """
    if organization is not Organization.HEAP:
        raise CatalogError(
            f"no se puede indexar '{column}': los índices secundarios necesitan una tabla "
            f"heap file y esta es {organization.value}"
        )


def _require_method_for_column(field: Field, method: IndexType) -> None:
    """Un R-Tree indexa puntos y solo puntos; un B+ o un hash, cualquier cosa menos puntos.

    Un punto no tiene un orden total que un B+ pueda aprovechar, y un hash solo serviría
    para encontrar filas en unas coordenadas exactas.

    Raises:
        CatalogError: si el método no corresponde al tipo de la columna.
    """
    if method not in SECONDARY_INDEX_METHODS:
        raise CatalogError(
            f"el método {method.value} no sirve como índice secundario de '{field.name}': "
            "los disponibles son BTREE, HASH y RTREE"
        )
    is_point = field.type is FieldType.POINT
    if method is IndexType.RTREE and not is_point:
        raise CatalogError(
            f"un índice RTREE necesita una columna POINT y '{field.name}' es {field.type.value}"
        )
    if method is not IndexType.RTREE and is_point:
        raise CatalogError(
            f"la columna '{field.name}' es POINT: solo admite un índice RTREE, "
            f"no {method.value}"
        )


def _default_method_for(field: Field) -> IndexType:
    """Índice que recibe una columna `UNIQUE` que no declara ninguno.

    Comprobar que un valor no se repite es una búsqueda por igualdad: un hash, salvo en
    una columna POINT, que solo admite un R-Tree.
    """
    return IndexType.RTREE if field.type is FieldType.POINT else IndexType.HASH


def _key_method_of(statement: CreateTableStatement) -> IndexType | None:
    """Método declarado sobre la clave primaria, que decide la organización de la tabla.

    Raises:
        CatalogError: si ese método no sirve para identificar filas, como un R-Tree.
    """
    for column in statement.columns:
        if not column.primary_key or column.index is None:
            continue
        if column.index not in KEY_INDEX_METHODS:
            raise CatalogError(
                f"la clave primaria '{column.name}' no admite un índice "
                f"{column.index.value}: usa SEQ, BTREE o HASH"
            )
        return column.index
    return None


def _require_orderable_key(field: Field) -> None:
    """La clave primaria ordena o reparte las filas, así que su tipo tiene que tener orden.

    Raises:
        CatalogError: si la columna es de un tipo sin orden, como POINT o VECTOR.
    """
    if field.type not in ORDERED_FIELD_TYPES:
        raise CatalogError(
            f"la columna '{field.name}' es {field.type.value} y no puede ser clave primaria"
        )
