"""Figuras del servicio de rostros: lo que tarda reconocer según el tamaño de la galería, y
los errores del conjunto abierto con cada umbral."""

from __future__ import annotations

from benchmarks.faces_service_benchmark import ENROLLED, IDENTIFY, OPEN_SET, REPORT_NAME, STRANGER
from benchmarks.plots.model import (
    BarFigure,
    BarGroup,
    BarPanel,
    Figure,
    Measure,
    Style,
    Technique,
    compare,
)
from benchmarks.plots.style import AQUA, BLUE, DARK_BLUE, LIGHT_BLUE, ORANGE

QUERIES = (
    Technique(ENROLLED, Style(BLUE, "o"), "foto de una persona registrada"),
    Technique(STRANGER, Style(ORANGE, "s"), "foto de una desconocida"),
)
THRESHOLDS = (
    Technique("umbral 0.363", Style(LIGHT_BLUE, "o"), "0,363 (OpenCV)"),
    Technique("umbral 0.4", Style(BLUE, "s"), "0,40"),
    Technique("umbral 0.45", Style(DARK_BLUE, "^"), "0,45 (servicio)"),
    Technique("umbral 0.5", Style(AQUA, "D"), "0,50"),
)
PER_QUERY = Measure("tiempo por petición (ms)", "ms")
GALLERY = (BarGroup("", OPEN_SET),)

FIGURES: tuple[Figure | BarFigure, ...] = (
    Figure(
        filename="rostros-servicio-reconocer",
        report=REPORT_NAME,
        title="Rostros: reconocer una foto por el API",
        subtitle=(
            "200 fotos de LFW por tamaño; el servicio contra el gestor en el mismo proceso.\n"
            "Los puntos de 250 y 500 se midieron con el equipo cargado por otros procesos.\n"
            "Ejes logarítmicos. Más abajo es mejor."
        ),
        rows_label="personas registradas",
        columns=3,
        legend_columns=2,
        panels=(
            compare(IDENTIFY, "Petición completa, p50", PER_QUERY, QUERIES),
            compare(IDENTIFY, "Petición completa, p95", PER_QUERY, QUERIES, field="p95_ms"),
            compare(
                IDENTIFY,
                "Solo la galería (HNSW), p50",
                PER_QUERY,
                QUERIES,
                field="galeria_p50_ms",
            ),
        ),
    ),
    BarFigure(
        filename="rostros-servicio-conjunto-abierto",
        report=REPORT_NAME,
        title="Rostros: errores del conjunto abierto en LFW",
        subtitle=(
            "Por umbral de coseno. Desconocidas: fotos de personas con una sola foto en LFW;\n"
            "registradas: una foto que no se usó al registrar. Más bajo es mejor."
        ),
        sizes_label="personas",
        panels=(
            BarPanel("Desconocidas aceptadas", "FPIR", "fpir", GALLERY, THRESHOLDS),
            BarPanel("Registradas perdidas", "FNIR", "fnir", GALLERY, THRESHOLDS),
        ),
    ),
)
