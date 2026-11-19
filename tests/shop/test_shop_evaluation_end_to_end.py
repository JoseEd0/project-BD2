"""La evaluación (#82) contra el gestor real con el catálogo diminuto: las compras que se dejan
fuera no alimentan nada de lo que el recomendador ve de cada usuario."""

from __future__ import annotations

from collections import Counter
from itertools import permutations
from pathlib import Path

import pytest
from apps.shop.tests.tiny_dataset import TINY_PURCHASES
from benchmarks.shop_evaluation.cases import read_split
from benchmarks.shop_evaluation.signals import Collection, collect, read_signals

from minigestor_client import AsyncClient, Point
from shop.catalog.schema import COPURCHASES
from shop.recommend.candidates import Signal
from shop.recommend.fusion import FusionWeights
from shop.recommend.recommender import Nearby, Recommender
from shop.settings import (
    DATASET_DIRECTORY,
    DEFAULT_COPURCHASE_MIN_SUPPORT,
    DEFAULT_RECOMMENDATION_SPLIT,
)

pytestmark = pytest.mark.anyio

TRAIN = "train"
CANDIDATES = 50
PROFILE_PRODUCTS = 10
COLLECTION = Collection(
    DEFAULT_RECOMMENDATION_SPLIT,
    limit=10,
    weights=FusionWeights(0.4, 0.25, 0.25, 0.1),
    nearby=Nearby(Point(-12.0464, -77.0428), 5000.0),
)


def pair_counts(splits: set[str]) -> Counter[tuple[str, str]]:
    """Cuántos clientes compraron cada par ordenado, contando solo las compras de `splits`."""
    baskets: dict[str, set[str]] = {}
    for user, product, _, _, split in TINY_PURCHASES:
        if split in splits:
            baskets.setdefault(user, set()).add(product)
    return Counter(pair for basket in baskets.values() for pair in permutations(sorted(basket), 2))


def supported(counts: Counter[tuple[str, str]]) -> dict[tuple[str, str], int]:
    return {pair: count for pair, count in counts.items() if count >= DEFAULT_COPURCHASE_MIN_SUPPORT}


async def test_held_out_purchases_never_reach_the_copurchases_or_the_history(
    seeded: None, engine: AsyncClient, datasets: Path, tmp_path: Path
) -> None:
    split = read_split(datasets / DATASET_DIRECTORY)
    rows = await engine.fetch_all(f"SELECT producto, otro, particion, veces FROM {COPURCHASES}", [])
    materialized = {(row["producto"], row["otro"]): row["veces"] for row in rows}
    assert {row["particion"] for row in rows} == {TRAIN}
    assert materialized == supported(pair_counts({TRAIN}))
    assert materialized != supported(pair_counts({TRAIN, "valid", "test"}))

    path = tmp_path / "senales.jsonl"
    recommender = Recommender(engine, DEFAULT_RECOMMENDATION_SPLIT, CANDIDATES, PROFILE_PRODUCTS)
    await collect(engine, recommender, sorted(split.held_out), COLLECTION, path)
    found = read_signals(path)

    train_only = pair_counts({TRAIN})
    for user, held in split.held_out.items():
        signals = found[user]
        assert len(signals.history) == split.train_purchases[user]
        for target in (held.validation, held.test):
            assert target not in signals.history
            for bought in signals.history:
                assert materialized.get((bought, target), 0) == supported(train_only).get(
                    (bought, target), 0
                )
        assert signals.clip[Signal.COPURCHASE] == signals.sift[Signal.COPURCHASE]
