"""La espera entre reintentos: crece, tiene tope y es al azar."""

from __future__ import annotations

import pytest

from minigestor_client import RetryPolicy

SAMPLES = 200


def test_the_delay_stays_under_a_ceiling_that_doubles():
    policy = RetryPolicy(attempts=5, base_delay_seconds=0.1, max_delay_seconds=10.0)
    for retry, ceiling in [(1, 0.1), (2, 0.2), (3, 0.4)]:
        delays = [policy.delay_before(retry) for _ in range(SAMPLES)]
        assert all(0 <= delay <= ceiling for delay in delays)
        assert len(set(delays)) > 1


def test_the_ceiling_stops_at_the_maximum():
    policy = RetryPolicy(attempts=20, base_delay_seconds=1.0, max_delay_seconds=2.0)
    assert all(policy.delay_before(15) <= 2.0 for _ in range(SAMPLES))


@pytest.mark.parametrize(
    "arguments",
    [{"attempts": 0}, {"base_delay_seconds": -1.0}, {"max_delay_seconds": -0.5}],
)
def test_impossible_policies_are_refused(arguments: dict[str, float]):
    with pytest.raises(ValueError):
        RetryPolicy(**arguments)  # type: ignore[arg-type]
