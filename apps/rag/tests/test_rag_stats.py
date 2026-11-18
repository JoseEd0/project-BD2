from __future__ import annotations

from rag.stats import HISTOGRAM_BIN_WORDS, length_distribution, percentile


def test_percentiles_use_the_nearest_rank() -> None:
    values = list(range(1, 101))
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile([7], 95) == 7


def test_the_distribution_counts_every_value_in_its_bin() -> None:
    values = [200, 210, 250, 300, 499]
    distribution = length_distribution(values, max_words=500)
    assert distribution["min"] == 200
    assert distribution["max"] == 499
    assert distribution["p50"] == 250
    histogram = distribution["histogram"]
    assert isinstance(histogram, dict)
    assert histogram["bin_words"] == HISTOGRAM_BIN_WORDS
    counts = histogram["counts"]
    assert len(counts) == 500 // HISTOGRAM_BIN_WORDS
    bin_starts = [
        index * HISTOGRAM_BIN_WORDS for index, count in enumerate(counts) for _ in range(count)
    ]
    assert bin_starts == [value // HISTOGRAM_BIN_WORDS * HISTOGRAM_BIN_WORDS for value in values]


def test_an_empty_distribution_only_has_its_count() -> None:
    assert length_distribution([], max_words=500) == {"count": 0}
