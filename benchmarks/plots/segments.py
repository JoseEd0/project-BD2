"""Figuras del experimento de segmentos: la consulta por segmentos de una canción contra un
SIMILAR_TO por ventana, y los índices sobre tramos de pistas de FMA."""

from __future__ import annotations

from benchmarks.plots.model import TOTAL_TIME, Figure, Measure, Style, Technique, compare
from benchmarks.plots.style import AQUA, BLUE, DARK_BLUE, LIGHT_BLUE, MUTED_INK, ORANGE
from benchmarks.segments.names import (
    DECODE,
    EXTRACT,
    EXTRACTION,
    FRAGMENTS,
    GROUPED,
    INDEX_BUILD,
    PER_ROW,
    REPORT,
    SEARCH,
    SEGMENT_QUERY,
    SEGMENT_STAGES,
    SEGMENTS,
    SEPARATE,
    WHOLE_FILES,
)

BATCHED = Technique(SEGMENTS, Style(ORANGE, "s"), "POST /media/segments (1 petición)")
ONE_BY_ONE = Technique(SEPARATE, Style(BLUE, "o"), "un SIMILAR_TO por ventana")
STAGES = (
    Technique(DECODE, Style(DARK_BLUE, "^"), "decodificar, una vez"),
    Technique(EXTRACT, Style(LIGHT_BLUE, "D"), "MFCC y TF-IDF de las ventanas"),
    Technique(SEARCH, Style(AQUA, "v"), "búsqueda HNSW en lote"),
)
WHOLE = Technique(WHOLE_FILES, Style(MUTED_INK, "x", dashed=True), "pistas enteras")
FRAGMENTED = Technique(FRAGMENTS, Style(ORANGE, "s"), "tramos de 10 s cada 5 s")
READ_EACH = Technique(PER_ROW, Style(BLUE, "o"), "cada tramo lee su rango")
DECODE_ONCE = Technique(GROUPED, Style(ORANGE, "s"), "una decodificación por pista")
PER_WINDOW = Measure("tiempo por ventana (ms)", "ms")
GUIDE = "Ejes logarítmicos. Más abajo es mejor."


FIGURES: tuple[Figure, ...] = (
    Figure(
        filename="segmentos-consulta",
        report=REPORT,
        title="Consultar una canción por segmentos",
        subtitle=(
            "Ventanas de 10 s cada 5 s, k = 5, contra 898 tramos de 200 pistas de FMA con HNSW; "
            "mediana de 3.\n"
            f"{GUIDE}"
        ),
        rows_label="duración de la canción (s)",
        columns=3,
        panels=(
            compare(SEGMENT_QUERY, "La canción entera", TOTAL_TIME, (BATCHED, ONE_BY_ONE)),
            compare(
                SEGMENT_QUERY,
                "Por ventana",
                PER_WINDOW,
                (BATCHED, ONE_BY_ONE),
                field="ms_por_ventana",
            ),
            compare(SEGMENT_STAGES, "Etapas de /media/segments", TOTAL_TIME, STAGES),
        ),
        legend_columns=3,
    ),
    Figure(
        filename="segmentos-indice",
        report=REPORT,
        title="Indexar tramos de pistas",
        subtitle=(
            "Tramos de 10 s cada 5 s de pistas de FMA,\n"
            "con un proceso de extracción.\n"
            f"{GUIDE}"
        ),
        rows_label="pistas del catálogo",
        columns=2,
        legend_columns=2,
        panels=(
            compare(INDEX_BUILD, "CREATE INDEX", TOTAL_TIME, (WHOLE, FRAGMENTED)),
            compare(EXTRACTION, "Extraer los tramos", TOTAL_TIME, (READ_EACH, DECODE_ONCE)),
        ),
    ),
)
