"""Las consultas booleanas sobre el índice: el resultado es el de evaluar la expresión
documento a documento, y el ranking es el del texto libre con los términos positivos."""

import dataclasses
import random
from collections.abc import Iterator
from pathlib import Path

import pytest
from inverted_support import TOLERANCE, build, random_texts, record_id_of

from config import EngineConfig
from index.inverted import InvertedIndex, Ranker, RankingMethod, RankingStats, ScoredDocument
from storage.record_id import RecordId
from text import Analyzer, BooleanQueryError

TF_IDF = RankingMethod.TF_IDF
BM25 = RankingMethod.BM25
METHODS = list(RankingMethod)
# Palabras frecuentes del vocabulario de `random_texts`, una stopword («para»), una palabra
# que no está en ningún documento y una que el analizador corta en dos términos.
OPERANDS = [
    "teclado", "raton", "pantalla", "cable", "cargador", "bateria", "usb", "año",
    "para", "zzzzqx", "usb-cable",
]
SEEDS = [3, 11, 23, 41]
BUILT, FLUSHED, PENDING = 70, 40, 30
DELETED_FRACTION = 0.2
EXPRESSIONS_PER_CORPUS = 120
MAX_DEPTH = 3
NESTING_PROBABILITY = 0.7
REDUNDANT_PARENTHESES_PROBABILITY = 0.15
IMPLICIT_OR_PROBABILITY = 0.4
VOCABULARY_SIZE = 20
TOP_K = 5
NEVER = {
    "inverted_max_segments": 1_000,
    "inverted_pending_fraction": 1.0,
    "inverted_deleted_fraction": 1.0,
}
# Cuánto liga cada nodo del árbol al escribirlo: un hijo que liga menos que su padre va
# entre paréntesis.
BINDING = {"or": 0, "and": 1, "not": 2, "word": 3}

Tree = tuple


@pytest.fixture
def path(config: EngineConfig) -> Path:
    return config.data_directory / "productos.inv"


@pytest.fixture
def quiet(config: EngineConfig) -> EngineConfig:
    """Una configuración que nunca fusiona sola: el índice conserva sus segmentos."""
    return dataclasses.replace(config, **NEVER)


def random_tree(generator: random.Random, depth: int) -> Tree:
    if depth == 0 or generator.random() > NESTING_PROBABILITY:
        return ("word", generator.choice(OPERANDS))
    kind = generator.choice(["and", "or", "not"])
    if kind == "not":
        return ("not", random_tree(generator, depth - 1))
    return (kind, [random_tree(generator, depth - 1) for _ in range(generator.randrange(2, 4))])


def written(tree: Tree, generator: random.Random) -> str:
    """El árbol como consulta, con los paréntesis que pide la precedencia, algunos de más
    y a veces el OR sin escribir."""
    kind = tree[0]
    if kind == "word":
        return tree[1]
    if kind == "not":
        return f"NOT {operand(tree[1], BINDING['not'], generator)}"
    if kind == "and":
        return " AND ".join(operand(child, BINDING["and"], generator) for child in tree[1])
    parts = [operand(child, BINDING["or"], generator) for child in tree[1]]
    text = parts[0]
    for part in parts[1:]:
        text += " " if generator.random() < IMPLICIT_OR_PROBABILITY else " OR "
        text += part
    return text


def operand(tree: Tree, parent_binding: int, generator: random.Random) -> str:
    text = written(tree, generator)
    if BINDING[tree[0]] < parent_binding or generator.random() < REDUNDANT_PARENTHESES_PROBABILITY:
        return f"({text})"
    return text


def holds(tree: Tree, terms: set[str], analyzer: Analyzer) -> bool | None:
    """La expresión sobre los términos de un documento; `None` si no le queda ningún
    operando, que se quita de su padre como una stopword."""
    kind = tree[0]
    if kind == "word":
        analyzed = analyzer.terms(tree[1])
        return all(term in terms for term in analyzed) if analyzed else None
    if kind == "not":
        inner = holds(tree[1], terms, analyzer)
        return None if inner is None else not inner
    values = [value for child in tree[1] if (value := holds(child, terms, analyzer)) is not None]
    if not values:
        return None
    return all(values) if kind == "and" else any(values)


def positive_words(tree: Tree, negations: int = 0) -> Iterator[str]:
    kind = tree[0]
    if kind == "word":
        if negations % 2 == 0:
            yield tree[1]
    elif kind == "not":
        yield from positive_words(tree[1], negations + 1)
    else:
        for child in tree[1]:
            yield from positive_words(child, negations)


@dataclasses.dataclass
class Corpus:
    """Un índice con un segmento construido, otro volcado, documentos en memoria y
    borrados de los tres, junto a los términos de cada documento vivo."""

    index: InvertedIndex
    live_terms: dict[RecordId, set[str]]


def corpus(path: Path, config: EngineConfig, seed: int) -> Corpus:
    analyzer = Analyzer(config)
    texts = random_texts(BUILT + FLUSHED + PENDING, seed, VOCABULARY_SIZE)
    index = build(path, texts[:BUILT], config)
    for position in range(BUILT, BUILT + FLUSHED):
        index.insert(record_id_of(position), texts[position])
    index.flush()
    for position in range(BUILT + FLUSHED, len(texts)):
        index.insert(record_id_of(position), texts[position])
    generator = random.Random(seed)
    deleted = generator.sample(range(len(texts)), int(len(texts) * DELETED_FRACTION))
    for position in deleted:
        index.delete(record_id_of(position))
    assert index.segment_count == 2 and index.pending_document_count == PENDING
    live_terms = {
        record_id_of(position): set(analyzer.terms(text))
        for position, text in enumerate(texts)
        if position not in deleted
    }
    return Corpus(index, live_terms)


def record_ids(documents: Iterator[ScoredDocument] | list[ScoredDocument]) -> list[RecordId]:
    return [document.record_id for document in documents]


@pytest.mark.parametrize("seed", SEEDS)
def test_the_matches_are_the_expression_evaluated_on_each_document(
    path: Path, quiet: EngineConfig, seed: int
):
    analyzer = Analyzer(quiet)
    generator = random.Random(seed)
    found = corpus(path, quiet, seed)
    ranker = Ranker(found.index, quiet)
    for _ in range(EXPRESSIONS_PER_CORPUS):
        tree = random_tree(generator, MAX_DEPTH)
        query = written(tree, generator)
        if holds(tree, set(), analyzer):
            with pytest.raises(BooleanQueryError, match="solo excluye documentos"):
                ranker.ranked(query, BM25)
            continue
        expected = {
            record_id
            for record_id, terms in found.live_terms.items()
            if holds(tree, terms, analyzer)
        }
        for method in METHODS:
            assert set(record_ids(ranker.ranked(f"({query})", method))) == expected, query
    found.index.close()


@pytest.mark.parametrize("seed", SEEDS)
def test_the_scores_are_those_of_the_positive_terms_as_free_text(
    path: Path, quiet: EngineConfig, seed: int
):
    analyzer = Analyzer(quiet)
    generator = random.Random(seed)
    found = corpus(path, quiet, seed)
    ranker = Ranker(found.index, quiet)
    for _ in range(EXPRESSIONS_PER_CORPUS):
        tree = random_tree(generator, MAX_DEPTH)
        if holds(tree, set(), analyzer):
            continue
        query = f"({written(tree, generator)})"
        free_text = " ".join(positive_words(tree))
        for method in METHODS:
            plain = {
                document.record_id: document.score for document in ranker.ranked(free_text, method)
            }
            boolean = list(ranker.ranked(query, method))
            for document in boolean:
                assert document.score == pytest.approx(plain[document.record_id], abs=TOLERANCE)
            assert [document.score for document in boolean] == sorted(
                (document.score for document in boolean), reverse=True
            )
    found.index.close()


@pytest.mark.parametrize("seed", SEEDS)
def test_the_pruned_top_k_is_the_head_of_the_lazy_order(
    path: Path, quiet: EngineConfig, seed: int
):
    analyzer = Analyzer(quiet)
    generator = random.Random(seed)
    found = corpus(path, quiet, seed)
    ranker = Ranker(found.index, quiet)
    for _ in range(EXPRESSIONS_PER_CORPUS):
        tree = random_tree(generator, MAX_DEPTH)
        if holds(tree, set(), analyzer):
            continue
        query = f"({written(tree, generator)})"
        for method in METHODS:
            lazy = list(ranker.ranked(query, method))[:TOP_K]
            assert ranker.top_k(query, method, TOP_K) == lazy
    found.index.close()


TEXTS = [
    "funda de silicona para iphone",
    "funda de cuero para iphone",
    "funda para samsung",
    "cable usb para iphone",
    "funda iphone iphone",
    "protector de pantalla",
]


POSITIONS = {record_id_of(position): position for position in range(len(TEXTS) + 1)}


def matched_positions(documents: list[ScoredDocument]) -> list[int]:
    return sorted(POSITIONS[document.record_id] for document in documents)


@pytest.mark.parametrize("method", METHODS)
def test_and_or_and_not_on_a_small_catalog(
    method: RankingMethod, path: Path, config: EngineConfig
):
    with build(path, TEXTS, config) as index:
        ranker = Ranker(index, config)

        def positions(query: str) -> list[int]:
            return matched_positions(list(ranker.ranked(query, method)))

        assert positions("funda AND iphone") == [0, 1, 4]
        assert positions("funda AND iphone AND NOT silicona") == [1, 4]
        assert positions("funda AND NOT (silicona OR cuero)") == [2, 4]
        assert positions("samsung OR cable") == [2, 3]
        assert positions("iphone AND NOT funda") == [3]
        assert positions("pantalla AND iphone") == []
        assert positions("teclado AND NOT funda") == []


def test_a_document_deleted_from_the_in_memory_block_is_left_out(
    path: Path, config: EngineConfig
):
    with build(path, TEXTS, config) as index:
        index.insert(record_id_of(len(TEXTS)), "funda de tela para iphone")
        index.delete(record_id_of(1))
        index.delete(record_id_of(len(TEXTS)))
        ranker = Ranker(index, config)
        assert matched_positions(ranker.top_k("funda AND iphone", BM25, 10)) == [0, 4]


@pytest.mark.parametrize("method", METHODS)
def test_an_or_only_boolean_query_ranks_exactly_like_free_text(
    method: RankingMethod, path: Path, config: EngineConfig
):
    with build(path, TEXTS, config) as index:
        ranker = Ranker(index, config)
        assert list(ranker.ranked("(funda) OR iphone cable", method)) == list(
            ranker.ranked("funda iphone cable", method)
        )


def test_negated_terms_neither_score_nor_are_reported(path: Path, config: EngineConfig):
    with build(path, TEXTS, config) as index:
        ranker = Ranker(index, config)
        query = "funda AND NOT silicona"
        assert ranker.analyzed(query) == {"fund": 4}
        scores = {document.record_id: document.score for document in ranker.ranked(query, BM25)}
        plain = {document.record_id: document.score for document in ranker.ranked("funda", BM25)}
        assert scores == {record_id: plain[record_id] for record_id in scores}


def test_each_list_is_read_once_and_only_candidates_are_scored(path: Path, config: EngineConfig):
    with build(path, TEXTS, config) as index:
        stats = RankingStats()
        Ranker(index, config).top_k("funda AND iphone AND NOT silicona", BM25, 10, stats=stats)
        lists = [len(index.postings(term)) for term in ["fund", "iphon", "silicon"]]
        assert stats.postings_read == sum(lists)
        assert stats.documents_scored == 2
        assert stats.document_frequencies == {"fund": 4, "iphon": 4}


def test_the_expression_is_described_with_analyzed_terms(path: Path, config: EngineConfig):
    with build(path, TEXTS, config) as index:
        ranker = Ranker(index, config)
        assert ranker.expression("funda AND iphone AND NOT silicona") == (
            "fund AND iphon AND NOT silicon"
        )
        assert ranker.expression("el AND de") == ""
        assert ranker.expression("funda iphone") is None


@pytest.mark.parametrize("method", METHODS)
def test_a_boolean_query_of_stopwords_matches_nothing(
    method: RankingMethod, path: Path, config: EngineConfig
):
    with build(path, TEXTS, config) as index:
        stats = RankingStats()
        assert list(Ranker(index, config).ranked("el AND (de OR la)", method, stats=stats)) == []
        assert stats.postings_read == 0


def test_a_purely_negative_query_is_rejected(path: Path, config: EngineConfig):
    with build(path, TEXTS, config) as index:
        ranker = Ranker(index, config)
        with pytest.raises(BooleanQueryError, match="solo excluye documentos"):
            ranker.top_k("NOT funda", BM25, 10)
        with pytest.raises(BooleanQueryError, match="solo excluye documentos"):
            ranker.analyzed("NOT funda")


def test_an_empty_index_matches_nothing(path: Path, config: EngineConfig):
    with build(path, [], config) as index:
        assert Ranker(index, config).top_k("funda AND NOT iphone", BM25, 10) == []
