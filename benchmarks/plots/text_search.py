"""Figuras del experimento de texto: TF-IDF + coseno, BM25 y GIN de PostgreSQL."""

from __future__ import annotations

from benchmarks.plots.model import (
    DISK_SPACE,
    MEMORY,
    QUERY_TIME,
    READING_GUIDE,
    TOTAL_TIME,
    BarFigure,
    BarGroup,
    BarPanel,
    Figure,
    Style,
    Technique,
    compare,
)
from benchmarks.plots.style import AQUA, BLUE, DARK_BLUE, ORANGE

TF_IDF = Technique("TF-IDF + coseno", Style(BLUE, "o"))
BM25 = Technique("BM25", Style(ORANGE, "s"))
GIN = Technique("GIN (PostgreSQL)", Style(AQUA, "^"))
INVERTED = Technique(
    "índice invertido (SPIMI)", Style(DARK_BLUE, "D"), "índice invertido (TF-IDF y BM25)"
)
RANKINGS = (TF_IDF, BM25, GIN)
INDEXES = (INVERTED, GIN)
DOCUMENTS = "número de documentos"
BUCKETS = (
    BarGroup("1 palabra", "1 palabra"),
    BarGroup("3 palabras", "3 palabras"),
    BarGroup("5+ palabras", "5+ palabras"),
    BarGroup("todas", "todas las consultas"),
)

FIGURES: tuple[Figure | BarFigure, ...] = (
    Figure(
        filename="texto-consultas",
        report="texto",
        title="Búsqueda de texto: TF-IDF + coseno vs BM25 vs GIN",
        subtitle=(
            "Tiempo por consulta de un top-10 según sus palabras (la pasada más rápida).\n"
            f"{READING_GUIDE}"
        ),
        rows_label=DOCUMENTS,
        columns=3,
        panels=(
            compare("1 palabra", "Consultas de 1 palabra", QUERY_TIME, RANKINGS),
            compare("3 palabras", "Consultas de 3 palabras", QUERY_TIME, RANKINGS),
            compare("5+ palabras", "Consultas de 5 o más", QUERY_TIME, RANKINGS),
        ),
    ),
    Figure(
        filename="texto-indice",
        report="texto",
        title="Índice de texto: construcción, espacio en disco y memoria",
        subtitle=(
            "Un solo índice invertido sirve a TF-IDF y BM25; construir incluye analizar el "
            "texto en los dos.\n"
            "La memoria es el pico de Python, medido con tracemalloc.\n"
            "Ejes logarítmicos. El valor escrito es el de 100 000 documentos."
        ),
        rows_label=DOCUMENTS,
        columns=4,
        panels=(
            compare("construcción", "Construcción del índice", TOTAL_TIME, INDEXES),
            compare("espacio del índice", "Espacio en disco", DISK_SPACE, INDEXES, field="kib"),
            compare(
                "memoria pico al construir",
                "Memoria al construir",
                MEMORY,
                (INVERTED,),
                field="kib",
            ),
            compare(
                "memoria pico al consultar",
                "Memoria al consultar",
                MEMORY,
                (TF_IDF, BM25),
                field="kib",
            ),
        ),
    ),
    BarFigure(
        filename="texto-relevancia",
        report="texto",
        title="Relevancia: TF-IDF + coseno vs BM25 vs GIN",
        subtitle=(
            "Media sobre las consultas de cada cubeta; relevante = producto calificado "
            "como exacto (E).\nMás alto es mejor. Con más de 10 exactos, ni un top-10 "
            "perfecto llega a Recall 1."
        ),
        sizes_label="documentos",
        panels=(
            BarPanel("Precision@{k}", "Precision@k", "precision_en_k", BUCKETS, RANKINGS),
            BarPanel("Recall@{k}", "Recall@k", "recall_en_k", BUCKETS, RANKINGS),
        ),
    ),
)
