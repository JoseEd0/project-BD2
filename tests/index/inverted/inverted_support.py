"""Construcción de índices de prueba, un índice de referencia en memoria y el oráculo del
ranking."""

import itertools
import math
import random
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from config import EngineConfig
from index.inverted import InvertedIndex, RankingMethod, ScoredDocument
from storage.record_id import RecordId
from text import AnalyzedText, Analyzer

# Palabras que el analizador deja casi intactas, con ñ, cifras y una stopword («para»).
WORDS = [
    "teclado", "raton", "pantalla", "cable", "cargador", "bateria", "ñandu", "año", "ano",
    "usb", "4k", "128gb", "para", "rojo", "azul", "funda", "auriculares", "camara", "lente",
]


def lnc_norm_by_definition(term_frequencies: Iterable[int]) -> float:
    """`√Σ (1 + log₁₀ tf)²`, escrita aquí y no tomada de `weights.py`: el oráculo de la norma."""
    return math.sqrt(math.fsum((1 + math.log10(tf)) ** 2 for tf in term_frequencies))


def record_id_of(position: int) -> RecordId:
    return RecordId(page_id=position // 10, slot=position % 10)


def documents_of(texts: list[str]) -> Iterator[tuple[RecordId, str]]:
    for position, text in enumerate(texts):
        yield record_id_of(position), text


def build(path: Path, texts: list[str], config: EngineConfig) -> InvertedIndex:
    return InvertedIndex.build(path, documents_of(texts), Analyzer(config), config)


def vocabulary(size: int, seed: int) -> list[str]:
    """`WORDS` más `size` palabras al azar."""
    generator = random.Random(seed)
    return WORDS + [_random_word(generator) for _ in range(size)]


def random_texts(count: int, seed: int, vocabulary_size: int = 400) -> list[str]:
    """Textos al azar sobre un vocabulario de Zipf: pocos términos muy frecuentes y una cola
    larga, como un corpus real."""
    generator = random.Random(seed)
    words = vocabulary(vocabulary_size, seed)
    weights = [1 / rank for rank in range(1, len(words) + 1)]
    return [
        " ".join(generator.choices(words, weights, k=generator.randrange(0, 40)))
        for _ in range(count)
    ]


def _random_word(generator: random.Random) -> str:
    letters = "abcdefghijklmnopqrstuvwxyzñ"
    return "".join(generator.choice(letters) for _ in range(generator.randrange(3, 12)))


@dataclass
class ReferenceIndex:
    """Lo que el índice tiene que contener, calculado directo sobre los textos."""

    postings: dict[str, dict[int, int]]
    lengths: list[int]
    norms: list[float]

    @classmethod
    def of(cls, texts: list[str], analyzer: Analyzer) -> "ReferenceIndex":
        postings: dict[str, dict[int, int]] = {}
        lengths, norms = [], []
        for doc_id, text in enumerate(texts):
            analyzed = analyzer.analyze(text)
            lengths.append(analyzed.length)
            norms.append(lnc_norm_by_definition(analyzed.frequencies.values()))
            for term, frequency in analyzed.frequencies.items():
                postings.setdefault(term, {})[doc_id] = frequency
        return cls(postings, lengths, norms)


def assert_matches_reference(index: InvertedIndex, reference: ReferenceIndex) -> None:
    assert index.document_count == len(reference.lengths)
    assert index.total_length == sum(reference.lengths)
    assert index.vocabulary_size == len(reference.postings)
    assert index.posting_count == sum(len(docs) for docs in reference.postings.values())
    assert [entry.term for entry in index.terms()] == sorted(reference.postings)
    for term, documents in reference.postings.items():
        postings = index.postings(term)
        assert list(postings.doc_ids) == sorted(documents)
        assert list(postings.term_frequencies) == [documents[doc] for doc in sorted(documents)]
        entry = index.lookup(term)
        assert entry is not None
        assert entry.document_frequency == len(documents)
        assert entry.total_frequency == sum(documents.values())
    for doc_id, document in enumerate(index.documents()):
        assert document.record_id == record_id_of(doc_id)
        assert document.length == reference.lengths[doc_id]
        assert document.norm == reference.norms[doc_id]
        assert not document.deleted


TOLERANCE = 1e-9
DEFAULT_K1 = 1.2
DEFAULT_B = 0.75


@dataclass(frozen=True)
class Oracle:
    """Las fórmulas de TF-IDF (lnc.ltc) y BM25 aplicadas directo a los textos, sin índice.

    `documents` son los vivos, por la dirección de su fila: N y avgdl salen de ellos. El df
    cuenta además `counted_deleted`, los borrados cuyos postings siguen en el índice hasta
    la próxima fusión, acotado a N como en el ranking.
    """

    documents: dict[RecordId, AnalyzedText]
    analyzer: Analyzer
    counted_deleted: tuple[AnalyzedText, ...] = ()

    @classmethod
    def of(
        cls, documents: dict[RecordId, str], analyzer: Analyzer, deleted: Iterable[str] = ()
    ) -> "Oracle":
        return cls(
            {record_id: analyzer.analyze(text) for record_id, text in documents.items()},
            analyzer,
            tuple(analyzer.analyze(text) for text in deleted),
        )

    @classmethod
    def of_texts(cls, texts: list[str], analyzer: Analyzer) -> "Oracle":
        return cls.of(dict(documents_of(texts)), analyzer)

    def scores(
        self, query: str, method: RankingMethod, k1: float = DEFAULT_K1, b: float = DEFAULT_B
    ) -> dict[RecordId, float]:
        query_frequencies = self.analyzer.analyze(query).frequencies
        present = [term for term in query_frequencies if self._document_frequency(term) > 0]
        matches = [
            record_id
            for record_id, document in self.documents.items()
            if any(term in document.frequencies for term in present)
        ]
        if not matches:
            return {}
        if method is RankingMethod.TF_IDF:
            return self._tf_idf(query_frequencies, present, matches)
        return self._bm25(present, matches, k1, b)

    def _tf_idf(self, query_frequencies, present, matches) -> dict[RecordId, float]:
        count = len(self.documents)
        query_weights = {
            term: (1 + math.log10(query_frequencies[term]))
            * math.log10(count / self._document_frequency(term))
            for term in present
        }
        query_norm = math.sqrt(sum(weight**2 for weight in query_weights.values()))
        scores = {}
        for record_id in matches:
            frequencies = self.documents[record_id].frequencies
            norm = math.sqrt(sum((1 + math.log10(tf)) ** 2 for tf in frequencies.values()))
            scores[record_id] = sum(
                (query_weights[term] / query_norm if query_norm else 0.0)
                * (1 + math.log10(frequencies[term]))
                / norm
                for term in present
                if term in frequencies
            )
        return scores

    def _bm25(self, present, matches, k1, b) -> dict[RecordId, float]:
        count = len(self.documents)
        average_length = sum(document.length for document in self.documents.values()) / count
        scores = {}
        for record_id in matches:
            document = self.documents[record_id]
            score = 0.0
            for term in present:
                tf = document.frequencies.get(term, 0)
                if tf == 0:
                    continue
                df = self._document_frequency(term)
                idf = math.log(1 + (count - df + 0.5) / (df + 0.5))
                score += (
                    idf
                    * tf
                    * (k1 + 1)
                    / (tf + k1 * (1 - b + b * document.length / average_length))
                )
            scores[record_id] = score
        return scores

    def _document_frequency(self, term: str) -> int:
        counted = itertools.chain(self.documents.values(), self.counted_deleted)
        found = sum(1 for document in counted if term in document.frequencies)
        return min(found, len(self.documents))


def expected_order(scores: dict[RecordId, float]) -> list[RecordId]:
    return sorted(scores, key=lambda record_id: (-scores[record_id], record_id))


def assert_same_ranking(
    actual: list[ScoredDocument], expected: dict[RecordId, float], limit: int | None = None
) -> None:
    """Mismos puntajes (con tolerancia) y mismo orden; dos documentos solo pueden cambiar
    de lugar respecto del oráculo si sus puntajes difieren menos que la tolerancia, que
    es lo que puede mover el redondeo de sumar en otro orden."""
    order = expected_order(expected)[:limit]
    assert len(actual) == len(order)
    for position, document in enumerate(actual):
        assert document.score == pytest.approx(expected[document.record_id], abs=TOLERANCE)
        expected_record = order[position]
        if document.record_id != expected_record:
            assert expected[document.record_id] == pytest.approx(
                expected[expected_record], abs=TOLERANCE
            )
    assert actual == sorted(actual, key=lambda document: (-document.score, document.record_id))
