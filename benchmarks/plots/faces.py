"""Figura del experimento de rostros: lo que tarda cada etapa según el tamaño de la foto."""

from __future__ import annotations

from benchmarks.faces_benchmark import (
    DECODE_STAGE,
    DETECT_STAGE,
    EMBED_STAGE,
    LATENCY,
    REPORT_NAME,
    TOTAL,
)
from benchmarks.plots.model import Figure, Measure, Style, Technique, compare
from benchmarks.plots.style import AQUA, BLUE, MUTED_INK, ORANGE

STAGES = (
    Technique(DECODE_STAGE, Style(BLUE, "o")),
    Technique(DETECT_STAGE, Style(ORANGE, "s")),
    Technique(EMBED_STAGE, Style(AQUA, "^")),
    Technique(TOTAL, Style(MUTED_INK, "x", dashed=True), "total de la foto"),
)
PER_PHOTO = Measure("tiempo por foto (ms)", "ms")

FIGURES: tuple[Figure, ...] = (
    Figure(
        filename="rostros-latencia",
        report=REPORT_NAME,
        title="Rostros: tiempo de cada etapa por foto",
        subtitle=(
            "250 px: las 13 233 fotos de LFW; el resto, 500 de ellas ampliadas. Un hilo de CPU.\n"
            "Ejes logarítmicos. Más abajo es mejor. El valor rotulado es el de 4 000 px."
        ),
        rows_label="lado mayor de la foto (px)",
        columns=2,
        legend_columns=2,
        panels=(
            compare(LATENCY, "Mediana (p50)", PER_PHOTO, STAGES),
            compare(LATENCY, "Percentil 95", PER_PHOTO, STAGES, field="p95_ms"),
        ),
    ),
)
