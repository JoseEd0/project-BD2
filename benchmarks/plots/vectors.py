"""Figuras del experimento de columnas VECTOR(n): HNSW e IVF por SQL contra la búsqueda
exacta, sobre embeddings sintéticos de 384 dimensiones."""

from __future__ import annotations

from benchmarks.plots.model import (
    DISK_SPACE,
    READING_GUIDE,
    TOTAL_TIME,
    BarFigure,
    BarGroup,
    BarPanel,
    CurveFigure,
    CurvePanel,
    Figure,
    Measure,
    Style,
    Technique,
    compare,
)
from benchmarks.plots.style import AQUA, BLUE, MUTED_INK, ORANGE
from benchmarks.vectors.names import (
    BUILD,
    CURVE,
    FILTERED_QUERY,
    LOAD,
    QUERY,
    REPORT,
    SPACE,
    SQL_EXACT,
    SQL_HNSW,
    SQL_IVF,
    TABLE_LOAD,
)

EXACT = Technique(SQL_EXACT, Style(MUTED_INK, "x", dashed=True), "búsqueda exacta")
IVF = Technique(SQL_IVF, Style(BLUE, "o"), "IVF")
HNSW = Technique(SQL_HNSW, Style(ORANGE, "s"), "HNSW")
TABLE = Technique(TABLE_LOAD, Style(AQUA, "^"), "cargar la tabla")
INDEXES = (IVF, HNSW)
WITH_EXACT = (EXACT, *INDEXES)
VECTORS = "número de vectores de la tabla"
SIZES_LABEL = "vectores"
QUERY_MEDIAN = Measure("mediana por consulta (ms)", "ms")
QUERY_TAIL = Measure("percentil 95 por consulta (ms)", "ms")


FIGURES: tuple[Figure | BarFigure | CurveFigure, ...] = (
    Figure(
        filename="vectores-sql",
        report=REPORT,
        title="HNSW e IVF sobre una columna VECTOR(384), por SQL",
        subtitle=(
            "SIMILAR_TO(emb, $1, k = 10) con el esfuerzo por omisión; la exacta es la misma "
            "consulta con otra métrica.\n"
            f"{READING_GUIDE}"
        ),
        rows_label=VECTORS,
        columns=3,
        panels=(
            compare(
                BUILD,
                "Construcción",
                TOTAL_TIME,
                WITH_EXACT,
                note="La exacta se arma en\nsu primera consulta.",
            ),
            compare(LOAD, "Cargar la tabla", TOTAL_TIME, (TABLE,)),
            compare(SPACE, "Espacio del índice en disco", DISK_SPACE, INDEXES, field="kib"),
            compare(QUERY, "Consulta (p50)", QUERY_MEDIAN, WITH_EXACT),
            compare(QUERY, "Consulta (p95)", QUERY_TAIL, WITH_EXACT, field="p95_ms"),
            compare(FILTERED_QUERY, "Filtro del 10 % (p50)", QUERY_MEDIAN, INDEXES),
        ),
    ),
    BarFigure(
        filename="vectores-recall",
        report=REPORT,
        title="Recall@10 contra la exacta, por SQL",
        subtitle=(
            "Media de 500 consultas (100 con filtro), esfuerzo\n"
            "por omisión. 1 es dar los 10 vecinos de la exacta."
        ),
        sizes_label=SIZES_LABEL,
        panels=(
            BarPanel(
                "Sin filtro", "Recall@10", "recall_en_k", (BarGroup("", QUERY),), WITH_EXACT
            ),
            BarPanel(
                "Filtro del 10 %",
                "Recall@10",
                "recall_en_k",
                (BarGroup("", FILTERED_QUERY),),
                INDEXES,
            ),
        ),
    ),
    CurveFigure(
        filename="vectores-curvas",
        report=REPORT,
        title="Recall@10 contra latencia",
        subtitle=(
            "Por SQL. IVF: probes de 1 a 128;\nHNSW: ef_search de 10 a 320.\n"
            "El anillo marca el valor por omisión.\nArriba a la izquierda es mejor."
        ),
        columns=1,
        x_axis="mediana por consulta (ms)",
        y_axis="Recall@10",
        field="recall_en_k",
        order_field="esfuerzo",
        default_field="por_omision",
        sizes_label=SIZES_LABEL,
        panels=(CurvePanel("384 dim.", CURVE, INDEXES),),
    ),
)
