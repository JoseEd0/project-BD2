"""Bytes canónicos de las claves: iguales para valores iguales y distintos para distintos."""

import pytest

from hashing import canonical_key_bytes


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (("a", "b\x00sc"), ("a\x00sb", "c")),
        (("ab", ""), ("a", "b")),
        ((1, (2, 3)), ((1, 2), 3)),
    ],
)
def test_different_tuples_never_share_bytes(first: tuple, second: tuple):
    assert canonical_key_bytes(first) != canonical_key_bytes(second)


def test_equal_numbers_share_bytes_inside_tuples():
    assert canonical_key_bytes((1, "x")) == canonical_key_bytes((1.0, "x"))
    assert canonical_key_bytes((0.0,)) == canonical_key_bytes((-0.0,))
