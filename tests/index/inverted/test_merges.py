"""Las mezclas lineales de las consultas booleanas, contra las operaciones de conjuntos."""

import random
from collections.abc import Callable, Sequence

import pytest

from index.inverted.merges import (
    difference,
    intersect,
    intersect_all,
    matching_documents,
    union,
    union_all,
)
from text import BooleanExpression, Conjunction, Disjunction, Term

Merge = Callable[[Sequence[int], Sequence[int]], Sequence[int]]
MERGES: list[tuple[Merge, Callable[[set[int], set[int]], set[int]]]] = [
    (intersect, set.intersection),
    (union, set.union),
    (difference, set.difference),
]
PAIRS = {
    "vacías": ([], []),
    "vacía a la izquierda": ([], [3, 8]),
    "vacía a la derecha": ([3, 8], []),
    "un elemento igual": ([5], [5]),
    "un elemento distinto": ([5], [6]),
    "disjuntas": ([1, 2, 3], [7, 8, 9]),
    "iguales": ([2, 4, 6, 8], [2, 4, 6, 8]),
    "intercaladas": ([1, 4, 5, 9, 12], [2, 4, 9, 10, 12, 15]),
    "una contiene a la otra": ([1, 2, 3, 4, 5, 6], [2, 5]),
}
RANDOM_PAIRS = 300
UNIVERSE = 60


@pytest.mark.parametrize(("merge", "model"), MERGES, ids=lambda item: item.__name__)
@pytest.mark.parametrize(("first", "second"), list(PAIRS.values()), ids=list(PAIRS))
def test_each_merge_is_its_set_operation_in_order(
    merge: Merge, model: Callable, first: list[int], second: list[int]
):
    assert list(merge(first, second)) == sorted(model(set(first), set(second)))


@pytest.mark.parametrize(("merge", "model"), MERGES, ids=lambda item: item.__name__)
def test_each_merge_is_its_set_operation_on_random_lists(merge: Merge, model: Callable):
    generator = random.Random(7)
    for _ in range(RANDOM_PAIRS):
        first = sorted(generator.sample(range(UNIVERSE), generator.randrange(UNIVERSE)))
        second = sorted(generator.sample(range(UNIVERSE), generator.randrange(UNIVERSE)))
        assert list(merge(first, second)) == sorted(model(set(first), set(second)))


class CountingList(list[int]):
    """Una lista que cuenta cuántas veces se lee un elemento."""

    def __init__(self, values: range) -> None:
        super().__init__(values)
        self.reads = 0

    def __getitem__(self, position):  # type: ignore[override]
        self.reads += 1
        return super().__getitem__(position)


def test_an_n_ary_intersection_starts_with_the_shortest_list_and_stops_when_empty():
    long_lists = [CountingList(range(0, 1000, 2)), CountingList(range(0, 1000, 3))]
    assert list(intersect_all(long_lists[0], [1], [7], long_lists[1])) == []
    assert [long.reads for long in long_lists] == [0, 0]


def test_an_n_ary_intersection_of_one_list_is_that_list():
    assert list(intersect_all([3, 4, 9])) == [3, 4, 9]
    assert list(intersect_all([1, 2, 3, 6], [2, 3, 6, 7], [0, 3, 6])) == [3, 6]


def test_an_n_ary_union_of_none_one_or_many_lists():
    assert list(union_all()) == []
    assert list(union_all([4, 5])) == [4, 5]
    assert list(union_all([1, 9], [], [2, 9, 10], [1, 3])) == [1, 2, 3, 9, 10]


LISTS = {
    "roj": [0, 2, 4, 6, 8],
    "azul": [1, 2, 3, 8],
    "cabl": [2, 3, 5, 8, 9],
    "usb": [],
}


def evaluated(expression: BooleanExpression) -> list[int]:
    return list(matching_documents(expression, LISTS.__getitem__))


def test_an_expression_is_evaluated_with_the_merges():
    roj, azul, cabl, usb = map(Term, ["roj", "azul", "cabl", "usb"])
    assert evaluated(roj) == [0, 2, 4, 6, 8]
    assert evaluated(Conjunction((roj, azul), ())) == [2, 8]
    assert evaluated(Disjunction((azul, cabl, usb))) == [1, 2, 3, 5, 8, 9]
    assert evaluated(Conjunction((roj,), (azul,))) == [0, 4, 6]
    assert evaluated(Conjunction((roj,), (azul, cabl))) == [0, 4, 6]
    assert evaluated(Conjunction((roj, usb), (azul,))) == []
    red_or_blue_but_not_cable_without_red = Conjunction(
        (Disjunction((roj, azul)),), (Conjunction((cabl,), (roj,)),)
    )
    assert evaluated(red_or_blue_but_not_cable_without_red) == [0, 1, 2, 4, 6, 8]


def test_an_and_that_is_already_empty_reads_no_excluded_list():
    reads: list[str] = []

    def doc_ids_of(term: str) -> list[int]:
        reads.append(term)
        return LISTS[term]

    expression = Conjunction((Term("usb"), Term("roj")), (Term("azul"),))
    assert list(matching_documents(expression, doc_ids_of)) == []
    assert "azul" not in reads
