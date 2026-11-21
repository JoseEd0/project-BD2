"""Figura de la prueba de carga: peticiones por segundo, p95 y CPU del gestor de cada ruta
según las réplicas de la API de su app."""

from __future__ import annotations

from benchmarks.load_benchmark import (
    COPYRIGHT_DETECTION,
    COPYRIGHT_SONG,
    FACES_IDENTIFY,
    OPERATION,
    PAPERS_SEARCH,
    REPORT_NAME,
    SHOP_COLD,
    SHOP_WARM,
)
from benchmarks.plots.model import Axis, Series, Style, XYFigure, XYPanel
from benchmarks.plots.style import AQUA, BLUE, DARK_BLUE, LIGHT_BLUE, MUTED_INK, ORANGE

REPLICAS = Axis("réplicas de la API de la app", "replicas", ticks=(1.0, 2.0, 4.0))
SERIES = tuple(
    Series(scenario.label, style, scenario.label, OPERATION)
    for scenario, style in (
        (SHOP_COLD, Style(BLUE, "o")),
        (SHOP_WARM, Style(LIGHT_BLUE, "s")),
        (PAPERS_SEARCH, Style(ORANGE, "^")),
        (COPYRIGHT_SONG, Style(AQUA, "D")),
        (COPYRIGHT_DETECTION, Style(DARK_BLUE, "v")),
        (FACES_IDENTIFY, Style(MUTED_INK, "P")),
    )
)

FIGURES: tuple[XYFigure, ...] = (
    XYFigure(
        filename="carga-replicas",
        report=REPORT_NAME,
        title="Prueba de carga: réplicas de cada API frente a un solo gestor",
        subtitle=(
            "k6 con 32 usuarios sin pausa durante 30 s por el nginx de cada app, en el mismo "
            "equipo. Más respuestas y menos latencia es mejor.\nUna línea plana en respuestas "
            "por segundo es un límite que no está en la API: el gestor o la CPU del equipo."
        ),
        columns=3,
        legend_columns=3,
        panels=(
            XYPanel(
                "Respuestas por segundo",
                REPLICAS,
                Axis("respuestas 2xx por segundo (req/s)", "rps_correctas", logarithmic=True),
                SERIES,
            ),
            XYPanel(
                "Latencia p95",
                REPLICAS,
                Axis("latencia p95 (ms)", "p95_ms", logarithmic=True),
                SERIES,
            ),
            XYPanel(
                "CPU del gestor",
                REPLICAS,
                Axis("núcleos ocupados por el gestor", "cpu_gestor_nucleos"),
                SERIES,
            ),
        ),
    ),
)
