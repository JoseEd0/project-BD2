"""Un tope de gasto diario en el modelo de lenguaje, por réplica del API.

Antes de llamar al modelo se reserva la cota de lo que puede costar la respuesta (toda la
entrada al precio más caro y `max_tokens` de salida); al terminar, la reserva se cambia por
el costo real. Si la reserva no cabe en lo que queda del día (UTC), la pregunta se rechaza sin
llamar al modelo: varias preguntas a la vez nunca pasan juntas el tope. Una respuesta cortada
(el cliente se fue o el proveedor falló) se queda con su reserva: lo que alcanzó a gastar no
se conoce y la cota es lo seguro.

Vive en la memoria de cada réplica y solo se toca desde su bucle de eventos (sin hilos): con N
réplicas el gasto del día puede llegar a N veces el tope, y reiniciar el contenedor lo pone
en cero.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from http import HTTPStatus

from appkit import ServiceError

Today = Callable[[], date]


def utc_today() -> date:
    return datetime.now(UTC).date()


class BudgetExhaustedError(ServiceError):
    """Lo que queda del tope del día no alcanza para una respuesta más."""

    status = HTTPStatus.TOO_MANY_REQUESTS


class Reservation:
    """Lo reservado para una respuesta; se cierra una sola vez con `settle`."""

    def __init__(self, budget: DailyBudget, day: date, amount: float) -> None:
        self._budget = budget
        self._day = day
        self.amount = amount
        self._settled = False

    @property
    def settled(self) -> bool:
        return self._settled

    def settle(self, actual_cost: float | None) -> None:
        """Cambia la reserva por `actual_cost`; con `None` (costo desconocido), la deja."""
        if self._settled:
            return
        self._settled = True
        if actual_cost is not None:
            self._budget.adjust(self._day, actual_cost - self.amount)


class DailyBudget:
    """El gasto del día en dólares contra `limit_usd`."""

    def __init__(self, limit_usd: float, today: Today = utc_today) -> None:
        self._limit = limit_usd
        self._today = today
        self._day = today()
        self._spent = 0.0

    @property
    def spent(self) -> float:
        self._roll_over()
        return self._spent

    def reserve(self, amount: float) -> Reservation:
        """Reserva `amount` dólares del día.

        Raises:
            BudgetExhaustedError: si no caben en lo que queda del tope.
        """
        self._roll_over()
        if self._spent + amount > self._limit:
            raise BudgetExhaustedError(
                "se alcanzó el gasto diario del servicio en el modelo de lenguaje; "
                "vuelve a preguntar mañana"
            )
        self._spent += amount
        return Reservation(self, self._day, amount)

    def adjust(self, day: date, difference: float) -> None:
        self._roll_over()
        if day == self._day:
            self._spent = max(0.0, self._spent + difference)

    def _roll_over(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._spent = 0.0
