"""Las consultas booleanas de un `MATCH`: `AND`, `OR`, `NOT` y paréntesis.

Los operadores son esas tres palabras **en mayúsculas**; en minúsculas son palabras de la
consulta. La precedencia es `NOT` > `AND` > `OR`, y dos operandos seguidos sin operador se
unen con `OR`, como el texto libre:

    expresión   := conjunción { [OR] conjunción }
    conjunción  := negación { AND negación }
    negación    := NOT negación | primario
    primario    := palabra | ( expresión )

Una consulta sin operadores ni paréntesis no es booleana (`is_boolean_query`) y sigue el
camino del texto libre.

Cada palabra pasa por el analizador del índice: si da varios términos («iphone-12») es el
`AND` de todos, y si no da ninguno (una stopword) se quita de la expresión, con el operador
que la unía. Lo que queda se reescribe en una forma en la que cada `NOT` es la parte
excluida de un `AND`, que es como se evalúa: «a AND NOT b» es la diferencia de dos listas.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from errors import DomainError

from .analyzer import Analyzer

AND = "AND"
OR = "OR"
NOT = "NOT"
OPEN = "("
CLOSE = ")"
BINARY_OPERATORS = frozenset({AND, OR})
OPERATORS = frozenset({AND, OR, NOT})
SYNTAX_TOKENS = frozenset({AND, OR, NOT, OPEN, CLOSE})
# Una palabra es todo lo que no es espacio ni paréntesis: lo que el analizador corte dentro
# de ella («iphone-12») sigue siendo un solo operando.
QUERY_TOKEN = re.compile(r"[()]|[^\s()]+")


class BooleanQueryError(DomainError):
    """La consulta booleana de un `MATCH` está mal formada o no se puede responder."""


@dataclass(frozen=True, slots=True)
class Term:
    """Los documentos que contienen el término, ya analizado."""

    term: str


@dataclass(frozen=True, slots=True)
class Disjunction:
    """Los documentos de cualquiera de los operandos (`OR`)."""

    operands: tuple[BooleanExpression, ...]


@dataclass(frozen=True, slots=True)
class Conjunction:
    """Los documentos de todos los `included` y de ninguno de los `excluded`: el `AND`, con
    sus `NOT` como diferencia. `included` nunca está vacío."""

    included: tuple[BooleanExpression, ...]
    excluded: tuple[BooleanExpression, ...]


BooleanExpression = Term | Disjunction | Conjunction


@dataclass(frozen=True, slots=True)
class BooleanQuery:
    """Una consulta booleana analizada.

    Attributes:
        expression: lo que se evalúa; `None` si todos sus operandos eran stopwords, y
            entonces no coincide con ningún documento, como una consulta vacía.
        positive_terms: los términos que puntúan, con las veces que aparecen: los que no
            están bajo un `NOT`, o bajo un número par de ellos.
    """

    expression: BooleanExpression | None
    positive_terms: Mapping[str, int]

    def describe(self) -> str:
        """La expresión con los términos analizados y un paréntesis en cada operando
        compuesto; vacía si no queda ninguno."""
        return "" if self.expression is None else _described(self.expression)


def is_boolean_query(query: str) -> bool:
    """Si la consulta tiene algún operador o paréntesis."""
    return any(token in SYNTAX_TOKENS for token in QUERY_TOKEN.findall(query))


def parse_boolean_query(query: str, analyzer: Analyzer) -> BooleanQuery:
    """La consulta analizada con el analizador del índice. Complejidad lineal en su largo.

    Raises:
        BooleanQueryError: si un paréntesis no cierra o cierra sin abrir, si un operador
            no tiene operando o si la consulta solo excluye documentos (`NOT cesar`, o
            `a OR NOT b`), que sería casi todo el índice.
    """
    tokens = [_Token(found.group(), found.start()) for found in QUERY_TOKEN.finditer(query)]
    if not tokens:
        return BooleanQuery(None, {})
    normalized = _normalized(_Parser(tokens).parse(), analyzer)
    if normalized is None:
        return BooleanQuery(None, {})
    if normalized.complemented:
        raise BooleanQueryError(
            "la consulta solo excluye documentos: un NOT va unido con AND a algún término "
            "positivo, como 'funda AND NOT silicona'"
        )
    return BooleanQuery(normalized.expression, Counter(_positive_terms(normalized.expression)))


class _Token(NamedTuple):
    text: str
    offset: int

    @property
    def column(self) -> int:
        return self.offset + 1


@dataclass(frozen=True, slots=True)
class _Word:
    text: str


@dataclass(frozen=True, slots=True)
class _Not:
    operand: _Syntax


@dataclass(frozen=True, slots=True)
class _And:
    operands: tuple[_Syntax, ...]


@dataclass(frozen=True, slots=True)
class _Or:
    operands: tuple[_Syntax, ...]


_Syntax = _Word | _Not | _And | _Or


class _Parser:
    """Descenso recursivo sobre los tokens, una función por nivel de precedencia."""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._position = 0

    def parse(self) -> _Syntax:
        expression = self._expression()
        leftover = self._peek()
        if leftover is not None:
            raise _unopened(leftover)
        return expression

    def _expression(self) -> _Syntax:
        operands = [self._conjunction()]
        while self._continues_disjunction():
            self._accept(OR)
            operands.append(self._conjunction())
        return operands[0] if len(operands) == 1 else _Or(tuple(operands))

    def _conjunction(self) -> _Syntax:
        operands = [self._negation()]
        while self._accept(AND):
            operands.append(self._negation())
        return operands[0] if len(operands) == 1 else _And(tuple(operands))

    def _negation(self) -> _Syntax:
        if self._accept(NOT):
            return _Not(self._negation())
        return self._primary()

    def _primary(self) -> _Syntax:
        token = self._peek()
        if token is None or token.text in BINARY_OPERATORS or token.text == CLOSE:
            raise self._missing_operand(token)
        self._position += 1
        if token.text != OPEN:
            return _Word(token.text)
        inner = self._expression()
        if not self._accept(CLOSE):
            raise _unclosed(token)
        return inner

    def _continues_disjunction(self) -> bool:
        """Sigue un `OR`, escrito o implícito: un operando pegado al anterior."""
        token = self._peek()
        return token is not None and token.text not in {AND, CLOSE}

    def _accept(self, text: str) -> bool:
        token = self._peek()
        if token is None or token.text != text:
            return False
        self._position += 1
        return True

    def _peek(self) -> _Token | None:
        return self._tokens[self._position] if self._position < len(self._tokens) else None

    def _previous(self) -> _Token | None:
        return self._tokens[self._position - 1] if self._position > 0 else None

    def _missing_operand(self, found: _Token | None) -> BooleanQueryError:
        """Hace falta un operando y en su lugar viene `found`, o se acabó la consulta."""
        previous = self._previous()
        if previous is not None and previous.text in OPERATORS:
            return BooleanQueryError(
                f"{previous.text} de la posición {previous.column} no tiene operando a la "
                "derecha"
            )
        if found is None:
            # Sin operador delante, lo único que deja la consulta esperando un operando al
            # final es un paréntesis que abre.
            return _unclosed(self._tokens[-1])
        if found.text in BINARY_OPERATORS:
            return BooleanQueryError(
                f"{found.text} de la posición {found.column} no tiene operando a la izquierda"
            )
        if previous is None:
            return _unopened(found)
        return BooleanQueryError(f"los paréntesis de la posición {previous.column} están vacíos")


def _unclosed(token: _Token) -> BooleanQueryError:
    return BooleanQueryError(f"falta cerrar el paréntesis de la posición {token.column}")


def _unopened(token: _Token) -> BooleanQueryError:
    return BooleanQueryError(
        f"el paréntesis de la posición {token.column} cierra sin haberse abierto"
    )


class _Normalized(NamedTuple):
    """Una subexpresión ya analizada: o los documentos de `expression`, o, si
    `complemented`, todos menos esos."""

    expression: BooleanExpression
    complemented: bool


def _normalized(syntax: _Syntax, analyzer: Analyzer) -> _Normalized | None:
    """La subexpresión en la forma que se evalúa, o `None` si no le queda ningún término.

    Un `NOT` invierte el complemento. Un `AND` interseca sus partes positivas y les resta
    las complementadas; si no tiene ninguna positiva es el complemento de la unión de las
    otras. Un `OR` con alguna parte complementada es el complemento de lo que todas ellas
    excluyen a la vez menos lo que aportan las positivas.
    """
    if isinstance(syntax, _Word):
        terms = analyzer.terms(syntax.text)
        if not terms:
            return None
        return _Normalized(_conjunction([Term(term) for term in terms], []), False)
    if isinstance(syntax, _Not):
        inner = _normalized(syntax.operand, analyzer)
        return None if inner is None else inner._replace(complemented=not inner.complemented)
    normalized = (_normalized(operand, analyzer) for operand in syntax.operands)
    parts = [part for part in normalized if part is not None]
    if not parts:
        return None
    positive = [part.expression for part in parts if not part.complemented]
    complemented = [part.expression for part in parts if part.complemented]
    if isinstance(syntax, _And):
        if positive:
            return _Normalized(_conjunction(positive, complemented), False)
        return _Normalized(_disjunction(complemented), True)
    if not complemented:
        return _Normalized(_disjunction(positive), False)
    excluded = [_disjunction(positive)] if positive else []
    return _Normalized(_conjunction(complemented, excluded), True)


def _conjunction(
    included: list[BooleanExpression], excluded: list[BooleanExpression]
) -> BooleanExpression:
    """El `AND`, absorbiendo los `AND` anidados para que la intersección sea de n listas."""
    flat_included: list[BooleanExpression] = []
    flat_excluded = list(excluded)
    for operand in included:
        if isinstance(operand, Conjunction):
            flat_included.extend(operand.included)
            flat_excluded.extend(operand.excluded)
        else:
            flat_included.append(operand)
    if len(flat_included) == 1 and not flat_excluded:
        return flat_included[0]
    return Conjunction(tuple(flat_included), tuple(flat_excluded))


def _disjunction(operands: list[BooleanExpression]) -> BooleanExpression:
    flat: list[BooleanExpression] = []
    for operand in operands:
        flat.extend(operand.operands if isinstance(operand, Disjunction) else [operand])
    return flat[0] if len(flat) == 1 else Disjunction(tuple(flat))


def _positive_terms(expression: BooleanExpression) -> Iterator[str]:
    if isinstance(expression, Term):
        yield expression.term
    elif isinstance(expression, Disjunction):
        for operand in expression.operands:
            yield from _positive_terms(operand)
    else:
        for operand in expression.included:
            yield from _positive_terms(operand)
        for operand in expression.excluded:
            yield from _positive_terms_of_excluded(operand)


def _positive_terms_of_excluded(excluded: BooleanExpression) -> Iterator[str]:
    """Los términos positivos de lo que se excluye: los que esa parte, a su vez, excluye."""
    if isinstance(excluded, Disjunction):
        for operand in excluded.operands:
            yield from _positive_terms_of_excluded(operand)
    elif isinstance(excluded, Conjunction):
        for operand in excluded.included:
            yield from _positive_terms_of_excluded(operand)
        for operand in excluded.excluded:
            yield from _positive_terms(operand)


def _described_operand(expression: BooleanExpression) -> str:
    if isinstance(expression, Term):
        return expression.term
    return f"({_described(expression)})"


def _described(expression: BooleanExpression) -> str:
    if isinstance(expression, Term):
        return expression.term
    if isinstance(expression, Disjunction):
        return f" {OR} ".join(map(_described_operand, expression.operands))
    included = map(_described_operand, expression.included)
    excluded = (f"{NOT} {_described_operand(operand)}" for operand in expression.excluded)
    return f" {AND} ".join([*included, *excluded])
