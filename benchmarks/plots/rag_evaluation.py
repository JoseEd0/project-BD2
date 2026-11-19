"""Figuras de la evaluación del RAG: Recall@k y MRR de BM25, vectorial e híbrido por grupo de
preguntas, y Recall@10 según qué cuenta como el fragmento correcto."""

from __future__ import annotations

from benchmarks.plots.model import BarFigure, BarGroup, BarPanel, Style, Technique
from benchmarks.plots.style import AQUA, BLUE, ORANGE
from benchmarks.rag.evaluation_names import (
    ALL_GROUP,
    COURSE_GROUP,
    DB_GROUP,
    IR_GROUP,
    MRR_FIELD,
    NEIGHBOUR_SUFFIX,
    PAPER_SUFFIX,
    RECALL_FIELD,
    RETRIEVAL_REPORT,
)
from rag.search import SearchMode

MODES = (
    Technique(SearchMode.BM25.value, Style(ORANGE, "s"), "BM25"),
    Technique(SearchMode.VECTOR.value, Style(BLUE, "o"), "vectorial (e5 int8, HNSW)"),
    Technique(SearchMode.HYBRID.value, Style(AQUA, "^"), "híbrido (RRF)"),
)
GROUPS = (
    BarGroup("todas", ALL_GROUP),
    BarGroup("cs.DB", DB_GROUP),
    BarGroup("cs.IR", IR_GROUP),
    BarGroup("curso (es)", COURSE_GROUP),
)
RECALL_5 = RECALL_FIELD.format(k=5)
RECALL_10 = RECALL_FIELD.format(k=10)
CHUNKS = "fragmentos"

FIGURES: tuple[BarFigure, ...] = (
    BarFigure(
        filename="rag-evaluacion-recuperacion",
        report=RETRIEVAL_REPORT,
        title="RAG: recuperación de preguntas en español",
        subtitle=(
            "Correcto = el fragmento del que salió la pregunta. Los papers están en inglés;\n"
            "el material del curso, en español. Más alto es mejor."
        ),
        sizes_label=CHUNKS,
        panels=(
            BarPanel("Recall@5", "Recall@5", RECALL_5, GROUPS, MODES),
            BarPanel("Recall@10", "Recall@10", RECALL_10, GROUPS, MODES),
            BarPanel("MRR@10", "MRR@10", MRR_FIELD, GROUPS, MODES),
        ),
    ),
    BarFigure(
        filename="rag-evaluacion-relevancia",
        report=RETRIEVAL_REPORT,
        title="RAG: Recall@10 según qué cuenta como correcto",
        subtitle=(
            "Exacto: el fragmento de la pregunta. Vecino: también el anterior y el siguiente\n"
            "(se solapan). Paper: cualquier fragmento del mismo documento. Más alto es mejor."
        ),
        sizes_label=CHUNKS,
        panels=(
            BarPanel("Fragmento exacto", "Recall@10", RECALL_10, GROUPS, MODES),
            BarPanel("Exacto o vecino", "Recall@10", RECALL_10 + NEIGHBOUR_SUFFIX, GROUPS, MODES),
            BarPanel("Mismo paper", "Recall@10", RECALL_10 + PAPER_SUFFIX, GROUPS, MODES),
        ),
    ),
)
