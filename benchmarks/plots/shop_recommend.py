"""Figura del recomendador de la tienda: compras conjuntas al vuelo frente a materializadas,
la latencia de cada señal y la de la recomendación completa con y sin caché."""

from __future__ import annotations

from benchmarks.plots.model import (
    BreakdownFigure,
    BreakdownPanel,
    Measure,
    Segment,
    StackedBar,
)
from benchmarks.plots.style import AQUA, BLUE
from benchmarks.shop_recommend_benchmark import (
    COLD,
    COPURCHASE_STRATEGY,
    FUSED,
    MATERIALIZED,
    MEDIAN_FIELD,
    ON_THE_FLY_HISTORY,
    ON_THE_FLY_SEQUENTIAL,
    P95_EXTRA_FIELD,
    REPORT_NAME,
    SECONDS_FIELD,
    WARM,
)

SECONDS = Measure("mediana por consulta (s)", "s")
MILLISECONDS = Measure("milisegundos (p50 y hasta p95)", "ms")
PERCENTILES = (
    Segment("p50", MEDIAN_FIELD, BLUE),
    Segment("de p50 a p95", P95_EXTRA_FIELD, AQUA),
)

FIGURES: tuple[BreakdownFigure, ...] = (
    BreakdownFigure(
        filename="tienda-recomendaciones",
        report=REPORT_NAME,
        title="Tienda: recomendar con cuatro señales del gestor",
        subtitle=(
            "Todo el dataset (24 587 productos, 511 836 compras) por HTTP, con Redis de verdad.\n"
            "Cada barra llega al p95; el tramo oscuro es la mediana. Medido con el equipo cargado."
        ),
        panels=(
            BreakdownPanel(
                "Compras conjuntas de un producto",
                SECONDS,
                (
                    StackedBar("al vuelo, compras (secuencial)", ON_THE_FLY_SEQUENTIAL,
                               COPURCHASE_STRATEGY),
                    StackedBar("al vuelo, historial (hash)", ON_THE_FLY_HISTORY,
                               COPURCHASE_STRATEGY),
                    StackedBar("materializada", MATERIALIZED, COPURCHASE_STRATEGY),
                ),
                (Segment("mediana", SECONDS_FIELD, BLUE),),
            ),
            BreakdownPanel(
                "Cada señal, caché fría",
                MILLISECONDS,
                (
                    StackedBar("compras conjuntas", "compras", COLD),
                    StackedBar("visual (CLIP)", "visual", COLD),
                    StackedBar("texto (BM25)", "texto", COLD),
                    StackedBar("cercanía (hash + R-Tree)", "cercania", COLD),
                ),
                PERCENTILES,
            ),
            BreakdownPanel(
                "Recomendación completa",
                MILLISECONDS,
                (
                    StackedBar("caché fría", FUSED, COLD),
                    StackedBar("caché caliente (Redis)", FUSED, WARM),
                ),
                PERCENTILES,
            ),
        ),
    ),
)
