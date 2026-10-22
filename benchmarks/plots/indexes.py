"""Figura del experimento de indexación: B+ agrupado, B+ no agrupado y hash extendible."""

from __future__ import annotations

from benchmarks.plots.model import (
    DISK_SPACE,
    READING_GUIDE,
    TABLE_ROWS,
    TOTAL_TIME,
    Figure,
    Style,
    Technique,
    compare,
)
from benchmarks.plots.style import AQUA, BLUE, ORANGE

CLUSTERED = Technique("B+ agrupado", Style(BLUE, "o"))
UNCLUSTERED = Technique("B+ no agrupado", Style(ORANGE, "s"))
HASH = Technique("hash extendible", Style(AQUA, "^"))
INDEXES = (CLUSTERED, UNCLUSTERED, HASH)
ORDERED_INDEXES = (CLUSTERED, UNCLUSTERED)


FIGURES: tuple[Figure, ...] = (
    Figure(
        filename="indices",
        report="indices",
        title="B+ agrupado vs B+ no agrupado vs hash extendible",
        subtitle=f"Costo de cada operación según el tamaño de la tabla.\n{READING_GUIDE}",
        rows_label=TABLE_ROWS,
        columns=3,
        panels=(
            compare("construcción", "Construcción del índice", TOTAL_TIME, INDEXES),
            compare("igualdad", "100 búsquedas por igualdad", TOTAL_TIME, INDEXES),
            compare(
                "rango",
                "100 búsquedas por rango",
                TOTAL_TIME,
                ORDERED_INDEXES,
                note="El hash no soporta\nbúsquedas por rango.",
            ),
            compare(
                "recorrido ordenado",
                "Recorrido ordenado de la tabla",
                TOTAL_TIME,
                ORDERED_INDEXES,
                note="El hash no mantiene\nlas claves en orden.",
            ),
            compare("espacio", "Espacio en disco", DISK_SPACE, INDEXES, field="kib"),
            compare(
                "inserciones/eliminaciones",
                "Eliminar y reinsertar el 10 %",
                TOTAL_TIME,
                INDEXES,
            ),
        ),
    ),
)
