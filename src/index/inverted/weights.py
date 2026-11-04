"""El peso logarítmico de una frecuencia y la norma lnc de un documento (esquema SMART).

Vive junto al índice porque la norma se calcula al indexar y se guarda en la tabla de
documentos; el ranking usa el mismo peso para la consulta, así que los dos lados del
coseno salen de esta única fórmula.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


def log_frequency_weight(term_frequency: int) -> float:
    """`1 + log₁₀ tf`, el peso «l» de SMART; `tf` tiene que ser positivo.

    La base 10 es la de SMART y la del libro de Manning, Raghavan y Schütze. No es un
    detalle de escala: con `1 + log tf`, cambiar la base cambia el ranking del coseno.
    """
    return 1.0 + math.log10(term_frequency)


def lnc_norm(term_frequencies: Iterable[int]) -> float:
    """La norma euclídea del vector «ln» de un documento: `√Σ (1 + log₁₀ tf)²`.

    No depende del idf (la «n» de lnc), así que no cambia cuando el corpus crece. Un
    documento sin términos tiene norma 0.
    """
    return math.sqrt(math.fsum(map(_SQUARED_LOG_WEIGHTS.__getitem__, term_frequencies)))


class _SquaredLogWeights(dict[int, float]):
    """`(1 + log₁₀ tf)²` de cada `tf` que ya apareció.

    Casi todos los `tf` de una colección son 1, 2 o 3: con las 100 000 fichas de ESCI,
    buscar el cuadrado ya calculado en lugar de llamar a `log_frequency_weight` por cada
    término baja las normas de 0,49 a 0,10 s. `fsum` redondea una sola vez, al final, así
    que la norma es la misma bit a bit.
    """

    def __missing__(self, term_frequency: int) -> float:
        squared = self[term_frequency] = log_frequency_weight(term_frequency) ** 2
        return squared


_SQUARED_LOG_WEIGHTS = _SquaredLogWeights()
