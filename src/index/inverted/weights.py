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
    return math.sqrt(math.fsum(log_frequency_weight(tf) ** 2 for tf in term_frequencies))
