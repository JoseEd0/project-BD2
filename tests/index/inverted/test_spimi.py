"""SPIMI: bloques con memoria acotada y su mezcla k-vías."""

import dataclasses
import random
import tracemalloc
from array import array
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from inverted_support import (
    ReferenceIndex,
    assert_matches_reference,
    build,
    documents_of,
    random_texts,
    vocabulary,
)

from config import EngineConfig
from index.inverted import InvertedIndex
from index.inverted.blocks import Block, MergePasses, merge_sources
from index.inverted.format import (
    FIRST_GENERATION,
    POSTING_FIELDS,
    POSTING_TYPECODE,
    build_directory,
    decode_postings,
    encode_postings,
    lexicon_path,
    segment_base,
)
from index.inverted.spimi import POSTING_ENTRY_BYTES, TERM_ENTRY_BYTES, SpimiBlock
from storage.record_id import RecordId
from text import Analyzer

SEEDS = [11, 23, 47]
# Cabe un puñado de documentos por bloque: con 200 textos salen decenas de bloques.
TINY_BLOCK_BYTES = 4 * TERM_ENTRY_BYTES
SMALLEST_FAN_IN = 2
MEMORY_BLOCK_BYTES = 256 * 1024
FEW_DOCUMENTS = 1_000
MANY_DOCUMENTS = 4 * FEW_DOCUMENTS
MEMORY_VOCABULARY = 300
WORDS_PER_DOCUMENT = 30
# Si la memoria creciera con N, cuadruplicar los documentos la multiplicaría casi por
# cuatro; el margen solo absorbe el ruido del intérprete.
MEMORY_GROWTH_TOLERANCE = 1.25
SHARED_TERM = b"cas"


def with_blocks(config: EngineConfig, block_bytes: int, fan_in: int) -> EngineConfig:
    return dataclasses.replace(config, spimi_block_bytes=block_bytes, merge_fan_in=fan_in)


def main_lexicon(path: Path) -> Path:
    return lexicon_path(segment_base(path, FIRST_GENERATION))


def file_bytes_but_the_lexicon(path: Path) -> list[bytes]:
    files = InvertedIndex.files_of(path)
    return [file.read_bytes() for file in files if file != main_lexicon(path)]


def lexicon_without_header(path: Path, config: EngineConfig) -> bytes:
    """El léxico sin la página 0, que además anota cuántos bloques y pasadas hubo."""
    return main_lexicon(path).read_bytes()[config.page_size :]


def test_the_block_estimate_counts_terms_and_postings():
    block = SpimiBlock(max_term_bytes=40)
    block.add(0, {"cas": 2, "ñu": 1})
    assert block.estimated_bytes == 2 * TERM_ENTRY_BYTES + len("cas") + len("ñu") + 2 * POSTING_ENTRY_BYTES
    block.add(1, {"cas": 1})
    assert block.estimated_bytes == 2 * TERM_ENTRY_BYTES + 5 + 3 * POSTING_ENTRY_BYTES


def test_many_blocks_and_passes_give_the_same_index_as_one_block(config: EngineConfig):
    texts = random_texts(200, seed=SEEDS[0])
    single_path = config.data_directory / "uno.inv"
    blocks_path = config.data_directory / "muchos.inv"
    single_config = with_blocks(config, 1 << 30, config.merge_fan_in)
    blocks_config = with_blocks(config, TINY_BLOCK_BYTES, SMALLEST_FAN_IN)
    with (
        build(single_path, texts, single_config) as single,
        build(blocks_path, texts, blocks_config) as blocks,
    ):
        assert single.block_count == 1
        assert single.merge_passes == 0
        assert blocks.block_count >= 3
        assert blocks.merge_passes >= 2
        assert list(blocks.terms()) == list(single.terms())
        assert list(blocks.documents()) == list(single.documents())
        for entry in single.terms():
            assert blocks.postings(entry.term) == single.postings(entry.term)
        for statistic in ("document_count", "total_length", "vocabulary_size", "posting_count"):
            assert getattr(blocks, statistic) == getattr(single, statistic)
    assert file_bytes_but_the_lexicon(blocks_path) == file_bytes_but_the_lexicon(single_path)
    assert lexicon_without_header(blocks_path, config) == lexicon_without_header(
        single_path, config
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_random_corpora_match_a_reference_index(config: EngineConfig, seed: int):
    generator = random.Random(seed)
    texts = random_texts(generator.randrange(50, 300), seed)
    block_bytes = generator.choice([TINY_BLOCK_BYTES, 8 * TINY_BLOCK_BYTES, 1 << 30])
    fan_in = generator.choice([SMALLEST_FAN_IN, 3, 8])
    blocks_config = with_blocks(config, block_bytes, fan_in)
    path = config.data_directory / f"azar{seed}.inv"
    with build(path, texts, blocks_config) as index:
        assert_matches_reference(index, ReferenceIndex.of(texts, Analyzer(config)))


def test_the_temporary_blocks_are_removed(config: EngineConfig):
    path = config.data_directory / "temporal.inv"
    build(path, random_texts(100, seed=SEEDS[1]), with_blocks(config, TINY_BLOCK_BYTES, 2)).close()
    assert not build_directory(path).exists()


class InterruptedCorpusError(Exception):
    pass


def failing_documents(texts: list[str], fail_at: int) -> Iterator[tuple[RecordId, str]]:
    for position, document in enumerate(documents_of(texts)):
        if position == fail_at:
            raise InterruptedCorpusError
        yield document


def test_a_failed_build_leaves_no_files(config: EngineConfig):
    path = config.data_directory / "fallido.inv"
    texts = random_texts(100, seed=SEEDS[2])
    tiny = with_blocks(config, TINY_BLOCK_BYTES, SMALLEST_FAN_IN)
    with pytest.raises(InterruptedCorpusError):
        InvertedIndex.build(path, failing_documents(texts, 80), Analyzer(tiny), tiny)
    assert list(config.data_directory.iterdir()) == []
    build(path, texts, tiny).close()


def peak_memory_of_build(path: Path, count: int, config: EngineConfig) -> int:
    analyzer = Analyzer(config)
    words = vocabulary(MEMORY_VOCABULARY, seed=SEEDS[0])

    def documents() -> Iterator[tuple[RecordId, str]]:
        generator = random.Random(SEEDS[0])
        for position in range(count):
            yield RecordId(position, 0), " ".join(generator.choices(words, k=WORDS_PER_DOCUMENT))

    tracemalloc.start()
    try:
        InvertedIndex.build(path, documents(), analyzer, config).close()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_build_memory_does_not_grow_with_the_number_of_documents(config: EngineConfig):
    bounded = with_blocks(config, MEMORY_BLOCK_BYTES, config.merge_fan_in)
    few = peak_memory_of_build(config.data_directory / "pocos.inv", FEW_DOCUMENTS, bounded)
    many = peak_memory_of_build(config.data_directory / "muchos.inv", MANY_DOCUMENTS, bounded)
    with InvertedIndex(config.data_directory / "muchos.inv", bounded) as index:
        assert index.block_count > 4
    assert many <= few * MEMORY_GROWTH_TOLERANCE


class CollectingSink:
    """Un `TermSink` que guarda los postings de cada término, ya decodificados."""

    def __init__(self) -> None:
        self.postings: dict[bytes, list[int]] = {}

    def add_term(
        self, term: bytes, document_frequency: int, total_frequency: int, postings: Iterable[bytes]
    ) -> None:
        self.postings[term] = list(decode_postings(b"".join(postings)))


def one_document_blocks(passes: MergePasses, count: int) -> list[Block]:
    """`count` bloques con el mismo término, cada uno con el documento de su posición."""
    return [
        passes.write_block(
            lambda sink, doc_id=doc_id: sink.add_term(
                SHARED_TERM, 1, 1, (encode_postings(array(POSTING_TYPECODE, [doc_id, 1])),)
            )
        )
        for doc_id in range(count)
    ]


@pytest.mark.parametrize(
    ("block_count", "fan_in", "untouched"),
    [(9, 8, 7), (8, 8, 8), (20, 8, 6), (5, 2, 1), (100, 8, 0)],
)
def test_a_merge_pass_rewrites_only_the_trailing_blocks_it_needs(
    config: EngineConfig, block_count: int, fan_in: int, untouched: int
):
    passes = MergePasses(config.data_directory / "mezcla", with_blocks(config, 1, fan_in))
    try:
        blocks = one_document_blocks(passes, block_count)
        remaining = passes.reduce(blocks)
        assert len(remaining) == min(block_count, fan_in)
        assert remaining[:untouched] == blocks[:untouched]
        sink = CollectingSink()
        merge_sources(remaining, config, sink)
        doc_ids = sink.postings[SHARED_TERM][0::POSTING_FIELDS]
        assert doc_ids == list(range(block_count))
    finally:
        passes.remove_all()
