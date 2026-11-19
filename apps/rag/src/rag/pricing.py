"""Lo que cuesta una respuesta: los tokens de cada intento y el precio de su modelo.

Precios de la API de Anthropic en dólares por millón de tokens, tomados de la tabla de
modelos de la referencia del SDK (`claude-api`, consultada el 2026-11-19). Claude Sonnet 5.5
cobra lo mismo que Claude Sonnet 5 salvo la lectura de caché, que cuesta la mitad. Una
escritura en caché cuesta 1,25 veces la entrada con la vida de 5 minutos y 2 veces con la de
una hora; la lectura, 0,05 veces en Sonnet 5.5 y 0,1 veces en Sonnet 5. El razonamiento
(thinking) se cobra como salida y ya viene contado en `output_tokens`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

TOKENS_PER_PRICE_UNIT = 1_000_000

SONNET_5_5 = "claude-sonnet-5-5"
SONNET_5 = "claude-sonnet-5"
SIMULATED_MODEL = "simulated"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Dólares por millón de tokens de cada clase."""

    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float


PRICES: dict[str, ModelPrice] = {
    SONNET_5_5: ModelPrice(
        input=2.00, output=10.00, cache_write_5m=2.50, cache_write_1h=4.00, cache_read=0.10
    ),
    # El único modelo al que la API pasa una respuesta que Sonnet 5.5 rechaza
    # (`fallbacks: "default"`): sus tokens se cobran a su precio.
    SONNET_5: ModelPrice(
        input=2.00, output=10.00, cache_write_5m=2.50, cache_write_1h=4.00, cache_read=0.20
    ),
    SIMULATED_MODEL: ModelPrice(
        input=0.0, output=0.0, cache_write_5m=0.0, cache_write_1h=0.0, cache_read=0.0
    ),
}


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Los tokens de un intento. `input_tokens` es solo la entrada que no tocó la caché; el
    prompt entero es la suma de las tres clases de entrada."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def cache_creation_tokens(self) -> int:
        return self.cache_write_5m_tokens + self.cache_write_1h_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_write_5m_tokens + other.cache_write_5m_tokens,
            self.cache_write_1h_tokens + other.cache_write_1h_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
        )


@dataclass(frozen=True, slots=True)
class Attempt:
    """Una pasada de un modelo por la petición: la del modelo pedido y, si la rechazó y la
    API la pasó a otro, la del modelo de respaldo."""

    model: str
    usage: TokenUsage


def price_of(model: str) -> ModelPrice | None:
    """El precio de `model`, o `None` si no está en la tabla."""
    return PRICES.get(model)


def attempt_cost(attempt: Attempt) -> float | None:
    """Dólares de un intento, o `None` si su modelo no tiene precio en la tabla."""
    price = price_of(attempt.model)
    if price is None:
        return None
    usage = attempt.usage
    dollars = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_write_5m_tokens * price.cache_write_5m
        + usage.cache_write_1h_tokens * price.cache_write_1h
        + usage.cache_read_tokens * price.cache_read
    )
    return dollars / TOKENS_PER_PRICE_UNIT


def total_cost(attempts: Iterable[Attempt]) -> float | None:
    """La suma de los intentos, o `None` si alguno no tiene precio."""
    costs = [attempt_cost(attempt) for attempt in attempts]
    if any(cost is None for cost in costs):
        return None
    return sum(cost for cost in costs if cost is not None)


def total_usage(attempts: Iterable[Attempt]) -> TokenUsage:
    usage = TokenUsage()
    for attempt in attempts:
        usage = usage + attempt.usage
    return usage


def worst_case_cost(model: str, input_tokens: int, max_output_tokens: int) -> float:
    """Una cota de lo que puede costar una petición antes de hacerla: toda la entrada al
    precio más caro (la escritura en caché de una hora) y la salida entera.

    Raises:
        KeyError: si `model` no tiene precio.
    """
    price = PRICES[model]
    highest_input = max(price.input, price.cache_write_5m, price.cache_write_1h)
    dollars = input_tokens * highest_input + max_output_tokens * price.output
    return dollars / TOKENS_PER_PRICE_UNIT
