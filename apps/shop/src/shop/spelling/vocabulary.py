"""El vocabulario de las fichas y la corrección de una búsqueda contra él.

Las candidatas a distancia 1 salen de un índice de borrados (SymSpell con un borrado): cada
palabra se guarda también sin cada una de sus letras, y una palabra de la consulta encuentra
las suyas borrando a su vez una letra. Las de distancia 2, que el índice no cubre siempre,
se buscan solo si no hubo ninguna a distancia 1, entre las palabras de la misma inicial.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .distance import edit_distance

WORD_PATTERN = re.compile(r"[a-z]+")
QUERY_WORD_PATTERN = re.compile(r"[^\W\d_]+")
# Las palabras de la consulta booleana del gestor: no se corrigen.
BOOLEAN_OPERATORS = frozenset({"AND", "OR", "NOT"})
# Una palabra que sale una sola vez en 24 587 fichas suele ser a su vez una errata.
MIN_WORD_FREQUENCY = 2
MIN_WORD_LENGTH = 2
# «amp», «uke», «mic»: con menos letras cualquier corrección es una adivinanza.
MIN_CORRECTABLE_LENGTH = 4
# Hasta 5 letras se admite un error; desde 6, dos («amplifer», «guitarra» → «guitar»).
SECOND_EDIT_LENGTH = 6
# Una palabra del vocabulario también se corrige si una vecina sale este número de veces más:
# «amplifer» está en algunas fichas, pero «amplifier» en cientos.
DOMINANT_NEIGHBOUR_RATIO = 50
TITLE_COLUMNS = ("title", "brand")


def normalized(text: str) -> str:
    """Minúsculas y sin tildes: «Guitárra» → «guitarra»."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def words_of(text: str) -> list[str]:
    return [word for word in WORD_PATTERN.findall(normalized(text)) if len(word) >= MIN_WORD_LENGTH]


def _deletions(word: str) -> set[str]:
    return {word[:position] + word[position + 1 :] for position in range(len(word))}


@dataclass(frozen=True, slots=True)
class Correction:
    """La consulta corregida y las palabras que cambiaron, en orden."""

    query: str
    changes: tuple[tuple[str, str], ...]


class Vocabulary:
    """Las palabras de los títulos y marcas, con cuántas veces salen."""

    def __init__(self, frequencies: Counter[str]) -> None:
        self._frequencies = {
            word: count for word, count in frequencies.items() if count >= MIN_WORD_FREQUENCY
        }
        self._by_deletion: defaultdict[str, list[str]] = defaultdict(list)
        self._by_initial: defaultdict[str, list[str]] = defaultdict(list)
        for word in self._frequencies:
            self._by_deletion[word].append(word)
            for variant in _deletions(word):
                self._by_deletion[variant].append(word)
            self._by_initial[word[0]].append(word)

    @classmethod
    def from_texts(cls, texts: Iterable[str]) -> Vocabulary:
        frequencies: Counter[str] = Counter()
        for text in texts:
            frequencies.update(words_of(text))
        return cls(frequencies)

    def __len__(self) -> int:
        return len(self._frequencies)

    def __contains__(self, word: object) -> bool:
        return isinstance(word, str) and normalized(word) in self._frequencies

    def correct_word(self, word: str) -> str:
        """La palabra del vocabulario más cercana a `word`, la más frecuente si empatan;
        `word` si es corta, si ninguna queda a la distancia admitida o si ya está y ninguna
        vecina la supera `DOMINANT_NEIGHBOUR_RATIO` veces en frecuencia."""
        key = normalized(word)
        if len(key) < MIN_CORRECTABLE_LENGTH:
            return word
        limit = 2 if len(key) >= SECOND_EDIT_LENGTH else 1
        if key in self._frequencies:
            return self._dominant_neighbour(key, limit) or word
        best = self._closest(key, self._near_candidates(key), limit)
        if best is None and limit > 1:
            best = self._closest(key, self._by_initial.get(key[0], []), limit)
        return word if best is None else best

    def correct(self, query: str) -> Correction | None:
        """`query` con cada palabra desconocida cambiada por la más cercana; `None` si no
        cambia ninguna. Los operadores `AND`, `OR`, `NOT` y lo que no son letras quedan."""
        changes: list[tuple[str, str]] = []

        def replace(match: re.Match[str]) -> str:
            word = match.group(0)
            if word in BOOLEAN_OPERATORS:
                return word
            corrected = self.correct_word(word)
            if corrected != word:
                changes.append((word, corrected))
            return corrected

        corrected_query = QUERY_WORD_PATTERN.sub(replace, query)
        if not changes:
            return None
        return Correction(corrected_query, tuple(changes))

    def _dominant_neighbour(self, key: str, limit: int) -> str | None:
        threshold = self._frequencies[key] * DOMINANT_NEIGHBOUR_RATIO
        dominant = [
            candidate
            for candidate in self._near_candidates(key)
            if self._frequencies[candidate] >= threshold
        ]
        return self._closest(key, dominant, limit)

    def _near_candidates(self, key: str) -> set[str]:
        candidates = set(self._by_deletion.get(key, []))
        for variant in _deletions(key):
            candidates.update(self._by_deletion.get(variant, []))
        return candidates

    def _closest(self, key: str, candidates: Iterable[str], limit: int) -> str | None:
        best: tuple[int, int, str] | None = None
        for candidate in candidates:
            distance = edit_distance(key, candidate, limit)
            if distance > limit:
                continue
            ranking = (distance, -self._frequencies[candidate], candidate)
            if best is None or ranking < best:
                best = ranking
        return None if best is None else best[2]


def load_vocabulary(products_csv: Path) -> Vocabulary:
    """El vocabulario de los títulos y marcas de `products_csv` (el de `python -m
    shop.dataset`); vacío si el archivo no está.

    Complejidad: una pasada por el archivo (unos 35 MB en el dataset completo).
    """
    if not products_csv.is_file():
        return Vocabulary(Counter())
    with products_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return Vocabulary.from_texts(
            " ".join(row.get(column) or "" for column in TITLE_COLUMNS) for row in reader
        )
