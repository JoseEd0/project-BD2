from collections import Counter
from pathlib import Path

import pytest

from shop.spelling import Vocabulary, edit_distance, load_vocabulary


@pytest.fixture
def vocabulary() -> Vocabulary:
    return Vocabulary(
        Counter(
            {
                "guitar": 900,
                "guitars": 40,
                "amplifier": 300,
                "amplifer": 3,
                "strings": 500,
                "drum": 200,
                "sticks": 150,
                "cajon": 30,
                "capo": 60,
                "electric": 400,
                "typo": 1,
            }
        )
    )


@pytest.mark.parametrize(
    ("left", "right", "distance"),
    [("guitar", "guitar", 0), ("guitr", "guitar", 1), ("gutiar", "guitar", 1), ("", "abc", 3)],
)
def test_edit_distance_counts_transpositions_as_one(left: str, right: str, distance: int):
    assert edit_distance(left, right, 3) == distance


def test_edit_distance_stops_past_the_limit():
    assert edit_distance("guitar", "microphone", 2) == 3


def test_an_empty_vocabulary_corrects_nothing():
    assert Vocabulary(Counter()).correct("guitr") is None
    assert len(Vocabulary(Counter())) == 0


def test_a_known_query_is_left_alone(vocabulary: Vocabulary):
    assert vocabulary.correct("guitar strings") is None
    assert vocabulary.correct("cajon capo") is None


def test_a_missing_letter_is_corrected(vocabulary: Vocabulary):
    correction = vocabulary.correct("guitr strings")
    assert correction is not None
    assert correction.query == "guitar strings"
    assert correction.changes == (("guitr", "guitar"),)


def test_two_edits_are_forgiven_in_long_words(vocabulary: Vocabulary):
    correction = vocabulary.correct("elcetrik")
    assert correction is not None and correction.query == "electric"


def test_a_rare_spelling_yields_to_a_far_more_common_neighbour(vocabulary: Vocabulary):
    correction = vocabulary.correct("amplifer")
    assert correction is not None and correction.query == "amplifier"


def test_words_seen_once_are_not_vocabulary(vocabulary: Vocabulary):
    assert "typo" not in vocabulary
    assert "Guitar" in vocabulary


def test_short_words_and_boolean_operators_stay(vocabulary: Vocabulary):
    correction = vocabulary.correct("drm AND guitr")
    assert correction is not None and correction.query == "drm AND guitar"


def test_a_word_too_far_from_everything_stays(vocabulary: Vocabulary):
    assert vocabulary.correct("xylophone") is None


def test_the_vocabulary_is_read_from_titles_and_brands(tmp_path: Path):
    products = tmp_path / "products.csv"
    products.write_text(
        "product_id,title,brand\n1,Electric Guitar,Fender\n2,Guitar Strings,Fender\n"
        "3,Electric Bass,Ibanez\n",
        encoding="utf-8",
    )
    vocabulary = load_vocabulary(products)
    assert all(word in vocabulary for word in ("electric", "guitar", "fender"))
    assert "ibanez" not in vocabulary
    assert len(load_vocabulary(tmp_path / "missing.csv")) == 0
