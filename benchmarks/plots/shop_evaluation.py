"""Figuras de la evaluación del recomendador de la tienda: cada señal, la fusión y las
referencias en la prueba; la búsqueda de pesos en la validación; el arranque en frío; y CLIP
contra SIFT como señal visual. Cada barra lleva su intervalo de confianza del 95 %."""

from __future__ import annotations

from benchmarks.plots.model import IntervalBar, IntervalFigure, IntervalPanel, Shade
from benchmarks.plots.style import DARK_BLUE, LIGHT_BLUE, ORANGE
from benchmarks.shop_evaluation.evaluation import Target
from benchmarks.shop_evaluation_benchmark import (
    CLIP_ALONE,
    CLIP_FUSION,
    COLD_PRODUCTS,
    COLD_USERS,
    COPURCHASE_ALONE,
    NEARBY_ALONE,
    PLACE,
    POPULAR,
    PRODUCT_GROUPS,
    RANDOM,
    REPORT_NAME,
    SIFT_ALONE,
    SIFT_FUSION,
    STARTING_FUSION,
    STARTING_PLACE,
    TEXT_ALONE,
    TOP_WEIGHTS,
    TUNED_FUSION,
    USER_GROUPS,
    VISUAL_OPERATION,
    WEIGHTS_OPERATION,
)

SIGNAL = Shade("una señal sola", LIGHT_BLUE)
FUSION = Shade("fusión", DARK_BLUE)
BASELINE = Shade("referencia", ORANGE)
HIT = ("hit", "Hit@10 (fracción de usuarios)")
NDCG = ("ndcg", "NDCG@10")
TEST = Target.TEST.value


def _main_bars(operation: str) -> tuple[IntervalBar, ...]:
    return (
        IntervalBar("compras conjuntas", COPURCHASE_ALONE, operation, SIGNAL),
        IntervalBar("visual (CLIP)", CLIP_ALONE, operation, SIGNAL),
        IntervalBar("texto (BM25)", TEXT_ALONE, operation, SIGNAL),
        IntervalBar("cercanía", NEARBY_ALONE, operation, SIGNAL),
        IntervalBar("fusión, de partida", STARTING_FUSION, operation, FUSION),
        IntervalBar("fusión, ajustada", TUNED_FUSION, operation, FUSION),
        IntervalBar("lo más vendido", POPULAR, operation, BASELINE),
        IntervalBar("al azar", RANDOM, operation, BASELINE),
    )


def _cold_bars(operation: str) -> tuple[IntervalBar, ...]:
    return (
        IntervalBar("compras conjuntas", COPURCHASE_ALONE, operation, SIGNAL),
        IntervalBar("visual (CLIP)", CLIP_ALONE, operation, SIGNAL),
        IntervalBar("texto (BM25)", TEXT_ALONE, operation, SIGNAL),
        IntervalBar("fusión, ajustada", TUNED_FUSION, operation, FUSION),
        IntervalBar("lo más vendido", POPULAR, operation, BASELINE),
    )


def _cold_panels() -> tuple[IntervalPanel, ...]:
    users = tuple(
        IntervalPanel(f"Usuarios con {name} compras", HIT[1], HIT[0],
                      _cold_bars(COLD_USERS.format(name)))
        for name, _, _ in USER_GROUPS
    )
    products = tuple(
        IntervalPanel(f"Producto acertado con {name} compras", HIT[1], HIT[0],
                      _cold_bars(COLD_PRODUCTS.format(name)))
        for name, _, _ in PRODUCT_GROUPS
    )
    return users + products


def _weight_bars() -> tuple[IntervalBar, ...]:
    ranked = tuple(
        IntervalBar(PLACE.format(place), PLACE.format(place), WEIGHTS_OPERATION, FUSION)
        for place in range(1, TOP_WEIGHTS + 1)
    )
    return (*ranked, IntervalBar(STARTING_PLACE, STARTING_PLACE, WEIGHTS_OPERATION, BASELINE))


FIGURES: tuple[IntervalFigure, ...] = (
    IntervalFigure(
        filename="tienda-evaluacion",
        report=REPORT_NAME,
        title="Tienda: ¿está la última compra entre las 10 recomendaciones?",
        subtitle=(
            "Partición last_out de Amazon Reviews 2023 (instrumentos), {usuarios_evaluados}"
            " usuarios al azar;\nse recomienda con sus compras de entrenamiento."
            " Intervalos bootstrap del 95 %."
        ),
        columns=2,
        panels=(
            IntervalPanel("Hit@10 en la prueba", HIT[1], HIT[0], _main_bars(TEST)),
            IntervalPanel("NDCG@10 en la prueba", NDCG[1], NDCG[0], _main_bars(TEST)),
        ),
    ),
    IntervalFigure(
        filename="tienda-evaluacion-pesos",
        report=REPORT_NAME,
        title="Tienda: los pesos de la fusión, buscados en la validación",
        subtitle=(
            "Rejilla de {pasos_de_la_rejilla} pasos sobre los pesos que suman 1"
            " (compras · visual · texto ·\ncercanía): las mejores por NDCG@10 en la validación"
            " y los pesos de partida."
        ),
        columns=2,
        panels=(
            IntervalPanel("NDCG@10 en la validación", NDCG[1], NDCG[0], _weight_bars(),
                          label_field="rotulo"),
            IntervalPanel(
                "Medidos una vez en la prueba",
                NDCG[1],
                NDCG[0],
                (
                    IntervalBar("de partida", STARTING_FUSION, TEST, BASELINE),
                    IntervalBar("ajustados", TUNED_FUSION, TEST, FUSION),
                ),
            ),
        ),
    ),
    IntervalFigure(
        filename="tienda-evaluacion-frio",
        report=REPORT_NAME,
        title="Tienda: arranque en frío, Hit@10 en la prueba por grupo",
        subtitle=(
            "Por compras de entrenamiento del usuario y del producto que había que acertar.\n"
            "Intervalos bootstrap del 95 %."
        ),
        columns=2,
        panels=_cold_panels(),
    ),
    IntervalFigure(
        filename="tienda-evaluacion-visual",
        report=REPORT_NAME,
        title="Tienda: CLIP contra SIFT como parecido visual",
        subtitle=(
            "Cada señal visual sola y fundida con compras y texto (sin cercanía), con los\n"
            "pesos de cada una ajustados en la validación. Prueba, {usuarios_evaluados} usuarios."
        ),
        columns=2,
        panels=tuple(
            IntervalPanel(
                title,
                axis,
                field,
                (
                    IntervalBar("CLIP sola", CLIP_ALONE, VISUAL_OPERATION, SIGNAL),
                    IntervalBar("SIFT sola", SIFT_ALONE, VISUAL_OPERATION, SIGNAL),
                    IntervalBar("compras + CLIP + texto", CLIP_FUSION, VISUAL_OPERATION, FUSION),
                    IntervalBar("compras + SIFT + texto", SIFT_FUSION, VISUAL_OPERATION, FUSION),
                ),
            )
            for title, (field, axis) in (("Hit@10", HIT), ("NDCG@10", NDCG))
        ),
    ),
)
