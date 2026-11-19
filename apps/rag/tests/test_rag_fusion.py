"""Reciprocal Rank Fusion con k = 60."""

from __future__ import annotations

import pytest

from rag.fusion import RRF_K, reciprocal_rank_fusion


def test_no_lists_fuse_into_nothing():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_one_list_keeps_its_order_and_scores_by_rank():
    fused = reciprocal_rank_fusion([["a", "b"]])
    assert [item.key for item in fused] == ["a", "b"]
    assert fused[0].score == pytest.approx(1 / (RRF_K + 1))
    assert fused[1].ranks == (2,)


def test_a_document_in_both_lists_beats_one_that_tops_only_one():
    fused = reciprocal_rank_fusion([["solo", "both"], ["other", "both"]])
    assert fused[0].key == "both"
    assert fused[0].score == pytest.approx(2 / (RRF_K + 2))
    assert fused[0].ranks == (2, 2)


def test_ranks_say_where_each_list_had_the_document():
    fused = {item.key: item for item in reciprocal_rank_fusion([["a", "b"], ["c", "a"]])}
    assert fused["a"].ranks == (1, 2)
    assert fused["b"].ranks == (2, None)
    assert fused["c"].ranks == (None, 1)


def test_ties_are_broken_by_best_rank_then_key():
    fused = reciprocal_rank_fusion([["b", "x"], ["a", "y"]])
    assert [item.key for item in fused] == ["a", "b", "x", "y"]


def test_a_smaller_k_rewards_the_top_more():
    lists = [["top", "both"], ["filler", "both"]]

    def order(k: int) -> list[str]:
        return [item.key for item in reciprocal_rank_fusion(lists, k=k)]

    assert order(0).index("top") < order(0).index("both")
    assert order(RRF_K)[0] == "both"


def test_a_repeated_key_or_negative_k_is_refused():
    with pytest.raises(ValueError, match="repite"):
        reciprocal_rank_fusion([["a", "a"]])
    with pytest.raises(ValueError, match="k de RRF"):
        reciprocal_rank_fusion([["a"]], k=-1)
