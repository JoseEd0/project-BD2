"""El índice invertido después de construirse: inserciones, borrados, segmentos y fusiones."""

import dataclasses
import random
from pathlib import Path

import pytest
from inverted_support import (
    Oracle,
    assert_same_ranking,
    build,
    random_texts,
    record_id_of,
    vocabulary,
)

from config import EngineConfig
from index.inverted import (
    DocumentAlreadyIndexedError,
    InvertedIndex,
    Ranker,
    RankingMethod,
    ScoredDocument,
)
from index.inverted.format import build_directory
from storage.record_id import RecordId
from text import Analyzer

TF_IDF = RankingMethod.TF_IDF
BM25 = RankingMethod.BM25
METHODS = list(RankingMethod)
QUERIES = ["teclado", "cable usb", "funda roja para teclado", "bateria cargador camara lente"]
# Ningún umbral se alcanza: los tests que miran segmentos pendientes los controlan a mano.
NEVER = {
    "inverted_max_segments": 1_000,
    "inverted_pending_fraction": 1.0,
    "inverted_deleted_fraction": 1.0,
}
SEEDS = [5, 17, 29]
MODEL_STEPS = 260
MODEL_CORPUS = 80


@pytest.fixture
def path(config: EngineConfig) -> Path:
    return config.data_directory / "productos.inv"


@pytest.fixture
def quiet(config: EngineConfig) -> EngineConfig:
    """Una configuración que nunca fusiona sola."""
    return dataclasses.replace(config, **NEVER)


def ranking(index: InvertedIndex, config: EngineConfig, query: str, method: RankingMethod):
    return Ranker(index, config).top_k(query, method, index.document_count + index.deleted_count)


def by_address(documents: list[ScoredDocument]) -> list[tuple[RecordId, float]]:
    return [(document.record_id, document.score) for document in documents]


def assert_same_as_rebuild(
    index: InvertedIndex, live: dict[RecordId, str], config: EngineConfig, path: Path
) -> None:
    """El índice da exactamente los mismos documentos, puntajes y orden que uno construido
    de cero con los documentos vivos, en un orden cualquiera."""
    documents = sorted(live.items())
    random.Random(len(documents)).shuffle(documents)
    with InvertedIndex.build(path, documents, Analyzer(config), config) as rebuilt:
        assert index.document_count == rebuilt.document_count
        assert index.total_length == rebuilt.total_length
        assert index.vocabulary_size == rebuilt.vocabulary_size
        assert index.posting_count == rebuilt.posting_count
        for entry in rebuilt.terms():
            assert index.document_frequency(entry.term) == entry.document_frequency
        for query in QUERIES:
            for method in METHODS:
                assert by_address(ranking(index, config, query, method)) == by_address(
                    ranking(rebuilt, config, query, method)
                )


def assert_matches_oracle(index: InvertedIndex, config: EngineConfig, oracle: Oracle) -> None:
    for query in QUERIES:
        for method in METHODS:
            assert_same_ranking(ranking(index, config, query, method), oracle.scores(query, method))


def test_an_inserted_document_is_found_before_and_after_the_flush(
    path: Path, quiet: EngineConfig
):
    texts = random_texts(60, seed=1)
    new = RecordId(500, 0)
    with build(path, texts, quiet) as index:
        assert index.insert(new, "teclado gamer teclado mecánico") == len(texts)
        assert index.pending_document_count == 1
        live = dict(zip(map(record_id_of, range(len(texts))), texts, strict=True))
        live[new] = "teclado gamer teclado mecánico"
        oracle = Oracle.of(live, Analyzer(quiet))
        assert_matches_oracle(index, quiet, oracle)
        assert new in [document.record_id for document in ranking(index, quiet, "gamer", BM25)]
        index.flush()
        assert index.pending_document_count == 0
        assert index.segment_count == 2
        assert_matches_oracle(index, quiet, oracle)


def test_a_deleted_document_is_no_longer_found(path: Path, quiet: EngineConfig):
    with build(path, ["cable usb", "cable hdmi", "teclado"], quiet) as index:
        assert index.delete(record_id_of(1))
        found = [document.record_id for document in ranking(index, quiet, "cable", TF_IDF)]
        assert found == [record_id_of(0)]
        assert not index.delete(record_id_of(1))
        assert not index.delete(RecordId(99, 9))


def test_an_address_that_already_has_a_document_is_refused(path: Path, quiet: EngineConfig):
    with build(path, ["cable usb"], quiet) as index:
        with pytest.raises(DocumentAlreadyIndexedError):
            index.insert(record_id_of(0), "teclado")
        assert index.document_count == 1
        index.delete(record_id_of(0))
        assert index.insert(record_id_of(0), "teclado") == 1


def test_an_update_is_a_delete_and_an_insert(path: Path, quiet: EngineConfig):
    with build(path, ["cable usb", "funda roja", "teclado"], quiet) as index:
        index.delete(record_id_of(1))
        assert index.insert(record_id_of(1), "funda azul") == 3
        assert [d.record_id for d in ranking(index, quiet, "azul", BM25)] == [record_id_of(1)]
        assert ranking(index, quiet, "roja", BM25) == []
        assert index.document(1).deleted
        assert index.document(3).record_id == record_id_of(1)


def test_tombstones_count_in_df_until_the_merge(path: Path, quiet: EngineConfig):
    """N y la longitud total cambian al borrar; el df y el vocabulario, al fusionar."""
    with build(path, ["cable rojo", "cable azul usb", "teclado"], quiet) as index:
        index.delete(record_id_of(1))
        assert index.document_count == 2
        assert index.deleted_count == 1
        assert index.total_length == 3
        assert index.average_length == 1.5
        assert index.document_frequency("cabl") == 2
        assert index.vocabulary_size == 5
        assert index.posting_count == 6
        index.merge()
        assert index.document_count == 2
        assert index.total_length == 3
        assert index.document_frequency("cabl") == 1
        assert index.document_frequency("azul") == 0
        assert index.vocabulary_size == 3
        assert index.posting_count == 3
        assert index.merge_count == 1
        assert index.describe()["deleted_with_postings"] == 0


def test_df_is_capped_at_n_so_tf_idf_never_goes_negative(path: Path, quiet: EngineConfig):
    texts = ["cable"] * 8 + ["cable teclado", "teclado"]
    with build(path, texts, quiet) as index:
        for position in range(8):
            index.delete(record_id_of(position))
        assert index.document_frequency("cabl") == 9
        assert index.document_count == 2
        scored = ranking(index, quiet, "cable teclado", TF_IDF)
        assert [document.record_id for document in scored] == [record_id_of(8), record_id_of(9)]
        assert all(document.score >= 0 for document in scored)
        live = {record_id_of(8): texts[8], record_id_of(9): texts[9]}
        oracle = Oracle.of(live, Analyzer(quiet), texts[:8])
        assert_matches_oracle(index, quiet, oracle)


@pytest.mark.parametrize("method", METHODS)
def test_an_index_whose_documents_are_all_deleted_matches_nothing(
    method: RankingMethod, path: Path, quiet: EngineConfig
):
    with build(path, ["cable rojo", "cable"], quiet) as index:
        index.delete(record_id_of(0))
        index.delete(record_id_of(1))
        assert index.document_count == 0
        assert ranking(index, quiet, "cable", method) == []
        index.insert(RecordId(7, 0), "")
        assert ranking(index, quiet, "cable", method) == []


def test_reopening_keeps_pending_segments_and_tombstones(path: Path, quiet: EngineConfig):
    texts = random_texts(80, seed=2)
    with build(path, texts, quiet) as index:
        for number in range(3):
            index.insert(RecordId(900, number), f"teclado numero {number} cable")
            index.flush()
        index.delete(record_id_of(4))
        expected = {method: by_address(ranking(index, quiet, "teclado cable", method)) for method in METHODS}
        description = index.describe()
    assert description["segments"][0]["generation"] == 0
    assert len(description["segments"]) == 4
    with InvertedIndex(path, quiet) as reopened:
        assert reopened.segment_count == 4
        assert reopened.deleted_count == 1
        assert reopened.describe() == description
        for method in METHODS:
            assert by_address(ranking(reopened, quiet, "teclado cable", method)) == expected[method]


def test_close_writes_the_block_that_was_never_flushed(path: Path, quiet: EngineConfig):
    with build(path, random_texts(30, seed=11), quiet) as index:
        index.insert(RecordId(3, 3), "xilofono rojo")
        assert index.segment_count == 1
    with InvertedIndex(path, quiet) as reopened:
        assert reopened.segment_count == 2
        assert reopened.pending_document_count == 0
        found = [document.record_id for document in ranking(reopened, quiet, "xilofono", BM25)]
        assert found == [RecordId(3, 3)]


def test_documents_without_terms_write_no_segment(path: Path, quiet: EngineConfig):
    with build(path, ["cable"], quiet) as index:
        index.insert(RecordId(3, 3), "para de la")
        assert index.pending_document_count == 1
        index.flush()
        assert index.segment_count == 1
        assert index.pending_document_count == 0
        assert index.document_count == 2


def test_a_block_over_budget_becomes_a_segment_without_merging(path: Path, config: EngineConfig):
    tight = dataclasses.replace(config, spimi_block_bytes=1, inverted_max_segments=1)
    with build(path, ["cable"], tight) as index:
        for number in range(4):
            index.insert(RecordId(50, number), f"teclado {number}")
        assert index.segment_count == 5
        assert index.pending_document_count == 0
        assert index.merge_count == 0
        index.flush()
        assert index.segment_count == 1
        assert index.merge_count == 1


def test_too_many_pending_segments_are_compacted(path: Path, config: EngineConfig):
    policy = dataclasses.replace(config, **{**NEVER, "inverted_max_segments": 2})
    texts = random_texts(100, seed=3)
    with build(path, texts, policy) as index:
        live = dict(zip(map(record_id_of, range(len(texts))), texts, strict=True))
        index.delete(record_id_of(0))
        deleted = [live.pop(record_id_of(0))]
        for number in range(3):
            live[RecordId(700, number)] = f"cable funda {number}"
            index.insert(RecordId(700, number), live[RecordId(700, number)])
            index.flush()
        assert index.segment_count == 2
        assert index.describe()["segments"][1]["blocks"] == 3
        assert index.merge_count == 0
        assert index.deleted_count == 1
        assert_matches_oracle(index, policy, Oracle.of(live, Analyzer(policy), deleted))


def test_new_postings_over_their_fraction_trigger_a_full_merge(
    path: Path, config: EngineConfig
):
    policy = dataclasses.replace(config, **{**NEVER, "inverted_pending_fraction": 0.2})
    texts = random_texts(50, seed=4)
    with build(path, texts, policy) as index:
        main_postings = index.posting_count
        number = 0
        while index.merge_count == 0:
            pending = index.posting_count - main_postings
            index.insert(RecordId(800, number), texts[number])
            index.flush()
            number += 1
            assert pending < 0.2 * main_postings or index.merge_count == 1
        assert index.segment_count == 1


def test_deletions_over_their_fraction_trigger_a_full_merge(path: Path, config: EngineConfig):
    policy = dataclasses.replace(config, **{**NEVER, "inverted_deleted_fraction": 0.25})
    texts = random_texts(40, seed=6)
    with build(path, texts, policy) as index:
        for position in range(9):
            index.delete(record_id_of(position))
            index.flush()
        assert index.merge_count == 0
        index.delete(record_id_of(9))
        index.flush()
        assert index.merge_count == 1
        assert index.describe()["deleted_with_postings"] == 0
        live = {record_id_of(doc): text for doc, text in enumerate(texts) if doc >= 10}
        assert_same_as_rebuild(index, live, policy, policy.data_directory / "otro.inv")


def test_a_merge_gives_the_same_index_as_a_rebuild(path: Path, quiet: EngineConfig):
    texts = random_texts(120, seed=7)
    live = dict(zip(map(record_id_of, range(len(texts))), texts, strict=True))
    extra = random_texts(40, seed=8)
    with build(path, texts, quiet) as index:
        for number, text in enumerate(extra):
            address = RecordId(1000 + number // 10, number % 10)
            index.insert(address, text)
            live[address] = text
            if number % 7 == 0:
                index.flush()
        for position in range(0, 120, 3):
            index.delete(record_id_of(position))
            del live[record_id_of(position)]
        index.merge()
        assert index.segment_count == 1
        assert index.deleted_count == 40
        assert_same_as_rebuild(index, live, quiet, quiet.data_directory / "otro.inv")
        assert_matches_oracle(index, quiet, Oracle.of(live, Analyzer(quiet)))


def test_merging_more_segments_than_the_fan_in_takes_passes(path: Path, config: EngineConfig):
    narrow = dataclasses.replace(config, **NEVER, merge_fan_in=2)
    texts = random_texts(60, seed=9)
    live = dict(zip(map(record_id_of, range(len(texts))), texts, strict=True))
    with build(path, texts, narrow) as index:
        for number in range(6):
            live[RecordId(600, number)] = f"cable teclado {number}"
            index.insert(RecordId(600, number), live[RecordId(600, number)])
            index.flush()
        index.delete(record_id_of(1))
        del live[record_id_of(1)]
        index.merge()
        assert index.merge_passes >= 2
        assert index.block_count == 7
        assert not build_directory(path).exists()
        assert_same_as_rebuild(index, live, narrow, narrow.data_directory / "otro.inv")


def test_replaced_segments_are_removed_and_files_of_lists_the_live_ones(
    path: Path, quiet: EngineConfig
):
    with build(path, random_texts(30, seed=10), quiet) as index:
        for number in range(3):
            index.insert(RecordId(400, number), "cable")
            index.flush()
        assert len(InvertedIndex.files_of(path)) == 4 + 3 * 4
        index.merge()
        files = InvertedIndex.files_of(path)
        assert [file.name for file in files][4:] == [
            "productos.inv.seg4.lexicon",
            "productos.inv.seg4.terms",
            "productos.inv.seg4.postings",
        ]
    assert sorted(path.parent.iterdir()) == sorted(files)


def test_describe_reports_segments_tombstones_and_the_pending_block(
    path: Path, quiet: EngineConfig
):
    with build(path, ["cable rojo", "cable azul", "teclado"], quiet) as index:
        index.insert(RecordId(9, 0), "funda")
        index.flush()
        index.insert(RecordId(9, 1), "cable funda")
        index.delete(record_id_of(0))
        description = index.describe()
    assert description["documents"] == 4
    assert description["deleted"] == 1
    assert description["deleted_with_postings"] == 1
    assert [segment["postings"] for segment in description["segments"]] == [5, 1]
    assert description["pending"] == {
        "documents": 1,
        "terms": 2,
        "postings": 2,
        "estimated_bytes": description["pending"]["estimated_bytes"],
    }
    assert description["postings"] == 8
    assert description["merges"] == 0
    assert description["top_terms"][0] == {
        "term": "cabl",
        "document_frequency": 3,
        "total_frequency": 3,
    }


def test_terms_merge_every_segment_in_order(path: Path, quiet: EngineConfig):
    with build(path, ["cable rojo", "teclado"], quiet) as index:
        index.insert(RecordId(5, 0), "cable azul")
        index.flush()
        index.insert(RecordId(5, 1), "año cable")
        terms = list(index.terms())
        assert [entry.term for entry in terms] == ["azul", "año", "cabl", "roj", "tecl"]
        cable = terms[2]
        assert (cable.document_frequency, len(cable.locations)) == (3, 3)
        assert index.lookup("cabl") == cable
        assert list(index.postings("cabl").doc_ids) == [0, 2, 3]


@pytest.mark.parametrize("seed", SEEDS)
def test_random_operations_match_a_model(seed: int, tmp_path: Path):
    """Inserciones, borrados, actualizaciones, `flush` y reaperturas al azar contra un
    modelo de los documentos vivos. Antes de cada fusión, el oráculo cuenta en el df a los
    borrados cuyos postings siguen ahí; tras la fusión final, el índice es el de
    reconstruir de cero."""
    config = EngineConfig(
        page_size=256,
        buffer_pool_pages=4,
        data_directory=tmp_path,
        spimi_block_bytes=2_000,
        merge_fan_in=2,
        inverted_max_segments=2,
        inverted_pending_fraction=0.5,
        inverted_deleted_fraction=0.4,
    )
    generator = random.Random(seed)
    analyzer = Analyzer(config)
    pool = random_texts(400, seed)
    words = vocabulary(400, seed)
    path = tmp_path / "productos.inv"
    texts = pool[:MODEL_CORPUS]
    live = dict(zip(map(record_id_of, range(len(texts))), texts, strict=True))
    unpurged: list[str] = []
    free: list[RecordId] = []
    next_page = MODEL_CORPUS
    index = build(path, texts, config)
    try:
        for _ in range(MODEL_STEPS):
            merges = index.merge_count
            action = generator.choices(
                ["insert", "delete", "update", "flush", "reopen", "check"], [8, 5, 3, 2, 1, 2]
            )[0]
            if action == "insert" or (action in ("delete", "update") and not live):
                if free and generator.random() < 0.5:
                    address = free.pop(generator.randrange(len(free)))
                else:
                    address, next_page = RecordId(next_page, 0), next_page + 1
                live[address] = generator.choice(pool)
                index.insert(address, live[address])
            elif action == "delete":
                address = generator.choice(sorted(live))
                assert index.delete(address)
                unpurged.append(live.pop(address))
                free.append(address)
            elif action == "update":
                address = generator.choice(sorted(live))
                assert index.delete(address)
                unpurged.append(live[address])
                live[address] = generator.choice(pool)
                index.insert(address, live[address])
            elif action == "flush":
                index.flush()
            elif action == "reopen":
                index.close()
                index = InvertedIndex(path, config)
            if index.merge_count > merges:
                unpurged = []
            if action == "check":
                query = " ".join(generator.choices(words, k=generator.randrange(1, 5)))
                oracle = Oracle.of(live, analyzer, unpurged)
                for method in METHODS:
                    assert_same_ranking(ranking(index, config, query, method), oracle.scores(query, method))
            assert index.document_count == len(live)
        index.merge()
        assert_same_as_rebuild(index, live, config, tmp_path / "otro.inv")
        assert_matches_oracle(index, config, Oracle.of(live, analyzer))
    finally:
        index.close()
