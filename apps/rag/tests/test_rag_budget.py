"""El tope de gasto diario: reserva, ajuste al costo real, rechazo y cambio de día."""

from __future__ import annotations

from datetime import date

import pytest

from rag.budget import BudgetExhaustedError, DailyBudget

MONDAY = date(2026, 11, 16)
TUESDAY = date(2026, 11, 17)


class Calendar:
    def __init__(self) -> None:
        self.today = MONDAY

    def __call__(self) -> date:
        return self.today


def test_a_reservation_is_replaced_by_the_actual_cost():
    budget = DailyBudget(1.0, Calendar())
    reservation = budget.reserve(0.5)
    assert budget.spent == pytest.approx(0.5)
    reservation.settle(0.01)
    assert budget.spent == pytest.approx(0.01)


def test_settling_twice_counts_once():
    budget = DailyBudget(1.0, Calendar())
    reservation = budget.reserve(0.5)
    reservation.settle(0.1)
    reservation.settle(0.3)
    assert budget.spent == pytest.approx(0.1)
    assert reservation.settled


def test_an_unknown_cost_keeps_the_reservation():
    budget = DailyBudget(1.0, Calendar())
    budget.reserve(0.4).settle(None)
    assert budget.spent == pytest.approx(0.4)


def test_a_reservation_that_does_not_fit_is_refused():
    budget = DailyBudget(1.0, Calendar())
    budget.reserve(0.7)
    with pytest.raises(BudgetExhaustedError) as refused:
        budget.reserve(0.4)
    assert refused.value.status == 429
    assert budget.spent == pytest.approx(0.7)


def test_a_zero_budget_refuses_everything():
    with pytest.raises(BudgetExhaustedError):
        DailyBudget(0.0, Calendar()).reserve(0.001)


def test_a_new_day_starts_from_zero_and_ignores_yesterdays_settlement():
    calendar = Calendar()
    budget = DailyBudget(1.0, calendar)
    yesterday = budget.reserve(0.9)
    calendar.today = TUESDAY
    assert budget.spent == 0
    budget.reserve(0.9)
    yesterday.settle(0.0)
    assert budget.spent == pytest.approx(0.9)
