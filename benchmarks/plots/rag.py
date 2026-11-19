"""Figuras de la ingesta del RAG: embeddings por segundo según los hilos, fp32 contra int8,
y lo que tarda cada etapa de la carga según los fragmentos."""

from __future__ import annotations

from benchmarks.plots.model import TOTAL_TIME, Figure, Measure, Style, Technique, compare
from benchmarks.plots.style import AQUA, BLUE, MUTED_INK, ORANGE
from benchmarks.rag.names import (
    DEFERRED_LOAD,
    EMBEDDING_OPERATION,
    HNSW_BUILD,
    INVERTED_BUILD,
    LOAD_ONLY,
    REPORT,
    STAGES_OPERATION,
    THROUGHPUT_FIELD,
)
from rag.models import ModelVariant

THROUGHPUT = Measure("rendimiento (fragmentos/s)", "frag./s")
VARIANTS = (
    Technique(ModelVariant.FP32.value, Style(BLUE, "o"), "fp32"),
    Technique(ModelVariant.INT8.value, Style(ORANGE, "s"), "int8 (cuantizado)"),
)
STAGES = (
    Technique(LOAD_ONLY, Style(BLUE, "o"), "carga sin índices"),
    Technique(INVERTED_BUILD, Style(ORANGE, "s"), "índice invertido, después"),
    Technique(HNSW_BUILD, Style(AQUA, "^"), "HNSW, después"),
    Technique(DEFERRED_LOAD, Style(MUTED_INK, "x", dashed=True), "carga con índices diferidos"),
)

FIGURES: tuple[Figure, ...] = (
    Figure(
        filename="rag-embeddings",
        report=REPORT,
        title="RAG: embeddings en CPU",
        subtitle=(
            "multilingual-e5-small en onnxruntime,\n"
            "muestra del corpus, lotes de 32.\n"
            "Ejes logarítmicos. Más arriba es mejor."
        ),
        rows_label="hilos de onnxruntime",
        columns=1,
        legend_columns=1,
        panels=(
            compare(
                EMBEDDING_OPERATION,
                "Fragmentos por segundo",
                THROUGHPUT,
                VARIANTS,
                field=THROUGHPUT_FIELD,
            ),
        ),
    ),
    Figure(
        filename="rag-ingesta",
        report=REPORT,
        title="RAG: la carga en el gestor",
        subtitle=(
            "Los primeros N fragmentos, por HTTP,\n"
            "con su embedding ya calculado.\n"
            "Ejes logarítmicos. Más abajo es mejor."
        ),
        rows_label="fragmentos cargados",
        columns=1,
        legend_columns=1,
        panels=(compare(STAGES_OPERATION, "Tiempo de cada etapa", TOTAL_TIME, STAGES),),
    ),
)
