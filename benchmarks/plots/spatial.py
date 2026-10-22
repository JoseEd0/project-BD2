"""Figuras del experimento espacial: búsqueda secuencial, R-Tree y GiST de PostgreSQL."""

from __future__ import annotations

from benchmarks.plots.model import (
    DISK_SPACE,
    MEMORY,
    NODES,
    QUERY_TIME,
    READING_GUIDE,
    TOTAL_TIME,
    Figure,
    Line,
    Panel,
    Style,
    Technique,
    compare,
)
from benchmarks.plots.style import AQUA, BLUE, DARK_BLUE, LIGHT_BLUE, MUTED_INK, ORANGE

SCAN = Technique("búsqueda secuencial", Style(BLUE, "o"))
RTREE = Technique("R-Tree", Style(ORANGE, "s"))
GIST = Technique("GiST (PostgreSQL)", Style(AQUA, "^"))
RTREE_INSERTED = Technique(
    "R-Tree (inserción)", Style(ORANGE, "D", dashed=True), "R-Tree, inserción uno a uno"
)
SPATIAL = (SCAN, RTREE, GIST)
SPATIAL_INDEXES = (RTREE, RTREE_INSERTED, GIST)
TABLE_POINTS = "número de puntos de la tabla"
TREE_NODES = "nodos_del_arbol"
TREE_SIZE = "total del árbol"


def _visited(label: str, operation: str, color: str, marker: str) -> Line:
    return Line(label, Style(color, marker), "R-Tree", operation, "nodos_visitados_promedio")


def _whole_tree(operation: str) -> Line:
    return Line(TREE_SIZE, Style(MUTED_INK, "x", dashed=True), "R-Tree", operation, TREE_NODES)


FIGURES: tuple[Figure, ...] = (
    Figure(
        filename="espacial-radio",
        report="espacial",
        title="Consultas por rango: búsqueda secuencial vs R-Tree vs GiST",
        subtitle=f"Tiempo promedio por consulta (100 consultas).\n{READING_GUIDE}",
        rows_label=TABLE_POINTS,
        columns=3,
        panels=(
            compare("radio 1 km", "Radio de 1 km", QUERY_TIME, SPATIAL),
            compare("radio 5 km", "Radio de 5 km", QUERY_TIME, SPATIAL),
            compare("radio 10 km", "Radio de 10 km", QUERY_TIME, SPATIAL),
        ),
    ),
    Figure(
        filename="espacial-knn",
        report="espacial",
        title="Consultas k-NN: búsqueda secuencial vs R-Tree vs GiST",
        subtitle=f"Tiempo promedio por consulta (100 consultas).\n{READING_GUIDE}",
        rows_label=TABLE_POINTS,
        columns=3,
        panels=(
            compare("k-NN k=10", "10 vecinos más cercanos", QUERY_TIME, SPATIAL),
            compare("k-NN k=50", "50 vecinos más cercanos", QUERY_TIME, SPATIAL),
            compare("k-NN k=100", "100 vecinos más cercanos", QUERY_TIME, SPATIAL),
        ),
    ),
    Figure(
        filename="espacial-indice",
        report="espacial",
        title="Índice espacial: construcción, espacio en disco y memoria",
        subtitle=f"Costo de construir, almacenar y consultar el índice.\n{READING_GUIDE}",
        rows_label=TABLE_POINTS,
        columns=3,
        panels=(
            compare("construcción", "Construcción del índice", TOTAL_TIME, SPATIAL_INDEXES),
            compare(
                "espacio del índice",
                "Espacio en disco del índice",
                DISK_SPACE,
                SPATIAL_INDEXES,
                field="kib",
            ),
            compare(
                "memoria pico",
                "Memoria pico al consultar",
                MEMORY,
                (SCAN, RTREE),
                field="kib",
                note="Solo el gestor propio\n(medido con tracemalloc).",
            ),
        ),
    ),
    Figure(
        filename="espacial-nodos",
        report="espacial",
        title="Nodos del R-Tree visitados por consulta",
        subtitle=(
            "Promedio de 100 consultas, comparado con el total de nodos del árbol.\n"
            "Ejes logarítmicos. El valor escrito es el de la tabla más grande (100 000).\n"
            "La primera fila de la leyenda es del panel izquierdo; la segunda, del derecho."
        ),
        rows_label=TABLE_POINTS,
        columns=2,
        panels=(
            Panel(
                "Consultas por rango",
                NODES,
                (
                    _visited("radio 1 km", "radio 1 km", LIGHT_BLUE, "o"),
                    _visited("radio 5 km", "radio 5 km", BLUE, "s"),
                    _visited("radio 10 km", "radio 10 km", DARK_BLUE, "^"),
                    _whole_tree("radio 1 km"),
                ),
            ),
            Panel(
                "Consultas k-NN",
                NODES,
                (
                    _visited("k = 10", "k-NN k=10", LIGHT_BLUE, "o"),
                    _visited("k = 50", "k-NN k=50", BLUE, "s"),
                    _visited("k = 100", "k-NN k=100", DARK_BLUE, "^"),
                    _whole_tree("k-NN k=10"),
                ),
            ),
        ),
    ),
)
