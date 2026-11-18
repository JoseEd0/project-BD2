from __future__ import annotations

from collections import Counter

import pytest

from rag.hyphenation import Evidence, Hyphenation, join_lines


def evidence_of(*lines: str) -> Evidence:
    return Evidence.from_pages([list(lines)])


def joined(lines: list[str], evidence: Evidence | None = None) -> tuple[str, Counter[str]]:
    tally: Counter[str] = Counter()
    text = join_lines(lines, evidence or evidence_of(*lines), tally)
    return text, tally


def test_a_compound_written_whole_elsewhere_keeps_its_hyphen() -> None:
    text, tally = joined(["We train a data-", "driven model.", "Data-Driven methods win."])
    assert text == "We train a data-driven model. Data-Driven methods win."
    assert tally == {Hyphenation.KEPT_COMPOUND_SEEN: 1}


def test_a_word_written_whole_elsewhere_is_joined() -> None:
    text, tally = joined(["The exam-", "ple shows it.", "Another example follows."])
    assert text == "The example shows it. Another example follows."
    assert tally == {Hyphenation.JOINED_WORD_SEEN: 1}


def test_both_parts_seen_as_words_and_never_joined_keep_the_hyphen() -> None:
    lines = ["a high-", "dimensional space", "high cost and dimensional data"]
    assert joined(lines)[0] == "a high-dimensional space high cost and dimensional data"


def test_short_parts_are_not_evidence_of_a_compound() -> None:
    lines = ["the in-", "formation needed", "in the formation of"]
    text, tally = joined(lines)
    assert text == "the information needed in the formation of"
    assert tally == {Hyphenation.JOINED_BY_DEFAULT: 1}


def test_without_evidence_a_lowercase_continuation_is_joined() -> None:
    text, tally = joined(["we use computa-", "tion here"])
    assert text == "we use computation here"
    assert tally == {Hyphenation.JOINED_BY_DEFAULT: 1}


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (["the k-", "nearest neighbors"], "the k-nearest neighbors"),
        (["a 2-", "dimensional grid"], "a 2-dimensional grid"),
        (["pages 10-", "20 here"], "pages 10-20 here"),
        (["a B-", "tree index"], "a B-tree index"),
        (["an LSM-", "tree store"], "an LSM-tree store"),
        (["end-to-", "end training"], "end-to-end training"),
        (["Smith-", "Jones and"], "Smith-Jones and"),
    ],
)
def test_without_evidence_numbers_letters_acronyms_and_names_keep_it(
    lines: list[str], expected: str
) -> None:
    text, tally = joined(lines)
    assert text == expected
    assert tally == {Hyphenation.KEPT_BY_DEFAULT: 1}


def test_evidence_is_case_insensitive_and_beats_the_default() -> None:
    evidence = evidence_of("BTree and btree are the same here.")
    assert joined(["a B-", "tree index"], evidence)[0] == "a Btree index"


def test_the_broken_pieces_are_not_evidence_of_themselves() -> None:
    evidence = evidence_of("we use computa-", "tion here")
    assert "computa" not in evidence.parts
    assert "tion" not in evidence.parts
    assert "here" in evidence.parts


def test_lines_without_a_hyphenated_break_are_joined_with_spaces() -> None:
    text, tally = joined(["first line", "second line", "a dash -", "(not a word)"])
    assert text == "first line second line a dash - (not a word)"
    assert tally == {}
