"""Figura del catálogo de copyright: cargar e indexar por tramos las pistas de FMA, qué parte
del `CREATE INDEX` pesa y cuánto disco ocupa el gestor, según el tamaño del catálogo."""

from __future__ import annotations

from benchmarks.copyright_catalog_benchmark import (
    DISK,
    EXTRACTION,
    FEATURES_DISK,
    HNSW_DISK,
    HNSW_FILL,
    INDEX_BUILD,
    INDEX_PARTS,
    KIB_FIELD,
    LOAD,
    QUANTIZATION,
    REPORT_NAME,
    SEGMENTS_LOAD,
    SONGS_LOAD,
    TABLES_DISK,
    TOTAL,
    VOCABULARY,
)
from benchmarks.plots.model import DISK_SPACE, TOTAL_TIME, Figure, Style, Technique, compare
from benchmarks.plots.style import AQUA, BLUE, DARK_BLUE, LIGHT_BLUE, MUTED_INK, ORANGE

LOADS = (
    Technique(SONGS_LOAD, Style(LIGHT_BLUE, "D"), "canciones (NDJSON)"),
    Technique(SEGMENTS_LOAD, Style(BLUE, "o"), "tramos (NDJSON)"),
    Technique(INDEX_BUILD, Style(ORANGE, "s"), "CREATE INDEX HNSW"),
    Technique(TOTAL, Style(MUTED_INK, "x", dashed=True), "total"),
)
PARTS = (
    Technique(EXTRACTION, Style(AQUA, "^")),
    Technique(VOCABULARY, Style(DARK_BLUE, "v")),
    Technique(QUANTIZATION, Style(ORANGE, "P")),
    Technique(HNSW_FILL, Style(BLUE, "s")),
)
SPACE = (
    Technique(TABLES_DISK, Style(DARK_BLUE, "h"), "tablas e índices relacionales"),
    Technique(FEATURES_DISK, Style(AQUA, "X")),
    Technique(HNSW_DISK, Style(ORANGE, "p")),
)

SUBTITLE = (
    "Tramos de 5 s cada 2,5 s de pistas de fma_small (30 s), HNSW coseno, por HTTP.\n"
    "Ejes logarítmicos. El valor rotulado es el del catálogo entero."
)

FIGURES: tuple[Figure, ...] = (
    Figure(
        filename="copyright-catalogo",
        report=REPORT_NAME,
        title="Catálogo de copyright: cargar e indexar FMA por tramos",
        subtitle=SUBTITLE,
        rows_label="canciones del catálogo",
        columns=3,
        legend_columns=3,
        panels=(
            compare(LOAD, "Cargar e indexar", TOTAL_TIME, LOADS),
            compare(INDEX_PARTS, "Dentro del CREATE INDEX", TOTAL_TIME, PARTS),
            compare(DISK, "Disco del gestor", DISK_SPACE, SPACE, field=KIB_FIELD),
        ),
    ),
)
