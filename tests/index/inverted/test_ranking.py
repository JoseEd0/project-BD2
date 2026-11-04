"""El ranking TF-IDF + coseno y BM25, comparado con las fórmulas aplicadas directo a los textos."""

import itertools
import math
import random
from pathlib import Path

import pytest
from inverted_support import (
    DEFAULT_B,
    DEFAULT_K1,
    TOLERANCE,
    Oracle,
    assert_same_ranking,
    build,
    random_texts,
    record_id_of,
    vocabulary,
)

from config import EngineConfig
from index.inverted import (
    Bm25Parameters,
    InvertedIndex,
    Ranker,
    RankingMethod,
    RankingParameterError,
    RankingStats,
    ScoredDocument,
)
from storage.record_id import RecordId
from text import Analyzer

BM25_SETTINGS = [(DEFAULT_K1, DEFAULT_B), (0.0, 0.5), (2.0, 0.0), (0.5, 1.0)]
SEEDS = [1, 2, 3]
CORPUS_SIZE = 250
QUERIES_PER_CORPUS = 25
TOP_KS = [1, 3, 10]
TF_IDF = RankingMethod.TF_IDF
BM25 = RankingMethod.BM25


@pytest.fixture
def path(config: EngineConfig) -> Path:
    return config.data_directory / "productos.inv"


def random_query(generator: random.Random, words: list[str]) -> str:
    """De una a seis palabras del vocabulario, a veces repetidas, con alguna stopword o una
    palabra que no está en ningún documento."""
    query = generator.choices(words, k=generator.randrange(1, 7))
    if generator.random() < 0.3:
        query.append(generator.choice(query))
    if generator.random() < 0.2:
        query.append(generator.choice(["para", "de", "zzzzqx"]))
    return " ".join(query)


@pytest.mark.parametrize("seed", SEEDS)
def test_scores_and_top_k_match_the_formulas_on_random_corpora(
    seed: int, path: Path, config: EngineConfig
):
    texts = random_texts(CORPUS_SIZE, seed)
    analyzer = Analyzer(config)
    oracle = Oracle.of_texts(texts, analyzer)
    generator = random.Random(seed)
    words = vocabulary(400, seed)
    with build(path, texts, config) as index:
        ranker = Ranker(index, config)
        for _ in range(QUERIES_PER_CORPUS):
            query = random_query(generator, words)
            expected = oracle.scores(query, TF_IDF)
            assert_same_ranking(list(ranker.ranked(query, TF_IDF)), expected)
            for k in TOP_KS:
                assert_same_ranking(ranker.top_k(query, TF_IDF, k), expected, k)
            for k1, b in BM25_SETTINGS:
                parameters = Bm25Parameters(k1, b)
                expected = oracle.scores(query, BM25, k1, b)
                assert_same_ranking(list(ranker.ranked(query, BM25, parameters)), expected)
                for k in TOP_KS:
                    assert_same_ranking(ranker.top_k(query, BM25, k, parameters), expected, k)


@pytest.mark.parametrize("method", list(RankingMethod))
def test_the_lazy_order_is_the_full_top_k(method: RankingMethod, path: Path, config: EngineConfig):
    texts = random_texts(CORPUS_SIZE, seed=4)
    with build(path, texts, config) as index:
        ranker = Ranker(index, config)
        query = "teclado raton cable funda"
        ranked = list(ranker.ranked(query, method))
        assert ranked == ranker.top_k(query, method, len(texts))
        lazy = ranker.ranked(query, method)
        assert list(itertools.islice(lazy, 5)) == ranked[:5]
        assert list(lazy) == ranked[5:]


@pytest.mark.parametrize("method", list(RankingMethod))
@pytest.mark.parametrize("query", ["", "   ", "para de la", "¡¿...?!"])
def test_an_empty_or_stopword_query_matches_nothing(
    method: RankingMethod, query: str, path: Path, config: EngineConfig
):
    with build(path, ["teclado para la casa", "cable de red"], config) as index:
        ranker = Ranker(index, config)
        stats = RankingStats()
        assert ranker.top_k(query, method, 10, stats=stats) == []
        assert list(ranker.ranked(query, method)) == []
        assert stats == RankingStats()


@pytest.mark.parametrize("method", list(RankingMethod))
def test_an_unknown_term_adds_nothing(method: RankingMethod, path: Path, config: EngineConfig):
    texts = random_texts(100, seed=6)
    with build(path, texts, config) as index:
        ranker = Ranker(index, config)
        assert ranker.top_k("zzzzqx", method, 10) == []
        assert ranker.top_k("teclado zzzzqx", method, 10) == ranker.top_k("teclado", method, 10)


def test_a_repeated_query_term_weighs_more_in_tf_idf_only(path: Path, config: EngineConfig):
    texts = random_texts(150, seed=7)
    oracle = Oracle.of_texts(texts, Analyzer(config))
    with build(path, texts, config) as index:
        ranker = Ranker(index, config)
        once, twice = "teclado raton", "teclado teclado raton"
        assert ranker.top_k(once, TF_IDF, 20) != ranker.top_k(twice, TF_IDF, 20)
        assert_same_ranking(ranker.top_k(twice, TF_IDF, 20), oracle.scores(twice, TF_IDF), 20)
        assert ranker.top_k(once, BM25, 20) == ranker.top_k(twice, BM25, 20)


@pytest.mark.parametrize("method", list(RankingMethod))
def test_k_beyond_the_matches_returns_them_all(
    method: RankingMethod, path: Path, config: EngineConfig
):
    texts = ["teclado rojo", "teclado azul", "cable", "raton"]
    with build(path, texts, config) as index:
        ranker = Ranker(index, config)
        assert [document.doc_id for document in ranker.top_k("teclado", method, 100)] == [0, 1]


@pytest.mark.parametrize("method", list(RankingMethod))
def test_k_zero_is_empty_and_negative_k_is_an_error(
    method: RankingMethod, path: Path, config: EngineConfig
):
    with build(path, ["teclado rojo"], config) as index:
        ranker = Ranker(index, config)
        assert ranker.top_k("teclado", method, 0) == []
        with pytest.raises(RankingParameterError):
            ranker.top_k("teclado", method, -1)


def test_a_single_document_corpus(path: Path, config: EngineConfig):
    with build(path, ["teclado rojo"], config) as index:
        ranker = Ranker(index, config)
        assert ranker.top_k("teclado", TF_IDF, 10) == [ScoredDocument(0, record_id_of(0), 0.0)]
        expected_bm25 = math.log(1 + 0.5 / 1.5)
        [document] = ranker.top_k("teclado", BM25, 10)
        assert document.score == pytest.approx(expected_bm25, abs=TOLERANCE)


def test_a_term_in_every_document_scores_zero_in_tf_idf(path: Path, config: EngineConfig):
    texts = ["cable rojo", "cable azul teclado", "cable", "cable teclado teclado"]
    with build(path, texts, config) as index:
        ranker = Ranker(index, config)
        everywhere = ranker.top_k("cable", TF_IDF, 10)
        assert everywhere == [ScoredDocument(doc, record_id_of(doc), 0.0) for doc in range(4)]
        ranked = ranker.top_k("cable teclado", TF_IDF, 10)
        assert [document.doc_id for document in ranked] == [3, 1, 0, 2]
        assert [document.score > 0 for document in ranked] == [True, True, False, False]
        assert all(document.score > 0 for document in ranker.top_k("cable", BM25, 10))


@pytest.mark.parametrize("method", list(RankingMethod))
def test_equal_scores_are_ordered_by_row_address(
    method: RankingMethod, path: Path, config: EngineConfig
):
    """El empate lo decide la dirección de la fila, no el orden de llegada al índice."""
    texts = ["cable rojo", "teclado", "cable rojo", "raton", "cable rojo"]
    addresses = [RecordId(9, 0), RecordId(1, 0), RecordId(0, 5), RecordId(2, 0), RecordId(0, 7)]
    documents = list(zip(addresses, texts, strict=True))
    with InvertedIndex.build(path, documents, Analyzer(config), config) as index:
        ranked = Ranker(index, config).top_k("cable", method, 10)
        assert [document.record_id for document in ranked] == [
            RecordId(0, 5),
            RecordId(0, 7),
            RecordId(9, 0),
        ]
        assert [document.doc_id for document in ranked] == [2, 4, 0]
        assert ranked[0].score == ranked[1].score == ranked[2].score
        ranker = Ranker(index, config)
        assert ranker.top_k("cable", method, 2) == ranked[:2]
        assert ranker.top_k("cable", method, 1) == ranked[:1]
        assert list(ranker.ranked("cable", method)) == ranked


@pytest.mark.parametrize("method", list(RankingMethod))
def test_deleted_documents_are_left_out(method: RankingMethod, path: Path, config: EngineConfig):
    """Hasta la próxima fusión, el df cuenta a los borrados; N y avgdl, solo a los vivos."""
    texts = random_texts(200, seed=8)
    deleted = [0, 3, 50, 51, 199]
    live = {record_id_of(doc): text for doc, text in enumerate(texts) if doc not in deleted}
    oracle = Oracle.of(live, Analyzer(config), [texts[doc] for doc in deleted])
    with build(path, texts, config) as index:
        for doc in deleted:
            assert index.delete(record_id_of(doc))
    query = "teclado cable raton bateria"
    expected = oracle.scores(query, method)
    with InvertedIndex(path, config) as index:
        ranker = Ranker(index, config)
        stats = RankingStats()
        assert_same_ranking(list(ranker.ranked(query, method, stats=stats)), expected)
        assert_same_ranking(ranker.top_k(query, method, 10), expected, 10)
        assert stats.documents_scored == len(expected)


def test_stats_describe_the_work(path: Path, config: EngineConfig):
    texts = ["teclado rojo", "teclado azul", "cable rojo", "raton"]
    with build(path, texts, config) as index:
        stats = RankingStats()
        Ranker(index, config).top_k("Teclados ROJOS para zzzzqx teclado", BM25, 1, stats=stats)
        assert stats.document_frequencies == {"tecl": 2, "roj": 2, "zzzzqx": 0}
        assert list(stats.document_frequencies) == ["tecl", "roj", "zzzzqx"]
        assert stats.postings_read == 4
        assert stats.documents_scored == 3


def test_analyzing_a_query_gives_its_terms_and_their_df(path: Path, config: EngineConfig):
    texts = ["teclado rojo", "teclado azul", "cable rojo", "raton"]
    with build(path, texts, config) as index:
        analyzed = Ranker(index, config).analyzed("Teclados ROJOS para zzzzqx teclado")
        assert list(analyzed.items()) == [("tecl", 2), ("roj", 2), ("zzzzqx", 0)]


def test_the_query_is_analyzed_in_the_language_of_the_index(path: Path, config: EngineConfig):
    english = Analyzer(config, "en")
    texts = ["running shoes", "the database"]
    with InvertedIndex.build(path, enumerate_documents(texts), english, config) as index:
        ranked = Ranker(index, config).top_k("Running", BM25, 10)
        assert [document.doc_id for document in ranked] == [0]


def enumerate_documents(texts: list[str]):
    return ((record_id_of(position), text) for position, text in enumerate(texts))


@pytest.mark.parametrize("method", list(RankingMethod))
def test_an_empty_index_matches_nothing(method: RankingMethod, path: Path, config: EngineConfig):
    with build(path, [], config) as index:
        assert Ranker(index, config).top_k("teclado", method, 10) == []


def test_bm25_uses_the_configured_parameters_by_default(tmp_path: Path):
    tuned = EngineConfig(data_directory=tmp_path, bm25_k1=2.0, bm25_b=0.3)
    texts = random_texts(100, seed=9)
    with build(tmp_path / "productos.inv", texts, tuned) as index:
        ranker = Ranker(index, tuned)
        query = "teclado cable funda"
        explicit = ranker.top_k(query, BM25, 10, Bm25Parameters(2.0, 0.3))
        assert ranker.top_k(query, BM25, 10) == explicit
        assert ranker.top_k(query, BM25, 10, Bm25Parameters(DEFAULT_K1, DEFAULT_B)) != explicit


@pytest.mark.parametrize(
    ("k1", "b"),
    [(-0.1, 0.75), (math.inf, 0.75), (math.nan, 0.75), (1.2, -0.1), (1.2, 1.1), (1.2, math.nan)],
)
def test_out_of_range_bm25_parameters_are_rejected(k1: float, b: float):
    with pytest.raises(RankingParameterError):
        Bm25Parameters(k1, b)


def test_the_bm25_bounds_themselves_are_valid():
    Bm25Parameters(0.0, 0.0)
    Bm25Parameters(0.0, 1.0)


def test_tf_idf_takes_no_parameters(path: Path, config: EngineConfig):
    with build(path, ["teclado"], config) as index:
        ranker = Ranker(index, config)
        with pytest.raises(RankingParameterError):
            ranker.top_k("teclado", TF_IDF, 10, Bm25Parameters(DEFAULT_K1, DEFAULT_B))
        with pytest.raises(RankingParameterError):
            ranker.ranked("teclado", TF_IDF, Bm25Parameters(DEFAULT_K1, DEFAULT_B))
