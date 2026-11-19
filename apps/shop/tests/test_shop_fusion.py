"""La fusión: normalización min-max, pesos, arranque en frío, exclusiones y empates."""

from __future__ import annotations

import pytest

from shop.recommend.candidates import Candidate, CopurchaseEvidence, Signal, StockEvidence
from shop.recommend.fusion import FusionWeights, fuse, min_max

WEIGHTS = FusionWeights(copurchase=0.4, visual=0.3, text=0.2, nearby=0.1)


def bought(product: str, customers: int) -> Candidate:
    return Candidate(product, customers, CopurchaseEvidence(customers, 1))


def test_min_max_maps_the_best_to_one_and_the_worst_to_zero():
    scores = min_max([Candidate("a", 10.0), Candidate("b", 4.0), Candidate("c", 1.0)])
    assert scores == {"a": 1.0, "b": pytest.approx(1 / 3), "c": 0.0}


def test_min_max_of_a_tie_or_a_single_candidate_is_one():
    assert min_max([Candidate("a", 0.8), Candidate("b", 0.8)]) == {"a": 1.0, "b": 1.0}
    assert min_max([Candidate("a", 3.0)]) == {"a": 1.0}
    assert min_max([]) == {}


def test_scores_are_weighted_sums_of_normalized_scores():
    fusion = fuse(
        {
            Signal.COPURCHASE: [bought("a", 9), bought("b", 1)],
            Signal.VISUAL: [Candidate("b", 0.9), Candidate("a", 0.5)],
            Signal.TEXT: [Candidate("c", 30.0)],
        },
        WEIGHTS,
        excluded=(),
        limit=10,
    )
    scores = {fused.product: fused.score for fused in fusion.ranked}
    assert scores == {"a": pytest.approx(0.4), "b": pytest.approx(0.3), "c": pytest.approx(0.2)}
    assert fusion.cold_start is False
    first = fusion.ranked[0]
    assert [part.signal for part in first.contributions] == [Signal.COPURCHASE, Signal.VISUAL]


def test_without_copurchases_the_weight_moves_to_image_and_text():
    fusion = fuse(
        {Signal.COPURCHASE: [], Signal.VISUAL: [Candidate("a", 0.9)], Signal.TEXT: []},
        WEIGHTS,
        excluded=(),
        limit=10,
    )
    assert fusion.cold_start is True
    assert fusion.weights == FusionWeights(0.0, pytest.approx(0.54), pytest.approx(0.36), 0.1)
    assert fusion.ranked[0].score == pytest.approx(0.54)


def test_cold_start_with_no_image_or_text_weight_splits_evenly():
    assert FusionWeights(0.4, 0.0, 0.0, 0.1).for_cold_start() == FusionWeights(0.0, 0.2, 0.2, 0.1)


def test_excluded_products_are_dropped_before_normalizing():
    fusion = fuse(
        {Signal.VISUAL: [Candidate("query", 1.0), Candidate("a", 0.8), Candidate("b", 0.6)]},
        WEIGHTS,
        excluded={"query"},
        limit=10,
    )
    assert [fused.product for fused in fusion.ranked] == ["a", "b"]
    assert fusion.ranked[0].contributions[0].normalized == 1.0


def test_nearby_only_reinforces_and_keeps_its_absolute_score():
    stock = StockEvidence("Centro", 500.0)
    fusion = fuse(
        {
            Signal.VISUAL: [Candidate("a", 0.9), Candidate("b", 0.5)],
            Signal.NEARBY: [Candidate("b", 0.9, stock), Candidate("z", 1.0, stock)],
        },
        WEIGHTS,
        excluded=(),
        limit=10,
    )
    scores = {fused.product: fused.score for fused in fusion.ranked}
    assert scores == {"a": pytest.approx(0.54), "b": pytest.approx(0.09)}


def test_ties_go_to_more_signals_then_to_the_smaller_id():
    fusion = fuse(
        {
            Signal.VISUAL: [Candidate("b", 1.0), Candidate("c", 1.0), Candidate("a", 1.0)],
            Signal.TEXT: [Candidate("c", 2.0), Candidate("x", 1.0)],
        },
        FusionWeights(0.0, 0.5, 0.0, 0.0),
        excluded=(),
        limit=3,
    )
    assert [fused.product for fused in fusion.ranked] == ["c", "a", "b"]


def test_the_version_changes_with_the_weights():
    assert WEIGHTS.version() == FusionWeights(0.4, 0.3, 0.2, 0.1).version()
    assert WEIGHTS.version() != FusionWeights(0.4, 0.3, 0.2, 0.2).version()
