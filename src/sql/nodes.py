"""Abstract syntax tree produced by the SQL parser.

Every node is immutable so a parsed statement can be cached and shared between
the planner and the executor without defensive copies.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, unique
from types import MappingProxyType

LiteralValue = int | float | str | bool | None
OptionValue = int | float | str | bool | None

EMPTY_OPTIONS: Mapping[str, OptionValue] = MappingProxyType({})
EMPTY_KEYWORD_ARGUMENTS: Mapping[str, Expression] = MappingProxyType({})


# Un `MappingProxyType` no es hashable hasta Python 3.12, y `dataclass` rechaza como valor por
# defecto todo lo que no lo sea. La fábrica devuelve siempre el mismo mapeo vacío e inmutable.
def no_options() -> Mapping[str, OptionValue]:
    return EMPTY_OPTIONS


def no_keyword_arguments() -> Mapping[str, Expression]:
    return EMPTY_KEYWORD_ARGUMENTS


@unique
class BinaryOperator(Enum):
    OR = "OR"
    AND = "AND"
    EQUAL = "="
    NOT_EQUAL = "<>"
    LESS = "<"
    LESS_EQUAL = "<="
    GREATER = ">"
    GREATER_EQUAL = ">="
    ADD = "+"
    SUBTRACT = "-"
    MULTIPLY = "*"
    DIVIDE = "/"
    MODULO = "%"


@unique
class UnaryOperator(Enum):
    NOT = "NOT"
    NEGATE = "-"


@unique
class SortDirection(Enum):
    ASCENDING = "ASC"
    DESCENDING = "DESC"


@unique
class JoinKind(Enum):
    INNER = "INNER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    FULL = "FULL"


@unique
class DataTypeKind(Enum):
    INT = "INT"
    BIGINT = "BIGINT"
    FLOAT = "FLOAT"
    DOUBLE = "DOUBLE"
    BOOLEAN = "BOOLEAN"
    CHAR = "CHAR"
    VARCHAR = "VARCHAR"
    TEXT = "TEXT"
    DATE = "DATE"
    POINT = "POINT"
    VECTOR = "VECTOR"
    BLOB = "BLOB"


@unique
class IndexType(Enum):
    SEQUENTIAL = "SEQUENTIAL"
    BTREE = "BTREE"
    HASH = "HASH"
    RTREE = "RTREE"
    INVERTED = "INVERTED"
    IVF = "IVF"
    HNSW = "HNSW"


@unique
class RankingMethod(Enum):
    TF_IDF = "TF_IDF"
    BM25 = "BM25"


SearchMethod = IndexType | RankingMethod


class Expression:
    """Marker base class for value-producing nodes."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Literal(Expression):
    value: LiteralValue


@dataclass(frozen=True, slots=True)
class ColumnRef(Expression):
    name: str
    qualifier: str | None = None


@dataclass(frozen=True, slots=True)
class Star(Expression):
    qualifier: str | None = None


@dataclass(frozen=True, slots=True)
class FunctionCall(Expression):
    name: str
    arguments: tuple[Expression, ...] = ()
    keyword_arguments: Mapping[str, Expression] = field(default_factory=no_keyword_arguments)


@dataclass(frozen=True, slots=True)
class TupleExpression(Expression):
    """Parenthesized list of values, e.g. the vertices passed to POLYGON."""

    elements: tuple[Expression, ...]


@dataclass(frozen=True, slots=True)
class UnaryOperation(Expression):
    operator: UnaryOperator
    operand: Expression


@dataclass(frozen=True, slots=True)
class BinaryOperation(Expression):
    operator: BinaryOperator
    left: Expression
    right: Expression


@dataclass(frozen=True, slots=True)
class BetweenPredicate(Expression):
    operand: Expression
    lower: Expression
    upper: Expression
    negated: bool = False


@dataclass(frozen=True, slots=True)
class InPredicate(Expression):
    operand: Expression
    values: tuple[Expression, ...]
    negated: bool = False


@dataclass(frozen=True, slots=True)
class LikePredicate(Expression):
    operand: Expression
    pattern: Expression
    negated: bool = False


@dataclass(frozen=True, slots=True)
class NullPredicate(Expression):
    operand: Expression
    negated: bool = False


def operands(expression: Expression) -> tuple[Expression, ...]:
    """Direct children of an expression node; empty for literals, columns and `*`."""
    if isinstance(expression, FunctionCall):
        return (*expression.arguments, *expression.keyword_arguments.values())
    if isinstance(expression, TupleExpression):
        return expression.elements
    if isinstance(expression, UnaryOperation):
        return (expression.operand,)
    if isinstance(expression, BinaryOperation):
        return (expression.left, expression.right)
    if isinstance(expression, BetweenPredicate):
        return (expression.operand, expression.lower, expression.upper)
    if isinstance(expression, InPredicate):
        return (expression.operand, *expression.values)
    if isinstance(expression, LikePredicate):
        return (expression.operand, expression.pattern)
    if isinstance(expression, NullPredicate):
        return (expression.operand,)
    return ()


def expression_depth(expression: Expression) -> int:
    """Number of levels of the tree, counted without recursion so any tree can be measured."""
    depth = 0
    level: list[Expression] = [expression]
    while level:
        depth += 1
        level = [child for node in level for child in operands(node)]
    return depth


@dataclass(frozen=True, slots=True)
class DataType:
    kind: DataTypeKind
    size: int | None = None


@dataclass(frozen=True, slots=True)
class ColumnDefinition:
    name: str
    data_type: DataType
    primary_key: bool = False
    nullable: bool = True
    unique: bool = False
    index: IndexType | None = None


@dataclass(frozen=True, slots=True)
class TableRef:
    name: str
    alias: str | None = None

    @property
    def reference(self) -> str:
        """How the query refers to the table: its alias, or its name when it has none."""
        return self.alias or self.name


@dataclass(frozen=True, slots=True)
class Join:
    kind: JoinKind
    table: TableRef
    condition: Expression


@dataclass(frozen=True, slots=True)
class Projection:
    expression: Expression
    alias: str | None = None


@dataclass(frozen=True, slots=True)
class OrderItem:
    expression: Expression
    direction: SortDirection = SortDirection.ASCENDING


@dataclass(frozen=True, slots=True)
class Assignment:
    column: str
    value: Expression


class Statement:
    """Marker base class for top-level SQL commands."""

    __slots__ = ()


class DefinitionStatement(Statement):
    """Marker base class for the statements that change the catalog (DDL)."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class SelectStatement(Statement):
    projections: tuple[Projection, ...]
    source: TableRef
    distinct: bool = False
    joins: tuple[Join, ...] = ()
    where: Expression | None = None
    group_by: tuple[Expression, ...] = ()
    having: Expression | None = None
    search_method: SearchMethod | None = None
    options: Mapping[str, OptionValue] = field(default_factory=no_options)
    order_by: tuple[OrderItem, ...] = ()
    limit: int | None = None
    offset: int | None = None


@dataclass(frozen=True, slots=True)
class InsertStatement(Statement):
    table: str
    rows: tuple[tuple[Expression, ...], ...]
    columns: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class UpdateStatement(Statement):
    table: str
    assignments: tuple[Assignment, ...]
    where: Expression | None = None


@dataclass(frozen=True, slots=True)
class DeleteStatement(Statement):
    table: str
    where: Expression | None = None


@dataclass(frozen=True, slots=True)
class CreateTableStatement(DefinitionStatement):
    name: str
    columns: tuple[ColumnDefinition, ...]
    if_not_exists: bool = False


@dataclass(frozen=True, slots=True)
class IndexSpec:
    method: IndexType
    columns: tuple[str, ...]
    options: Mapping[str, OptionValue] = field(default_factory=no_options)


@dataclass(frozen=True, slots=True)
class CreateTableFromFileStatement(DefinitionStatement):
    """`CREATE TABLE t FROM FILE "path" USING INDEX BTREE("id")`.

    The schema is inferred from the file header by the loader, so this node only
    carries the source path and the optional index to build while loading.
    """

    name: str
    path: str
    index: IndexSpec | None = None
    if_not_exists: bool = False


@dataclass(frozen=True, slots=True)
class CreateIndexStatement(DefinitionStatement):
    table: str
    spec: IndexSpec
    name: str | None = None
    if_not_exists: bool = False


@dataclass(frozen=True, slots=True)
class DropTableStatement(DefinitionStatement):
    name: str
    if_exists: bool = False


@dataclass(frozen=True, slots=True)
class DropIndexStatement(DefinitionStatement):
    name: str
    table: str | None = None
    if_exists: bool = False


@dataclass(frozen=True, slots=True)
class BeginTransactionStatement(Statement):
    pass


@dataclass(frozen=True, slots=True)
class CommitTransactionStatement(Statement):
    pass


@dataclass(frozen=True, slots=True)
class RollbackTransactionStatement(Statement):
    pass


@dataclass(frozen=True, slots=True)
class ExplainStatement(Statement):
    """`EXPLAIN [ANALYZE] SELECT ...`.

    Sin `ANALYZE` se devuelve el plan sin ejecutar la consulta; con `ANALYZE` se ejecuta y
    cada operador informa de las filas que produjo y del tiempo que tardó.
    """

    query: SelectStatement
    analyze: bool = False
