"""Figuras de la detección de copyright: lo que tarda `POST /detections` según la duración de
la subida, sus etapas, y los puntajes de los casos de control frente al umbral."""

from __future__ import annotations

from benchmarks.copyright_detection_benchmark import (
    LATENCY,
    LONG_FRAGMENT,
    MAXIMUM,
    MEDIAN,
    MINIMUM,
    OTHER_SONGS,
    P50,
    P95,
    REPORT_NAME,
    SHORT_FRAGMENT,
    STAGES,
    UNCATALOGUED,
    WHOLE_COPY,
)
from benchmarks.plots.model import (
    TOTAL_TIME,
    BarFigure,
    BarGroup,
    BarPanel,
    Figure,
    Measure,
    Style,
    Technique,
    compare,
)
from benchmarks.plots.style import AQUA, BLUE, DARK_BLUE, LIGHT_BLUE, MUTED_INK, ORANGE
from songcheck.detection_api import (
    ENGINE_DECODE_STAGE,
    ENGINE_EXTRACTION_STAGE,
    ENGINE_SEARCH_STAGE,
    LOUDNESS_STAGE,
    SONGS_STAGE,
    VOTING_STAGE,
)

PERCENTILES = (
    Technique(P50, Style(BLUE, "o"), "p50"),
    Technique(P95, Style(ORANGE, "s"), "p95"),
)
STAGE_LINES = (
    Technique(LOUDNESS_STAGE, Style(LIGHT_BLUE, "D"), "nivel por ventana (servicio)"),
    Technique(ENGINE_DECODE_STAGE, Style(DARK_BLUE, "^"), "decodificar (gestor)"),
    Technique(ENGINE_EXTRACTION_STAGE, Style(AQUA, "v"), "MFCC y TF-IDF (gestor)"),
    Technique(ENGINE_SEARCH_STAGE, Style(BLUE, "o"), "HNSW en lote (gestor)"),
    Technique(VOTING_STAGE, Style(ORANGE, "s"), "votación por desfase"),
    Technique(SONGS_STAGE, Style(MUTED_INK, "x", dashed=True), "canciones (SQL)"),
)
SUMMARY = (
    Technique(MINIMUM, Style(LIGHT_BLUE, "o")),
    Technique(MEDIAN, Style(BLUE, "s")),
    Technique(MAXIMUM, Style(DARK_BLUE, "^")),
)
COPIES = (
    BarGroup("copia entera", WHOLE_COPY),
    BarGroup("tramo de 15 s", LONG_FRAGMENT),
    BarGroup("tramo de 7,5 s", SHORT_FRAGMENT),
)
OTHERS = (
    BarGroup("no catalogada", UNCATALOGUED),
    BarGroup("otras en las copias", OTHER_SONGS),
)
PER_WINDOW = Measure("tiempo por ventana (ms)", "ms")

FIGURES: tuple[Figure | BarFigure, ...] = (
    Figure(
        filename="copyright-deteccion",
        report=REPORT_NAME,
        title="Copyright: detectar copias por el API",
        subtitle=(
            "Subidas en MP3 a 44,1 kHz contra 83 290 tramos de 7 954 canciones (HNSW coseno);\n"
            "10 subidas por duración, el gestor en otro proceso. Ejes logarítmicos."
        ),
        rows_label="segundos de audio subido",
        columns=2,
        legend_columns=2,
        panels=(
            compare(LATENCY, "POST /detections", TOTAL_TIME, PERCENTILES),
            compare(LATENCY, "Por ventana", PER_WINDOW, PERCENTILES, field="ms_por_ventana"),
        ),
    ),
    Figure(
        filename="copyright-deteccion-etapas",
        report=REPORT_NAME,
        title="Copyright: etapas de una detección",
        subtitle=(
            "Mediana de 10 subidas por duración, de Server-Timing.\n"
            "Las del gestor cubren todas las ventanas a la vez. Ejes logarítmicos."
        ),
        rows_label="segundos de audio subido",
        columns=2,
        legend_columns=2,
        panels=(
            compare(STAGES, "Servicio y gestor", TOTAL_TIME, STAGE_LINES[:3]),
            compare(STAGES, "Búsqueda, votación y SQL", TOTAL_TIME, STAGE_LINES[3:]),
        ),
    ),
    BarFigure(
        filename="copyright-deteccion-puntajes",
        report=REPORT_NAME,
        title="Copyright: puntajes de los casos de control",
        subtitle=(
            "20 casos de cada tipo; puntaje = similitud media por cobertura. Umbral provisional: "
            "0,45.\nIzquierda, el de la canción copiada; derecha, el mejor de cualquier otra."
        ),
        sizes_label="canciones del catálogo",
        panels=(
            BarPanel("La canción copiada", "puntaje", "puntaje", COPIES, SUMMARY),
            BarPanel("Canciones que no se copiaron", "puntaje", "puntaje", OTHERS, SUMMARY),
        ),
    ),
)
