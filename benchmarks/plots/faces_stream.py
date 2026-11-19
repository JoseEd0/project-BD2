"""Figura del reconocimiento en vivo: cuánto tarda un cuadro de punta a punta y en qué se va,
por el nginx o directo a la API, con y sin la caché de rastros."""

from __future__ import annotations

from benchmarks.faces_stream_benchmark import (
    REPORT_NAME,
    REST_STAGE,
    SERVER_TAIL_FIELD,
    TAIL_FIELD,
    WITH_CACHE,
    WITHOUT_CACHE,
)
from benchmarks.plots.model import (
    BreakdownFigure,
    BreakdownPanel,
    Measure,
    Segment,
    StackedBar,
)
from benchmarks.plots.style import AQUA, AXIS, BLUE, DARK_BLUE, LIGHT_BLUE, ORANGE

MILLISECONDS = Measure("tiempo por cuadro (ms)", "ms")
TARGETS = (("nginx", "por el nginx"), ("directo", "directo a la API"))
MODES = ((WITH_CACHE, "con rastros"), (WITHOUT_CACHE, "sin rastros"))
BARS = tuple(
    StackedBar(f"{target_label}, {mode_label}", target, mode)
    for target, target_label in TARGETS
    for mode, mode_label in MODES
)

FIGURES: tuple[BreakdownFigure, ...] = (
    BreakdownFigure(
        filename="rostros-tiempo-real",
        report=REPORT_NAME,
        title="Rostros en vivo: latencia de un cuadro por WebSocket",
        subtitle=(
            "Cuadros JPEG de 640 x 480 con una persona registrada, a 10 por segundo, en el "
            "mismo equipo.\nDetectar con YuNet, embeber con SFace, buscar en el HNSW del "
            "gestor. La meta es 100 ms. Más corto es mejor."
        ),
        panels=(
            BreakdownPanel(
                "Mediana de cada etapa",
                MILLISECONDS,
                BARS,
                (
                    Segment("decodificar", "decode_p50_ms", LIGHT_BLUE),
                    Segment("detectar", "detect_p50_ms", BLUE),
                    Segment("Redis", "track_p50_ms", AQUA),
                    Segment("embeber", "embed_p50_ms", DARK_BLUE),
                    Segment("buscar", "search_p50_ms", ORANGE),
                    Segment("red y espera", f"{REST_STAGE}_p50_ms", AXIS),
                ),
            ),
            BreakdownPanel(
                "En el servidor: p50 y p95",
                MILLISECONDS,
                BARS,
                (
                    Segment("p50", "servidor_p50_ms", BLUE),
                    Segment("hasta p95", SERVER_TAIL_FIELD, LIGHT_BLUE),
                ),
            ),
            BreakdownPanel(
                "En el celular: p50 y p95",
                MILLISECONDS,
                BARS,
                (
                    Segment("p50", "p50_ms", BLUE),
                    Segment("hasta p95", TAIL_FIELD, LIGHT_BLUE),
                ),
            ),
        ),
    ),
)
