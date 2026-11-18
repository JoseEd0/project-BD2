"""El 5-core y la partición dejando fuera la última compra de cada usuario."""

from pathlib import Path

import pytest

from shop.dataset.errors import CoreError, SplitError
from shop.dataset.interactions import (
    Interaction,
    chronological,
    counts_by_user,
    read_split,
    train_popularity,
    verify_core,
    verify_leave_last_out,
)
from shop.dataset.sources import Split


def bought(user: str, product: str, timestamp: int, split: Split) -> Interaction:
    return Interaction(user, product, 5.0, timestamp, split)


def history(user: str, timestamps: list[int]) -> list[Interaction]:
    """Las compras de un usuario, con las dos últimas en validación y prueba."""
    splits = [Split.TRAIN] * (len(timestamps) - 2) + [Split.VALID, Split.TEST]
    return [
        bought(user, f"P{index}", timestamp, split)
        for index, (timestamp, split) in enumerate(zip(timestamps, splits, strict=True))
    ]


def test_read_split_parses_the_benchmark_columns(tmp_path: Path) -> None:
    path = tmp_path / "test.csv"
    path.write_text("user_id,parent_asin,rating,timestamp\nU1,B1,4.0,1659727366632\n")
    [item] = read_split(path, Split.TEST)
    assert item == Interaction("U1", "B1", 4.0, 1659727366632, Split.TEST)
    assert item.purchased_at == "2022-08-05T19:22:46.632+00:00"


def test_a_core_where_everyone_reaches_the_minimum_passes() -> None:
    items = [
        bought(f"U{user}", f"P{product}", 0, Split.TRAIN)
        for user in range(2)
        for product in range(2)
    ]
    verify_core(items, 2)


def test_a_user_below_the_minimum_is_reported() -> None:
    items = [bought("U1", "P1", 0, Split.TRAIN), bought("U1", "P2", 0, Split.TRAIN)]
    items.append(bought("U2", "P1", 0, Split.TRAIN))
    with pytest.raises(CoreError, match="usuario U2"):
        verify_core(items, 2)


def test_a_product_below_the_minimum_is_reported() -> None:
    items = [bought("U1", "P1", 0, Split.TRAIN), bought("U2", "P1", 0, Split.TRAIN)]
    items += [bought("U1", "P2", 0, Split.TRAIN), bought("U2", "P3", 0, Split.TRAIN)]
    with pytest.raises(CoreError, match="producto P2"):
        verify_core(items, 2)


def test_leave_last_out_accepts_the_last_two_purchases_held_out() -> None:
    verify_leave_last_out(history("U1", [1, 2, 3, 4, 5]) + history("U2", [10, 20, 30]))


def test_leave_last_out_accepts_ties_at_the_boundary() -> None:
    verify_leave_last_out(history("U1", [1, 5, 5, 5]))


def test_a_user_without_a_test_purchase_is_rejected() -> None:
    items = history("U1", [1, 2, 3])[:-1]
    with pytest.raises(SplitError, match="0 compras en test"):
        verify_leave_last_out(items)


def test_a_user_with_two_validation_purchases_is_rejected() -> None:
    items = [*history("U1", [1, 2, 3]), bought("U1", "P9", 2, Split.VALID)]
    with pytest.raises(SplitError, match="2 compras en valid"):
        verify_leave_last_out(items)


def test_a_training_purchase_after_validation_is_rejected() -> None:
    items = [*history("U1", [1, 2, 3]), bought("U1", "P9", 9, Split.TRAIN)]
    with pytest.raises(SplitError, match="entrenamiento después"):
        verify_leave_last_out(items)


def test_validation_after_test_is_rejected() -> None:
    items = [bought("U1", "P0", 1, Split.TRAIN), bought("U1", "P1", 5, Split.VALID)]
    items.append(bought("U1", "P2", 3, Split.TEST))
    with pytest.raises(SplitError, match="validación después de la prueba"):
        verify_leave_last_out(items)


def test_chronological_breaks_ties_by_user_and_product() -> None:
    items = [
        bought("U2", "P1", 5, Split.TRAIN),
        bought("U1", "P2", 5, Split.TRAIN),
        bought("U1", "P1", 5, Split.TRAIN),
        bought("U3", "P1", 1, Split.TRAIN),
    ]
    ordered = [(item.user_id, item.product_id) for item in chronological(items)]
    assert ordered == [("U3", "P1"), ("U1", "P1"), ("U1", "P2"), ("U2", "P1")]


def test_counts_and_popularity_only_from_training() -> None:
    items = history("U2", [1, 2, 3]) + history("U1", [1, 2, 3, 4])
    assert counts_by_user(items) == {"U1": 4, "U2": 3}
    assert train_popularity(items) == {"P0": 2, "P1": 1}
