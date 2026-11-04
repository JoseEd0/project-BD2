"""Construcción de índices de prueba y un índice de referencia en memoria."""

import random
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from config import EngineConfig
from index.inverted import InvertedIndex, lnc_norm
from index.inverted.format import DOCUMENT_RECORD, FIRST_DATA_PAGE, documents_path
from storage.page import RecordPage, slot_capacity
from storage.pager import Pager
from storage.record_id import RecordId
from text import Analyzer

# Palabras que el analizador deja casi intactas, con ñ, cifras y una stopword («para»).
WORDS = [
    "teclado", "raton", "pantalla", "cable", "cargador", "bateria", "ñandu", "año", "ano",
    "usb", "4k", "128gb", "para", "rojo", "azul", "funda", "auriculares", "camara", "lente",
]


def record_id_of(position: int) -> RecordId:
    return RecordId(page_id=position // 10, slot=position % 10)


def documents_of(texts: list[str]) -> Iterator[tuple[RecordId, str]]:
    for position, text in enumerate(texts):
        yield record_id_of(position), text


def build(path: Path, texts: list[str], config: EngineConfig) -> InvertedIndex:
    return InvertedIndex.build(path, documents_of(texts), Analyzer(config), config)


def mark_deleted(path: Path, config: EngineConfig, doc_ids: list[int]) -> None:
    """Marca documentos como borrados directamente en la tabla de documentos de un índice
    cerrado: el mantenimiento que lo hará por API todavía no existe."""
    capacity = slot_capacity(config.page_size, DOCUMENT_RECORD.size)
    with Pager(documents_path(path), config) as pager:
        for doc_id in doc_ids:
            page_index, slot = divmod(doc_id, capacity)
            page_id = FIRST_DATA_PAGE + page_index
            page = RecordPage.from_bytes(pager.read(page_id), DOCUMENT_RECORD.size)
            page.tombstone(slot)
            pager.write(page_id, page.to_bytes())


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
            norms.append(lnc_norm(analyzed.frequencies.values()))
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
