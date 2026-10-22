"""El motor contra SQLite: consultas al azar, con semillas fijas, deben dar las mismas filas.

Los mismos datos se cargan en las dos bases y cada consulta se ejecuta en ambas. Se usa lo
que las dos definen igual: comparaciones, `BETWEEN`, `IN` con NULL, `LIKE` (SQLite con
`case_sensitive_like`), `IS NULL`, lógica de tres valores, `+ - * %`, reuniones internas y
externas, agrupación con sus agregados, `DISTINCT` y `ORDER BY … LIMIT … OFFSET`. Queda
fuera `/`, que en este motor siempre da un real.

Cada consulta corre sobre cuatro organizaciones de la tabla, con y sin índices: la respuesta
no puede depender del camino de acceso que elija el planificador.
"""

import random
import sqlite3
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence

import pytest

from config import EngineConfig
from query.engine import Engine

SEEDS = range(6)
QUERIES_PER_SEED = 40
ROWS = 60
DIGITS = 9
INTEGERS = [None, None, *range(-4, 6)]
REALS = [None, -2.5, -1.0, 0.0, 0.5, 1.0, 1.5, 3.25]
TEXTS = [None, "", "a", "ab", "abc", "b", "a_b", "ba", "zz"]
COMPARISONS = ["=", "<>", "<", "<=", ">", ">="]
LIKE_PATTERNS = ["a%", "%b", "_", "%", "a_b", "%a%", ""]
ORGANIZATIONS = {
    "heap": "id INT, a INT, b FLOAT, s VARCHAR(6)",
    "indexed": "id INT PRIMARY KEY, a INT INDEX BTREE, b FLOAT INDEX HASH, s VARCHAR(6) INDEX BTREE",
    "sequential": "id INT PRIMARY KEY INDEX SEQ, a INT, b FLOAT, s VARCHAR(6)",
    "clustered": "id INT PRIMARY KEY INDEX BTREE, a INT, b FLOAT, s VARCHAR(6)",
}
SQLITE_COLUMNS = "id INTEGER, a INTEGER, b REAL, s TEXT"


def literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return repr(value)


class Queries:
    """Generador de consultas sobre las tablas `l` y `r`, de columnas `id, a, b, s`."""

    def __init__(self, seed: int) -> None:
        self._random = random.Random(seed)

    def __iter__(self) -> Iterator[tuple[str, bool]]:
        """Pares `(consulta, el orden importa)`."""
        kinds = [self._filtered, self._joined, self._grouped, self._distinct, self._ordered]
        for _ in range(QUERIES_PER_SEED):
            yield self._random.choice(kinds)()

    def _filtered(self) -> tuple[str, bool]:
        return f"SELECT id, a, b, s FROM l{self._where('l.')}", False

    def _joined(self) -> tuple[str, bool]:
        kind = self._random.choice(["JOIN", "LEFT JOIN"])
        extra = f" AND {self._atom('r.')}" if self._random.random() < 0.3 else ""
        return f"SELECT l.id, r.id, r.s FROM l {kind} r ON l.a = r.a{extra}{self._where('l.')}", False

    def _grouped(self) -> tuple[str, bool]:
        key = self._random.choice(["a", "s", "a % 3"])
        having = f" HAVING COUNT(*) > {self._random.randint(0, 3)}" if self._random.random() < 0.4 else ""
        return (
            f"SELECT {key}, COUNT(*), COUNT(b), SUM(a), MIN(s), MAX(b) FROM l"
            f"{self._where('')} GROUP BY {key}{having}",
            False,
        )

    def _distinct(self) -> tuple[str, bool]:
        return f"SELECT DISTINCT a, s FROM l{self._where('')}", False

    def _ordered(self) -> tuple[str, bool]:
        direction = self._random.choice(["", " DESC"])
        limit = f" LIMIT {self._random.randint(0, 8)} OFFSET {self._random.randint(0, 5)}"
        return f"SELECT id, a FROM l{self._where('')} ORDER BY id{direction}{limit}", True

    def _where(self, prefix: str) -> str:
        if self._random.random() < 0.2:
            return ""
        return f" WHERE {self._predicate(prefix, depth=0)}"

    def _predicate(self, prefix: str, depth: int) -> str:
        choice = self._random.random()
        if depth >= 2 or choice < 0.45:
            return self._atom(prefix)
        left, right = self._predicate(prefix, depth + 1), self._predicate(prefix, depth + 1)
        if choice < 0.7:
            return f"({left} AND {right})"
        if choice < 0.9:
            return f"({left} OR {right})"
        return f"NOT ({left})"

    def _atom(self, prefix: str) -> str:
        number = f"{prefix}{self._random.choice(['id', 'a', 'b'])}"
        text = f"{prefix}s"
        options = [
            lambda: f"{number} {self._random.choice(COMPARISONS)} {literal(self._random.choice(INTEGERS[2:] + REALS[1:]))}",
            lambda: f"{number} {self._negation()}BETWEEN {self._random.randint(-5, 2)} AND {self._random.randint(0, 40)}",
            lambda: f"{number} {self._negation()}IN ({', '.join(literal(self._random.choice(INTEGERS)) for _ in range(3))})",
            lambda: f"{text} {self._negation()}LIKE {literal(self._random.choice(LIKE_PATTERNS))}",
            lambda: f"{self._random.choice([number, text])} IS {self._negation()}NULL",
            lambda: f"{text} {self._random.choice(COMPARISONS)} {literal(self._random.choice(TEXTS[1:]))}",
            lambda: f"{prefix}a % 3 {self._random.choice(COMPARISONS)} {self._random.randint(-2, 2)}",
            lambda: f"{prefix}a + {prefix}id * 2 {self._random.choice(COMPARISONS)} {self._random.randint(-5, 60)}",
        ]
        return self._random.choice(options)()

    def _negation(self) -> str:
        return self._random.choice(["", "NOT "])


def rows_for(seed: int) -> dict[str, list[tuple[object, ...]]]:
    generator = random.Random(seed)
    return {
        table: [
            (
                number,
                generator.choice(INTEGERS),
                generator.choice(REALS),
                generator.choice(TEXTS),
            )
            for number in range(ROWS)
        ]
        for table in ("l", "r")
    }


def normalized(rows: Iterable[Sequence[object]]) -> list[tuple[object, ...]]:
    """Filas comparables entre las dos bases: los reales, redondeados, porque una suma de
    reales puede diferir en la última cifra según el orden en que se sume."""
    return [
        tuple(round(value, DIGITS) if isinstance(value, float) else value for value in row)
        for row in rows
    ]


def unordered(rows: list[tuple[object, ...]]) -> Counter[tuple[object, ...]]:
    return Counter(rows)


@pytest.fixture(params=sorted(ORGANIZATIONS))
def organization(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.mark.parametrize("seed", SEEDS)
def test_random_queries_answer_like_sqlite(config: EngineConfig, organization: str, seed: int):
    data = rows_for(seed)
    reference = sqlite3.connect(":memory:")
    reference.execute("PRAGMA case_sensitive_like = ON")
    with Engine(config) as engine:
        for table, rows in data.items():
            columns = ORGANIZATIONS[organization] if table == "l" else ORGANIZATIONS["heap"]
            engine.execute(f"CREATE TABLE {table} ({columns})")
            engine.execute(f"INSERT INTO {table} VALUES " + ", ".join(
                "(" + ", ".join(map(literal, row)) + ")" for row in rows
            ))
            reference.execute(f"CREATE TABLE {table} ({SQLITE_COLUMNS})")
            reference.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?)", rows)
        for query, ordered in Queries(seed):
            ours = normalized(engine.execute(query).rows)
            theirs = normalized(reference.execute(query).fetchall())
            if ordered:
                assert ours == theirs, query
            else:
                assert unordered(ours) == unordered(theirs), query
    reference.close()
