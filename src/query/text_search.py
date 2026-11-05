"""Reconocimiento de la búsqueda de texto dentro de un SELECT: `MATCH`, `SCORE()`, `USING` y
`WITH`.

Aquí se decide si una consulta busca texto, en qué columna, con qué índice y con qué
ranking, y se rechaza antes de leer ninguna fila la que no se puede responder. El
planificador lo usa para elegir el acceso y el motor para decirle a la interfaz qué
términos resaltar, de modo que los dos miran la consulta con los mismos ojos.

| Forma en SQL | Qué es |
|---|---|
| `WHERE MATCH(columna, 'consulta')` | las filas con algún término de la consulta |
| `WHERE MATCH(columna, 'a AND NOT b')` | las filas que cumplen la expresión booleana |
| `USING TF_IDF` / `USING BM25` | el ranking; sin `USING`, BM25 |
| `WITH (k1 = 1.5, b = 0.6)` | los parámetros de BM25 para esta consulta |
| `SCORE()` | el puntaje de cada fila |

`MATCH` no es una función que se evalúe fila a fila: es la condición que resuelve el índice
invertido, y por eso tiene que ir en el `WHERE` unida al resto con `AND`, sobre una columna
con ese índice y con una consulta constante.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from config import EngineConfig
from errors import DomainError
from index.inverted import Bm25Parameters, RankingMethod, require_fitting_parameters
from query.expressions import (
    RowLayout,
    all_of,
    conjuncts,
    constant_value,
    describe_expression,
    order_key,
    rewritten,
    subexpressions,
)
from query.functions import MATCH, SCORE, is_call_to
from query.index_methods import TEXT_FIELD_TYPES, Capability
from query.table import Table, index_with_capability
from sql.nodes import (
    EMPTY_OPTIONS,
    ColumnRef,
    Expression,
    FunctionCall,
    OptionValue,
    Projection,
    SearchMethod,
    SelectStatement,
    SortDirection,
    TableRef,
)
from sql.nodes import RankingMethod as SqlRankingMethod
from storage.schema import Field
from storage.types import is_number

# El nombre de la columna virtual del puntaje. Sin comillas no se puede escribir en SQL, así
# que no choca con ninguna columna de la tabla; `SCORE()` es la forma de leerla.
SCORE_COLUMN = "score()"
SCORE_REFERENCE = ColumnRef(SCORE_COLUMN)
# Cómo se llama en el resultado un `SCORE()` del SELECT sin alias, igual que `distancia(…)`
# se llama `distancia`.
SCORE_LABEL = SCORE.lower()
# BM25 fue el más relevante en los tres tamaños del experimento de la Parte 3 (Precision@10
# de 0,35 frente a 0,28 de TF-IDF con 100 000 productos), y es el ranking por omisión de
# Lucene.
DEFAULT_RANKING_METHOD = RankingMethod.BM25
K1_OPTION = "k1"
B_OPTION = "b"
BM25_OPTIONS = (K1_OPTION, B_OPTION)
MATCH_ARGUMENTS = 2


class TextSearchError(DomainError):
    """La búsqueda de texto de la consulta no se puede responder tal como está escrita."""


@dataclass(frozen=True, slots=True)
class TextSearch:
    """Un `MATCH` ya resuelto contra el catálogo.

    Attributes:
        table: la tabla en la que se busca.
        reference: el nombre o alias con que la consulta nombra esa tabla.
        column: la columna de texto, tal como la declara la tabla.
        index_name: su índice invertido.
        query: el texto que se busca, sin analizar.
        expression: si la consulta es booleana, la expresión tal como se evalúa, con los
            términos analizados; `None` si es texto libre.
        method: el ranking.
        parameters: los de BM25, ya con los de la configuración donde la consulta no los
            pide; `None` con TF-IDF.
        condition: el `MATCH` del WHERE, que el acceso consume.
    """

    table: Table
    reference: str
    column: str
    index_name: str
    query: str
    expression: str | None
    method: RankingMethod
    parameters: Bm25Parameters | None
    condition: FunctionCall


@dataclass(frozen=True, slots=True)
class TextView:
    """Lo que la interfaz necesita para resaltar el resultado de un `MATCH`.

    Attributes:
        table: tabla en la que se buscó.
        column: columna de texto.
        query: la consulta tal como se escribió.
        method: el ranking con que se ordenó.
        terms: los términos de la consulta ya analizados —plegados, sin stopwords y
            reducidos a su raíz—, en el orden en que aparecen; de una consulta booleana,
            solo los positivos, que son los que pueden aparecer en el resultado.
        parameters: los `k1` y `b` con que puntuó BM25, los de la consulta o los de la
            configuración; `None` con TF-IDF.
    """

    table: str
    column: str
    query: str
    method: RankingMethod
    terms: tuple[str, ...]
    parameters: Bm25Parameters | None


def text_search(
    statement: SelectStatement, tables: Mapping[str, Table], config: EngineConfig
) -> TextSearch | None:
    """El `MATCH` del WHERE resuelto, o `None` si la consulta no busca texto.

    Raises:
        TextSearchError: si hay más de un `MATCH`, si está dentro de un `OR` o un `NOT`, si
            sus argumentos no son una columna y una consulta constante, si la columna no
            tiene índice invertido, si el `USING` no es un ranking de texto o si el `WITH`
            no son parámetros válidos de BM25; también si hay un `SCORE()` sin `MATCH`.
        BooleanQueryError: si la consulta es booleana y está mal formada.
        ExpressionError: si la columna no existe o es ambigua.
    """
    found = _calls_to(MATCH, statement.where)
    if not found:
        _require_no_score(statement)
        return None
    if len(found) > 1:
        raise TextSearchError(
            "una consulta admite un solo MATCH: junta las palabras en una sola consulta"
        )
    condition = found[0]
    if not any(part is condition for part in conjuncts(statement.where)):
        raise TextSearchError(
            "MATCH no se puede negar ni combinar con OR: va en el WHERE, unido al resto de "
            "las condiciones con AND"
        )
    column, query = _match_arguments(condition)
    source, field = _searched_column(statement, tables, column)
    table = tables[source.name.lower()]
    index = index_with_capability(table.definition, field.name, Capability.TEXT_SEARCH)
    if index is None:
        raise TextSearchError(
            f"la columna '{field.name}' de '{table.name}' no tiene índice invertido; créalo con "
            f"CREATE INDEX ON {table.name} USING INVERTED ({field.name})"
        )
    method = _ranking_method(statement.search_method)
    return TextSearch(
        table=table,
        reference=source.reference,
        column=field.name,
        index_name=index.name,
        query=query,
        expression=table.text_search_index(index.name).ranker().expression(query),
        method=method,
        parameters=_parameters(method, statement.options, config),
        condition=condition,
    )


def served(statement: SelectStatement, search: TextSearch) -> SelectStatement:
    """La consulta que queda cuando el acceso de texto ya resolvió el `MATCH`.

    El `MATCH` sale del WHERE: el acceso solo entrega filas que lo cumplen, así que el
    filtro no lo vuelve a evaluar. Cada `SCORE()` pasa a leer la columna del puntaje que
    entrega el acceso, y el `USING` y el `WITH` ya se usaron.

    Raises:
        TextSearchError: si un `SCORE()` lleva argumentos.
    """
    residual = [part for part in conjuncts(statement.where) if part is not search.condition]
    having = statement.having
    return replace(
        statement,
        projections=tuple(map(_scored_projection, statement.projections)),
        joins=tuple(
            replace(join, condition=_reading_score(join.condition)) for join in statement.joins
        ),
        where=all_of([_reading_score(part) for part in residual]),
        group_by=tuple(map(_reading_score, statement.group_by)),
        having=None if having is None else _reading_score(having),
        order_by=tuple(
            replace(item, expression=_reading_score(item.expression))
            for item in statement.order_by
        ),
        search_method=None,
        options=EMPTY_OPTIONS,
    )


def ordered_by_score(statement: SelectStatement) -> bool:
    """Si la consulta ordena solo por el puntaje, de mayor a menor —el orden en que el
    acceso de texto entrega las filas—, también a través de un alias del SELECT."""
    if len(statement.order_by) != 1:
        return False
    item = statement.order_by[0]
    if item.direction is not SortDirection.DESCENDING:
        return False
    return order_key(item.expression, statement.projections) == SCORE_REFERENCE


def text_view(
    statement: SelectStatement, tables: Mapping[str, Table], config: EngineConfig
) -> TextView | None:
    """Tabla, columna y términos que la interfaz debe resaltar; `None` si no hay `MATCH`."""
    search = text_search(statement, tables, config)
    if search is None:
        return None
    ranker = search.table.text_search_index(search.index_name).ranker()
    return TextView(
        table=search.table.name,
        column=search.column,
        query=search.query,
        method=search.method,
        terms=tuple(ranker.analyzed(search.query)),
        parameters=search.parameters,
    )


def _calls_to(name: str, expression: Expression | None) -> list[FunctionCall]:
    """Todas las llamadas a esa función dentro de la expresión."""
    if expression is None:
        return []
    if isinstance(expression, FunctionCall) and is_call_to(expression.name, name):
        return [expression]
    return [call for child in subexpressions(expression) for call in _calls_to(name, child)]


def _require_no_score(statement: SelectStatement) -> None:
    expressions = [
        *(projection.expression for projection in statement.projections),
        *(join.condition for join in statement.joins),
        statement.where,
        *statement.group_by,
        statement.having,
        *(item.expression for item in statement.order_by),
    ]
    if any(_calls_to(SCORE, expression) for expression in expressions):
        raise TextSearchError(
            "SCORE() es el puntaje de un MATCH y esta consulta no tiene ninguno en su WHERE"
        )


def _match_arguments(condition: FunctionCall) -> tuple[ColumnRef, str]:
    """La columna y la consulta de `MATCH(columna, 'consulta')`."""
    if len(condition.arguments) != MATCH_ARGUMENTS or condition.keyword_arguments:
        raise TextSearchError("MATCH recibe la columna y la consulta: MATCH(texto, 'cable usb')")
    column, query = condition.arguments
    if not isinstance(column, ColumnRef):
        raise TextSearchError(
            f"el primer argumento de MATCH es la columna en la que se busca, no "
            f"'{describe_expression(column)}'"
        )
    value = constant_value(query)
    if not isinstance(value, str):
        raise TextSearchError(
            "la consulta de MATCH tiene que ser un texto constante, como 'cable usb', y es "
            f"'{describe_expression(query)}'"
        )
    return column, value


def _searched_column(
    statement: SelectStatement, tables: Mapping[str, Table], column: ColumnRef
) -> tuple[TableRef, Field]:
    """La tabla del FROM a la que pertenece la columna y su definición.

    Raises:
        UnknownColumnError: si ninguna tabla la tiene.
        AmbiguousColumnError: si la tienen varias y no está cualificada.
        TextSearchError: si no es una columna de texto.
    """
    sources = [statement.source, *(join.table for join in statement.joins)]
    owned = [
        (source, slot)
        for source in sources
        for slot in RowLayout.of_table(tables[source.name.lower()].schema, source.reference).slots
    ]
    combined = RowLayout([slot for _, slot in owned])
    source, slot = owned[combined.position_of(column.name, column.qualifier)]
    if slot.field is None or slot.field.type not in TEXT_FIELD_TYPES:
        kind = "?" if slot.field is None else slot.field.type.value
        raise TextSearchError(
            f"MATCH busca en una columna de texto (VARCHAR o TEXT) y '{column.name}' es {kind}"
        )
    return source, slot.field


def _ranking_method(chosen: SearchMethod | None) -> RankingMethod:
    if chosen is None:
        return DEFAULT_RANKING_METHOD
    if isinstance(chosen, SqlRankingMethod):
        return RankingMethod(chosen.value)
    raise TextSearchError(f"MATCH se ordena con USING TF_IDF o USING BM25, no con {chosen.value}")


def _parameters(
    method: RankingMethod, options: Mapping[str, OptionValue], config: EngineConfig
) -> Bm25Parameters | None:
    """Los parámetros de BM25 de la consulta; los que no pide salen de la configuración.

    Raises:
        TextSearchError: si el `WITH` pide algo que no es `k1` ni `b`, o no es un número.
        RankingParameterError: si están fuera de rango o se piden para TF-IDF.
    """
    named = {name.lower(): value for name, value in options.items()}
    unknown = sorted(set(named) - set(BM25_OPTIONS))
    if unknown:
        raise TextSearchError(
            f"el WITH de un MATCH lleva los parámetros de BM25, k1 y b; no {', '.join(unknown)}"
        )
    if not named:
        return Bm25Parameters.from_config(config) if method is RankingMethod.BM25 else None
    parameters = Bm25Parameters(
        _number(named, K1_OPTION, config.bm25_k1), _number(named, B_OPTION, config.bm25_b)
    )
    require_fitting_parameters(method, parameters)
    return parameters


def _number(options: Mapping[str, OptionValue], name: str, default: float) -> float:
    value = options.get(name, default)
    if not is_number(value):
        raise TextSearchError(f"{name} es un número y llegó {value!r}")
    return float(value)


def _scored_projection(projection: Projection) -> Projection:
    """La columna del SELECT leyendo el puntaje; un `SCORE()` sin alias se llama `score`."""
    expression = projection.expression
    if _is_score(expression) and projection.alias is None:
        return Projection(_reading_score(expression), SCORE_LABEL)
    return Projection(_reading_score(expression), projection.alias)


def _reading_score(expression: Expression) -> Expression:
    return rewritten(expression, _score_reference)


def _score_reference(expression: Expression) -> Expression | None:
    if not _is_score(expression):
        return None
    if isinstance(expression, FunctionCall) and (
        expression.arguments or expression.keyword_arguments
    ):
        raise TextSearchError("SCORE() no recibe argumentos: es el puntaje del MATCH")
    return SCORE_REFERENCE


def _is_score(expression: Expression) -> bool:
    return isinstance(expression, FunctionCall) and is_call_to(expression.name, SCORE)
