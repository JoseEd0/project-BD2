"""Las mezclas lineales de las consultas booleanas sobre listas de `doc_id` ordenadas.

Son el `INTERSECT` de las diapositivas y sus dos variantes: las tres avanzan un cursor por
lista, una sola vez, y entregan una lista también ordenada y sin repetidos, así que sus
resultados se pueden volver a mezclar. Con listas de `n` y `m` documentos cuestan
`O(n + m)` comparaciones. `matching_documents` evalúa con ellas una expresión entera.
"""

from __future__ import annotations

from array import array
from collections.abc import Callable, Sequence

from text import BooleanExpression, Disjunction, Term

from .format import POSTING_TYPECODE

DocIds = Sequence[int]


def intersect(first: DocIds, second: DocIds) -> array[int]:
    """Los documentos de las dos listas, para `t1 AND t2`, en `O(n + m)`."""
    result = array(POSTING_TYPECODE)
    i = j = 0
    first_length, second_length = len(first), len(second)
    while i < first_length and j < second_length:
        left, right = first[i], second[j]
        if left == right:
            result.append(left)
            i += 1
            j += 1
        elif left < right:
            i += 1
        else:
            j += 1
    return result


def union(first: DocIds, second: DocIds) -> array[int]:
    """Los documentos de alguna de las dos listas, para `t1 OR t2`, en `O(n + m)`."""
    result = array(POSTING_TYPECODE)
    i = j = 0
    first_length, second_length = len(first), len(second)
    while i < first_length and j < second_length:
        left, right = first[i], second[j]
        if left == right:
            result.append(left)
            i += 1
            j += 1
        elif left < right:
            result.append(left)
            i += 1
        else:
            result.append(right)
            j += 1
    result.extend(first[i:])
    result.extend(second[j:])
    return result


def difference(first: DocIds, second: DocIds) -> array[int]:
    """Los documentos de la primera lista que no están en la segunda, para `t1 AND NOT t2`,
    en `O(n + m)`: el ejercicio de las diapositivas. No hace falta el complemento de `t2`,
    que tendría casi todo el índice."""
    result = array(POSTING_TYPECODE)
    i = j = 0
    first_length, second_length = len(first), len(second)
    while i < first_length and j < second_length:
        left, right = first[i], second[j]
        if left < right:
            result.append(left)
            i += 1
        elif left == right:
            i += 1
            j += 1
        else:
            j += 1
    result.extend(first[i:])
    return result


def intersect_all(first: DocIds, *others: DocIds) -> array[int]:
    """La intersección de una o más listas, de la más corta a la más larga: ningún
    resultado intermedio pasa de la más corta, así que mezclar las `k` cuesta
    `O(k · n_min + Σ n_i)` como mucho, y se detiene en cuanto queda vacía."""
    shortest, *longer = sorted([first, *others], key=len)
    result = array(POSTING_TYPECODE, shortest)
    for following in longer:
        if not result:
            break
        result = intersect(result, following)
    return result


def union_all(*lists: DocIds) -> array[int]:
    """La unión de cualquier número de listas, también de la más corta a la más larga para
    que las primeras mezclas sean las baratas; la de ninguna está vacía."""
    result = array(POSTING_TYPECODE)
    for following in sorted(lists, key=len):
        result = union(result, following)
    return result


def matching_documents(
    expression: BooleanExpression, doc_ids_of: Callable[[str], DocIds]
) -> DocIds:
    """Los documentos que cumplen la expresión, en orden de `doc_id`, con `doc_ids_of` dando
    la lista ordenada de cada término. Cada nodo es una mezcla de las de arriba, así que el
    costo es lineal en las listas que toca: un `AND` interseca sus operandos del más corto
    al más largo y después les resta, uno a uno, los que van con `NOT`."""
    if isinstance(expression, Term):
        return doc_ids_of(expression.term)
    if isinstance(expression, Disjunction):
        return union_all(*(matching_documents(part, doc_ids_of) for part in expression.operands))
    included = (matching_documents(part, doc_ids_of) for part in expression.included)
    result = intersect_all(*included)
    for part in expression.excluded:
        if not result:
            break
        result = difference(result, matching_documents(part, doc_ids_of))
    return result
