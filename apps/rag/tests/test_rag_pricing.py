"""El costo de una respuesta con los precios de la tabla, por intento."""

from __future__ import annotations

import pytest

from rag.pricing import (
    PRICES,
    SIMULATED_MODEL,
    SONNET_5,
    SONNET_5_5,
    Attempt,
    TokenUsage,
    attempt_cost,
    total_cost,
    total_usage,
    worst_case_cost,
)

MILLION = 1_000_000


def test_each_token_class_has_its_price():
    usage = TokenUsage(
        input_tokens=MILLION,
        output_tokens=MILLION,
        cache_write_5m_tokens=MILLION,
        cache_write_1h_tokens=MILLION,
        cache_read_tokens=MILLION,
    )
    assert attempt_cost(Attempt(SONNET_5_5, usage)) == pytest.approx(2 + 10 + 2.5 + 4 + 0.1)


def test_a_typical_answer_costs_about_a_cent():
    usage = TokenUsage(input_tokens=3000, output_tokens=400, cache_read_tokens=650)
    assert attempt_cost(Attempt(SONNET_5_5, usage)) == pytest.approx(0.010065)


def test_cache_reads_cost_half_on_sonnet_5_5_than_on_sonnet_5():
    assert PRICES[SONNET_5_5].cache_read * 2 == PRICES[SONNET_5].cache_read


def test_a_fallback_attempt_is_billed_at_its_own_model_price():
    usage = TokenUsage(cache_read_tokens=MILLION)
    attempts = [Attempt(SONNET_5_5, usage), Attempt(SONNET_5, usage)]
    assert total_cost(attempts) == pytest.approx(0.10 + 0.20)
    assert total_usage(attempts) == TokenUsage(cache_read_tokens=2 * MILLION)


def test_an_unknown_model_has_no_cost():
    attempts = [Attempt(SONNET_5_5, TokenUsage(input_tokens=1)), Attempt("otro", TokenUsage())]
    assert attempt_cost(attempts[1]) is None
    assert total_cost(attempts) is None


def test_no_attempts_cost_nothing():
    assert total_cost([]) == 0
    assert total_usage([]) == TokenUsage()


def test_the_simulated_model_is_free():
    usage = TokenUsage(input_tokens=MILLION, output_tokens=MILLION)
    assert attempt_cost(Attempt(SIMULATED_MODEL, usage)) == 0


def test_the_worst_case_takes_the_dearest_input_and_every_output_token():
    assert worst_case_cost(SONNET_5_5, MILLION, MILLION) == pytest.approx(4 + 10)
