"""La evaluación del recomendador (#82) sin gestor: las medidas sobre casos hechos a mano, la
lectura de la partición, las listas de referencia y la búsqueda de pesos."""

from __future__ import annotations

import json
import math
import random
from dataclasses import astuple
from pathlib import Path

import pytest
from apps.shop.tests.tiny_dataset import write_tiny_dataset
from benchmarks.shop_evaluation.cases import read_split, sample_users
from benchmarks.shop_evaluation.evaluation import Evaluator, Settings, Target
from benchmarks.shop_evaluation.metrics import (
    bootstrap_mean,
    coverage,
    hit_at,
    mean_popularity,
    ndcg_at,
    paired_difference,
    rank_of,
)
from benchmarks.shop_evaluation.rankings import (
    alone,
    at_random,
    fused,
    is_cold_start,
    most_popular,
)
from benchmarks.shop_evaluation.signals import UserSignals, read_signals
from benchmarks.shop_evaluation.weights import label, search, simplex

from shop.recommend.candidates import Candidate, Signal
from shop.recommend.fusion import FusionWeights

K = 10
SETTINGS = Settings(k=K, resamples=200, confidence=0.95, seed=7)
ONLY_COPURCHASES = FusionWeights(1.0, 0.0, 0.0, 0.0)
ONLY_TEXT = FusionWeights(0.0, 0.0, 1.0, 0.0)


def candidates(*products: str) -> tuple[Candidate, ...]:
    """Candidatos con puntajes que bajan en el orden dado."""
    return tuple(Candidate(product, float(len(products) - place)) for place, product in enumerate(products))


def user(name: str, history: tuple[str, ...], copurchases: tuple[str, ...], text: tuple[str, ...]) -> UserSignals:
    found = {Signal.COPURCHASE: candidates(*copurchases), Signal.TEXT: candidates(*text)}
    return UserSignals(name, history, found, found)


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    write_tiny_dataset(tmp_path)
    return tmp_path


def test_rank_counts_from_one_and_misses_are_none() -> None:
    assert rank_of("b", ["a", "b", "c"]) == 2
    assert rank_of("z", ["a", "b"]) is None


def test_hit_counts_only_the_first_k() -> None:
    assert hit_at(1, K) == 1.0
    assert hit_at(K, K) == 1.0
    assert hit_at(K + 1, K) == 0.0
    assert hit_at(None, K) == 0.0


def test_ndcg_with_one_relevant_product_on_hand_cases() -> None:
    assert ndcg_at(1, K) == 1.0
    assert ndcg_at(3, K) == 0.5
    assert ndcg_at(K, K) == pytest.approx(1 / math.log2(K + 1))
    assert ndcg_at(K + 1, K) == 0.0
    assert ndcg_at(None, K) == 0.0


def test_bootstrap_of_a_constant_is_that_constant() -> None:
    interval = bootstrap_mean([0.25] * 50, resamples=100, confidence=0.95, seed=1)
    assert (interval.mean, interval.low, interval.high) == (0.25, 0.25, 0.25)


def test_bootstrap_brackets_the_mean_and_repeats_with_its_seed() -> None:
    values = [1.0] * 30 + [0.0] * 70
    first = bootstrap_mean(values, resamples=500, confidence=0.95, seed=3)
    assert first.low < first.mean == 0.3 < first.high
    assert bootstrap_mean(values, resamples=500, confidence=0.95, seed=3) == first


def test_bootstrap_rejects_no_values() -> None:
    with pytest.raises(ValueError, match="no hay valores"):
        bootstrap_mean([], resamples=10, confidence=0.95, seed=1)


def test_paired_difference_needs_the_same_users() -> None:
    assert paired_difference([1.0, 0.0], [0.0, 0.0], 50, 0.95, 1).mean == 0.5
    with pytest.raises(ValueError, match="mismos usuarios"):
        paired_difference([1.0], [1.0, 0.0], 50, 0.95, 1)


def test_coverage_and_popularity_of_hand_lists() -> None:
    lists = [["a", "b"], ["b", "c"]]
    assert coverage(lists, catalog_size=8) == 3 / 8
    assert mean_popularity(lists, {"a": 10, "b": 2}) == (10 + 2 + 2 + 0) / 4
    assert mean_popularity([], {"a": 1}) == 0.0


def test_a_signal_alone_keeps_its_order_and_skips_the_history() -> None:
    assert alone(candidates("a", "b", "c", "d"), {"b"}, 2) == ["a", "c"]


def test_most_popular_and_random_never_recommend_the_history() -> None:
    assert most_popular(["a", "b", "c", "d"], {"a", "c"}, 2) == ["b", "d"]
    catalog = [f"P{number}" for number in range(50)]
    history = set(catalog[:10])
    drawn = at_random(catalog, history, K, random.Random(5))
    assert len(drawn) == len(set(drawn)) == K
    assert not history & set(drawn)
    assert drawn == at_random(catalog, history, K, random.Random(5))


def test_fusion_is_a_cold_start_only_without_copurchases_left() -> None:
    found = {Signal.COPURCHASE: candidates("a"), Signal.TEXT: candidates("b", "c")}
    assert not is_cold_start(found, set())
    assert is_cold_start(found, {"a"})
    assert fused(found, ONLY_COPURCHASES, {"a"}, K) == ["b", "c"]


def test_the_split_of_the_tiny_dataset(dataset: Path) -> None:
    split = read_split(dataset)
    assert split.held_out["USER-A"].validation == "B000COPYGT"
    assert split.held_out["USER-A"].test == "B000STRING"
    assert split.train_purchases == {"USER-A": 2, "USER-B": 3, "USER-C": 2}
    assert split.popularity["B000GUITAR"] == 3
    assert split.popularity["B000COPYGT"] == 0
    assert split.most_popular()[:2] == ("B000GUITAR", "B000CABLES")
    assert len(split.catalog) == len(set(split.catalog))


def test_a_larger_sample_contains_the_smaller_one() -> None:
    users = [f"U{number:04d}" for number in range(1000)]
    small, large = sample_users(users, 50, 82), sample_users(users, 200, 82)
    assert set(small) < set(large)
    assert small == sorted(small)
    assert sample_users(users, 5000, 82) == users


def test_the_evaluator_scores_each_target_on_hand_lists(dataset: Path) -> None:
    split = read_split(dataset)
    users = [
        user("USER-A", ("B000GUITAR", "B000CABLES"), ("B000STRING", "B000COPYGT"), ()),
        user("USER-B", ("B0000DRUMS", "B000GUITAR", "B000CABLES"), (), ("B000STRING",)),
    ]
    evaluator = Evaluator(users, split, SETTINGS)
    copurchases = evaluator.lists(lambda found, excluded: alone(found.clip[Signal.COPURCHASE], excluded, K))
    validation = evaluator.scores(copurchases, Target.VALIDATION, evaluator.everyone())
    test = evaluator.scores(copurchases, Target.TEST, evaluator.everyone())
    assert validation.hits == [1.0, 0.0]
    assert validation.ndcgs == [pytest.approx(1 / math.log2(3)), 0.0]
    assert test.ndcgs == [1.0, 0.0]
    summary = evaluator.summary(copurchases, Target.TEST, [0])
    assert summary["hit"] == summary["hit_bajo"] == summary["hit_alto"] == 1.0
    assert summary["usuarios"] == 1


def test_the_simplex_grid_sums_to_one_without_repeats() -> None:
    four = simplex(10, (Signal.COPURCHASE, Signal.VISUAL, Signal.TEXT, Signal.NEARBY))
    assert len(four) == math.comb(10 + 3, 3)
    assert len(set(four)) == len(four)
    for weights in four:
        assert weights.copurchase + weights.visual + weights.text + weights.nearby == pytest.approx(1.0)
    three = simplex(4, (Signal.COPURCHASE, Signal.VISUAL, Signal.TEXT))
    assert len(three) == math.comb(4 + 2, 2)
    assert all(weights.nearby == 0.0 for weights in three)
    with pytest.raises(ValueError, match="al menos un paso"):
        simplex(0, (Signal.TEXT,))


def test_the_weight_search_is_deterministic_and_breaks_ties_by_weights() -> None:
    grid = simplex(4, (Signal.COPURCHASE, Signal.VISUAL, Signal.TEXT))

    def score(weights: FusionWeights) -> tuple[float, float]:
        return round(weights.text, 1), 0.5

    first = search(grid, score)
    shuffled = list(grid)
    random.Random(1).shuffle(shuffled)
    assert search(shuffled, score) == first
    assert first[0].weights == FusionWeights(0.0, 0.0, 1.0, 0.0)
    tied = [astuple(found.weights) for found in first if found.ndcg == 0.5]
    assert len(tied) > 1
    assert tied == sorted(tied)


def test_the_weight_search_finds_the_signal_that_hits(dataset: Path) -> None:
    split = read_split(dataset)
    users = [user("USER-A", ("B000GUITAR",), ("B0000DRUMS",), ("B000COPYGT",))]
    evaluator = Evaluator(users, split, SETTINGS)

    def score(weights: FusionWeights) -> tuple[float, float]:
        lists = evaluator.lists(lambda found, excluded: fused(found.clip, weights, excluded, K)[:1])
        scores = evaluator.scores(lists, Target.VALIDATION, evaluator.everyone())
        return scores.mean_ndcg(), scores.mean_hit()

    best = search([ONLY_COPURCHASES, ONLY_TEXT], score)[0]
    assert best.weights == ONLY_TEXT
    assert (best.ndcg, best.hit) == (1.0, 1.0)


def test_weight_labels_follow_the_signal_order() -> None:
    assert label(FusionWeights(0.4, 0.25, 0.25, 0.1)) == "0,40 · 0,25 · 0,25 · 0,10"


def test_signals_survive_the_json_lines_file(tmp_path: Path) -> None:
    saved = user("USER-A", ("B000GUITAR",), ("B0000DRUMS",), ("B000COPYGT", "B000STRING"))
    path = tmp_path / "senales.jsonl"
    path.write_text(json.dumps(saved.to_json()) + "\n", encoding="utf-8")
    assert read_signals(path) == {"USER-A": saved}
    assert read_signals(tmp_path / "no-existe.jsonl") == {}
