"""Figura de la carga masiva: filas por segundo por HTTP, NDJSON contra INSERT con
parámetros, y cuándo conviene construir un índice."""

from __future__ import annotations

from benchmarks.ingest.names import (
    HNSW_AFTER,
    HNSW_EMPTY,
    HNSW_ROWS,
    INSERT_BATCH,
    INSERT_ROW,
    NDJSON,
    PLAIN,
    REPORT,
    RTREE_AFTER,
    RTREE_EMPTY,
    RTREE_ROWS,
    THROUGHPUT_FIELD,
    VECTOR,
)
from benchmarks.plots.model import (
    TABLE_ROWS,
    Figure,
    Line,
    Measure,
    Panel,
    Style,
    Technique,
    compare,
)
from benchmarks.plots.style import AQUA, BLUE, MUTED_INK, ORANGE

THROUGHPUT = Measure("rendimiento (filas/s)", "filas/s")
ONE_ROW = Technique(INSERT_ROW, Style(MUTED_INK, "x", dashed=True), "INSERT, una fila por petición")
BATCHED = Technique(INSERT_BATCH, Style(BLUE, "o"), "INSERT, un lote por petición")
BULK = Technique(NDJSON, Style(ORANGE, "s"), "NDJSON por lotes")
TECHNIQUES = (ONE_ROW, BATCHED, BULK)
AT_THE_END = Style(ORANGE, "s")
ROW_BY_ROW = Style(BLUE, "o")
AFTERWARDS = Style(AQUA, "^")


def _when_built(title: str, empty: str, with_rows: str, after: str) -> Panel:
    """Las tres cargas NDJSON de una tabla con índice, según cuándo se construye."""
    return Panel(
        title,
        THROUGHPUT,
        (
            Line(
                "antes, tabla vacía: se construye al final",
                AT_THE_END,
                NDJSON,
                empty,
                THROUGHPUT_FIELD,
            ),
            Line(
                "antes, tabla con filas: fila a fila",
                ROW_BY_ROW,
                NDJSON,
                with_rows,
                THROUGHPUT_FIELD,
            ),
            Line("después, con CREATE INDEX", AFTERWARDS, NDJSON, after, THROUGHPUT_FIELD),
        ),
    )


FIGURES: tuple[Figure, ...] = (
    Figure(
        filename="ingesta",
        report=REPORT,
        title="Carga masiva por HTTP: filas por segundo",
        subtitle=(
            "uvicorn en otro proceso; lotes de 1 000 filas, los mismos para el INSERT por lotes "
            "y el NDJSON.\nEjes logarítmicos. Más arriba es mejor. El valor al final de cada "
            "línea es el de 50 000 filas (10 000 en el INSERT de una fila)."
        ),
        rows_label=TABLE_ROWS,
        columns=3,
        legend_columns=3,
        panels=(
            compare(PLAIN, "Sin vector", THROUGHPUT, TECHNIQUES, field=THROUGHPUT_FIELD),
            compare(
                VECTOR, "VECTOR(384), sin índice", THROUGHPUT, TECHNIQUES, field=THROUGHPUT_FIELD
            ),
            compare(
                HNSW_EMPTY,
                "Con HNSW, tabla vacía",
                THROUGHPUT,
                TECHNIQUES,
                field=THROUGHPUT_FIELD,
            ),
            _when_built("NDJSON: cuándo, el HNSW", HNSW_EMPTY, HNSW_ROWS, HNSW_AFTER),
            _when_built("NDJSON: cuándo, el R-Tree", RTREE_EMPTY, RTREE_ROWS, RTREE_AFTER),
        ),
    ),
)
