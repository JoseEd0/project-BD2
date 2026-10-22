"""Figura del experimento de almacenamiento: heap file vs archivo secuencial."""

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
from benchmarks.plots.style import BLUE, ORANGE

HEAP = Technique("heap file", Style(BLUE, "o"))
SEQUENTIAL_FILE = Technique("archivo secuencial", Style(ORANGE, "s"))
STORAGE = (HEAP, SEQUENTIAL_FILE)


FIGURES: tuple[Figure, ...] = (
    Figure(
        filename="almacenamiento",
        report="almacenamiento",
        title="Heap file vs archivo secuencial",
        subtitle=f"Costo de cada operación según el tamaño de la tabla.\n{READING_GUIDE}",
        rows_label=TABLE_ROWS,
        columns=3,
        panels=(
            compare("inserción", "Inserción de todas las filas", TOTAL_TIME, STORAGE),
            compare("búsqueda por clave", "100 búsquedas por clave", TOTAL_TIME, STORAGE),
            compare("borrado", "100 borrados por clave", TOTAL_TIME, STORAGE),
            compare("espacio en disco", "Espacio en disco", DISK_SPACE, STORAGE, "kib"),
            compare(
                "reorganización",
                "Reorganización por umbral",
                TOTAL_TIME,
                (SEQUENTIAL_FILE,),
                note="El heap no se reorganiza:\nreutiliza los espacios libres.",
            ),
        ),
    ),
)
