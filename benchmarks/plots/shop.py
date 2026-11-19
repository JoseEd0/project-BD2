"""Figura de la carga de la tienda: cuánto tarda cada paso, cuánto cada índice y cuánto
ocupa cada tabla."""

from __future__ import annotations

from benchmarks.plots.model import (
    BreakdownFigure,
    BreakdownPanel,
    Measure,
    Segment,
    StackedBar,
)
from benchmarks.plots.style import AQUA, BLUE, ORANGE
from benchmarks.shop_seed_benchmark import (
    CLIP_EMBEDDINGS,
    DATA_FIELD,
    INDEX_BUILD,
    INDEX_FIELD,
    LOAD,
    REPORT_NAME,
    SIFT,
    SPACE,
    TIME_FIELD,
    WORK_FIELD,
)
from shop.catalog.schema import (
    CLIP,
    CLIP_INDEX,
    INVENTORY,
    INVENTORY_INDEX,
    PRODUCTS,
    PURCHASES,
    SIFT_INDEX_NAME,
    STORES,
    STORES_INDEX,
    TEXT_INDEX,
    USERS,
)

SECONDS = Measure("segundos", "s")
MEBIBYTES = Measure("espacio en disco (MiB)", "MiB")
TABLES = (
    (PRODUCTS, "productos"),
    (USERS, "usuarios"),
    (PURCHASES, "compras"),
    (STORES, "tiendas"),
    (INVENTORY, "inventario"),
    (CLIP, "CLIP (vectores)"),
)
STEPS = (
    *(StackedBar(label, table, LOAD) for table, label in TABLES),
    StackedBar("CLIP: embeddings", CLIP_EMBEDDINGS, LOAD),
)
INDEXES = (
    StackedBar("SIFT + HNSW (fotos)", SIFT, SIFT_INDEX_NAME),
    StackedBar("HNSW de CLIP", CLIP_INDEX, INDEX_BUILD),
    StackedBar("invertido (texto)", TEXT_INDEX, INDEX_BUILD),
    StackedBar("hash (inventario)", INVENTORY_INDEX, INDEX_BUILD),
    StackedBar("R-Tree (tiendas)", STORES_INDEX, INDEX_BUILD),
)

FIGURES: tuple[BreakdownFigure, ...] = (
    BreakdownFigure(
        filename="tienda-carga",
        report=REPORT_NAME,
        title="Tienda: cargar el catálogo en el gestor",
        subtitle=(
            "Todo el dataset por HTTP: 24 587 productos, 511 836 compras, 24 308 fotos.\n"
            "Cada carga incluye sus índices construidos al final; el panel del medio los "
            "mide aparte."
        ),
        panels=(
            BreakdownPanel(
                "Cargar cada tabla",
                SECONDS,
                STEPS,
                (
                    Segment("carga NDJSON con sus índices", TIME_FIELD, BLUE),
                    Segment("calcular (CLIP, SIFT) o construir", WORK_FIELD, AQUA),
                ),
            ),
            BreakdownPanel(
                "Construir cada índice (CREATE INDEX)",
                SECONDS,
                INDEXES,
                (Segment("calcular (CLIP, SIFT) o construir", WORK_FIELD, AQUA),),
            ),
            BreakdownPanel(
                "Espacio de cada tabla",
                MEBIBYTES,
                tuple(StackedBar(label, table, SPACE) for table, label in TABLES),
                (
                    Segment("datos", DATA_FIELD, BLUE),
                    Segment("índices", INDEX_FIELD, ORANGE),
                ),
            ),
        ),
    ),
)
