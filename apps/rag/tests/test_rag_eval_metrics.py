"""Recall@k, MRR y el acuerdo entre etiquetados, con casos calculados a mano."""

from __future__ import annotations

import pytest

from rag.evaluation.metrics import (
    Relevance,
    agreement,
    first_relevant_rank,
    is_relevant,
    recall_at,
    reciprocal_rank,
    summarize,
)

GOLD = "2501.00001#5"
RANKED = ["2502.00002#1", "2501.00001#6", "2501.00001#5", "2501.00001#9"]


def test_the_exact_level_only_counts_the_gold_chunk():
    assert first_relevant_rank(RANKED, GOLD, Relevance.EXACT) == 3


def test_the_neighbour_level_counts_the_chunks_next_to_it_in_the_same_paper():
    assert first_relevant_rank(RANKED, GOLD, Relevance.NEIGHBOUR) == 2
    assert not is_relevant("2501.00001#7", GOLD, Relevance.NEIGHBOUR)
    assert not is_relevant("2509.00001#5", GOLD, Relevance.NEIGHBOUR)


def test_the_paper_level_counts_any_chunk_of_the_paper():
    assert is_relevant("2501.00001#40", GOLD, Relevance.PAPER)
    assert first_relevant_rank(RANKED, GOLD, Relevance.PAPER) == 2


def test_course_chunk_ids_with_colons_parse_by_their_last_hash():
    assert is_relevant("curso:informe#15", "curso:informe#16", Relevance.NEIGHBOUR)


def test_a_missing_chunk_has_no_rank_and_scores_zero():
    rank = first_relevant_rank([], GOLD, Relevance.EXACT)
    assert rank is None
    assert recall_at(rank, 10) == 0.0
    assert reciprocal_rank(rank, 10) == 0.0


def test_recall_and_reciprocal_rank_cut_at_k():
    assert recall_at(3, 3) == 1.0
    assert recall_at(4, 3) == 0.0
    assert reciprocal_rank(4, 10) == pytest.approx(0.25)
    assert reciprocal_rank(11, 10) == 0.0


def test_summarize_averages_over_questions():
    scores = summarize([1, 3, None, 8], ks=(5, 10), mrr_depth=10)
    assert scores.questions == 4
    assert scores.recall == {5: 0.5, 10: 0.75}
    assert scores.mrr == pytest.approx((1 + 1 / 3 + 0 + 1 / 8) / 4)


def test_summarize_rejects_an_empty_group():
    with pytest.raises(ValueError):
        summarize([], ks=(5,), mrr_depth=10)


def test_agreement_matches_cohens_kappa_by_hand():
    judge = [True, True, True, False, False, True]
    human = [True, True, False, False, False, True]
    result = agreement(judge, human)
    # Observado 5/6; esperado 4/6·3/6 + 2/6·3/6 = 1/2; kappa = (5/6 - 1/2) / (1/2) = 2/3.
    assert result.accuracy == pytest.approx(5 / 6)
    assert result.cohen_kappa == pytest.approx(2 / 3)


def test_agreement_with_one_label_everywhere_is_perfect_or_nothing():
    assert agreement([True, True], [True, True]).cohen_kappa == 1.0
    assert agreement([True], [False]).cohen_kappa == 0.0


def test_agreement_needs_the_same_cases():
    with pytest.raises(ValueError):
        agreement([True], [True, False])
    with pytest.raises(ValueError):
        agreement([], [])
