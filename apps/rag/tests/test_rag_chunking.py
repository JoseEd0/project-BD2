from __future__ import annotations

import itertools
import re

import pytest

from rag.chunking import Chunk, chunk_pages, chunk_sentences, split_sentences
from rag.settings import ChunkSettings

SETTINGS = ChunkSettings()
SENTENCE_END = re.compile(r"[.!?]$")


def sentence(number: int, words: int = 12) -> str:
    filler = " ".join(f"w{number}x{index}" for index in range(words - 2))
    return f"Sentence {number} {filler}."


def pages_of(sentences_per_page: list[int], words: int = 12) -> list[str]:
    pages = []
    number = 0
    for count in sentences_per_page:
        pages.append(" ".join(sentence(number + index, words) for index in range(count)))
        number += count
    return pages


def test_sentences_split_on_terminal_punctuation_but_not_abbreviations() -> None:
    text = (
        "We use e.g. BM25 in Fig. 3 here. It works! J. Smith et al. agree. Does it? (Yes) indeed."
    )
    assert [part for _, part in split_sentences(text)] == [
        "We use e.g. BM25 in Fig. 3 here.",
        "It works!",
        "J. Smith et al. agree.",
        "Does it?",
        "(Yes) indeed.",
    ]


def test_a_lowercase_continuation_or_a_decimal_is_not_a_boundary() -> None:
    parts = [part for _, part in split_sentences("Recall is 0.95 and grows. vs. the other one")]
    assert parts == ["Recall is 0.95 and grows. vs. the other one"]


def test_every_chunk_is_within_bounds() -> None:
    chunks = chunk_pages(pages_of([40] * 10), SETTINGS)
    assert len(chunks) > 5
    assert all(SETTINGS.min_words <= chunk.words <= SETTINGS.max_words for chunk in chunks)
    assert all(chunk.words == len(chunk.text.split()) for chunk in chunks)


@pytest.mark.parametrize("words", [7, 23, 61, 140, 299])
def test_bounds_hold_for_any_sentence_length(words: int) -> None:
    chunks = chunk_pages(pages_of([15] * 6, words), SETTINGS)
    assert all(SETTINGS.min_words <= chunk.words <= SETTINGS.max_words for chunk in chunks)


def test_chunks_never_cut_a_sentence() -> None:
    text_sentences = [sentence(number) for number in range(200)]
    for chunk in chunk_pages([" ".join(text_sentences)], SETTINGS):
        assert chunk.text.startswith("Sentence ")
        assert SENTENCE_END.search(chunk.text)
        assert all(part in text_sentences for _, part in split_sentences(chunk.text))


def test_consecutive_chunks_overlap_by_whole_sentences() -> None:
    chunks = chunk_pages(pages_of([300]), SETTINGS)
    for previous, following in itertools.pairwise(chunks):
        previous_sentences = [part for _, part in split_sentences(previous.text)]
        following_sentences = [part for _, part in split_sentences(following.text)]
        shared = [part for part in following_sentences if part in previous_sentences]
        assert shared == following_sentences[: len(shared)]
        assert shared == previous_sentences[-len(shared) :]
        shared_words = sum(len(part.split()) for part in shared)
        assert SETTINGS.overlap_words <= shared_words <= SETTINGS.max_overlap_words


def test_chunks_cover_every_sentence() -> None:
    pages = pages_of([37, 41, 29])
    chunks = chunk_pages(pages, SETTINGS)
    covered = {part for chunk in chunks for _, part in split_sentences(chunk.text)}
    assert covered == {part for page in pages for _, part in split_sentences(page)}


def test_chunks_remember_the_start_and_end_page() -> None:
    chunks = chunk_pages(pages_of([10, 10, 10, 10]), ChunkSettings(target_words=300))
    first = chunks[0]
    assert first.page_start == 1
    assert first.page_end == 3
    assert chunks[-1].page_end == 4
    assert all(chunk.page_start <= chunk.page_end for chunk in chunks)


def test_a_sentence_crossing_a_page_break_belongs_to_its_start_page() -> None:
    pages = ["Intro text. The cross page sentence starts here and", "ends on page two. Next one."]
    chunks = chunk_pages(pages, ChunkSettings(min_words=1, target_words=8, max_words=20))
    crossing = next(chunk for chunk in chunks if chunk.text.startswith("The cross page"))
    assert "ends on page two." in crossing.text
    assert crossing.page_start == 1
    assert crossing.page_end == 2


def test_an_overlong_sentence_is_split_by_words() -> None:
    giant = "word " * 1000 + "end."
    chunks = chunk_pages([giant], SETTINGS)
    assert sum(chunk.forced_splits for chunk in chunks) == 4
    assert all(chunk.words <= SETTINGS.max_words for chunk in chunks)


def test_the_tail_borrows_sentences_to_reach_the_minimum() -> None:
    chunks = chunk_pages(pages_of([26]), SETTINGS)
    assert chunks[-1].words >= SETTINGS.min_words
    assert chunks[-1].text.endswith(sentence(25).split()[-1])


def test_a_short_document_is_one_chunk_and_an_empty_one_none() -> None:
    assert chunk_pages(["Just one short sentence."], SETTINGS) == [
        Chunk("Just one short sentence.", 1, 1, 4, 0)
    ]
    assert chunk_pages(["", ""], SETTINGS) == []
    assert chunk_sentences([], SETTINGS) == []
