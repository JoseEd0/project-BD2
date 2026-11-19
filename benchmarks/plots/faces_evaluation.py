"""Figuras de la evaluación de rostros con LFW: la ROC y los pliegues de la verificación, los
errores del conjunto abierto por umbral, la latencia de cada etapa y la caché de rastros."""

from __future__ import annotations

from benchmarks.faces_cache_benchmark import REPORT_NAME as CACHE_REPORT
from benchmarks.faces_cache_benchmark import SPEED, TIMELINE
from benchmarks.faces_evaluation.embeddings import STAGES
from benchmarks.faces_evaluation_benchmark import (
    ALL_PAIRS,
    FOLDS,
    IDENTIFICATION,
    LATENCY,
    MODEL,
    OPEN_SET,
    OPEN_SET_CURVE,
    OPERATING_POINT,
    PROTOCOL,
    REPORT_NAME,
    ROC,
    SEARCH_STAGE,
    SQL_EXACT,
    SQL_HNSW,
)
from benchmarks.faces_stream_benchmark import WITH_CACHE, WITHOUT_CACHE
from benchmarks.plots.model import (
    Axis,
    BreakdownFigure,
    BreakdownPanel,
    Measure,
    Segment,
    Series,
    StackedBar,
    Style,
    XYFigure,
    XYPanel,
)
from benchmarks.plots.style import AQUA, BLUE, DARK_BLUE, LIGHT_BLUE, MUTED_INK, ORANGE

# Los umbrales que se marcan sobre las curvas: el de OpenCV, el anterior del servicio para
# reconocer, el calibrado aquí y el de «ya estás registrado».
MARKED_THRESHOLDS = (
    ("0.363", "0,363 (OpenCV, registro)", Style(LIGHT_BLUE, "o")),
    ("0.45", "0,45 (antes)", Style(MUTED_INK, "s")),
    ("0.47", "0,47 (reconocer)", Style(DARK_BLUE, "^")),
    ("0.5", "0,50 (duplicado)", Style(AQUA, "D")),
)
FAR = Axis("FAR: pares de personas distintas aceptados", "far", logarithmic=True)
TAR = Axis("TAR: pares de la misma persona aceptados", "tar")
FOLD = Axis("pliegue", "pliegue")
THRESHOLD = Axis("umbral de coseno", "umbral")
SPEED_AXIS = Axis("velocidad del rostro (px por cuadro)", "velocidad_px")
MILLISECONDS = Measure("tiempo (ms)", "ms")


def _threshold_marks(source: str, operation: str) -> tuple[Series, ...]:
    return tuple(
        Series(label, style, f"{source}: umbral {value}", operation, points_only=True)
        for value, label, style in MARKED_THRESHOLDS
    )


def _percentile_segments() -> tuple[Segment, ...]:
    return (
        Segment("p50", "p50_ms", BLUE),
        Segment("hasta p95", "p95_sobre_p50_ms", LIGHT_BLUE),
        Segment("hasta p99", "p99_sobre_p95_ms", ORANGE),
    )


VERIFICATION = XYFigure(
    filename="rostros-evaluacion-verificacion",
    report=REPORT_NAME,
    title="Rostros: verificación con los pares de LFW",
    subtitle=(
        "SFace sobre el rostro más cercano al centro. Protocolo: 6 000 pares en 10 pliegues;\n"
        "todos los pares: 242 000 de la misma persona y 16,5 millones de personas distintas."
    ),
    columns=3,
    legend_columns=3,
    panels=(
        XYPanel(
            "ROC (más arriba y a la izquierda es mejor)",
            FAR,
            TAR,
            (
                Series("6 000 pares del protocolo", Style(ORANGE, "o"), PROTOCOL, ROC),
                Series("todos los pares", Style(BLUE, "s"), ALL_PAIRS, ROC),
                *_threshold_marks(ALL_PAIRS, OPERATING_POINT),
            ),
        ),
        XYPanel(
            "Exactitud de cada pliegue",
            FOLD,
            Axis("pares bien clasificados", "exactitud"),
            (Series("pliegue de prueba", Style(BLUE, "o"), MODEL, FOLDS, points_only=True),),
        ),
        XYPanel(
            "Umbral elegido con los otros nueve",
            FOLD,
            Axis("umbral de coseno", "umbral"),
            (Series("umbral del pliegue", Style(ORANGE, "s"), MODEL, FOLDS, points_only=True),),
        ),
    ),
)

OPEN_SET_FIGURE = XYFigure(
    filename="rostros-evaluacion-identificacion",
    report=REPORT_NAME,
    title="Rostros: identificación en el gestor con 1 679 personas de LFW",
    subtitle=(
        "Galería: la primera foto de cada persona con dos o más (VECTOR(128), HNSW coseno).\n"
        "7 481 sondas de esas personas y 4 061 de personas con una sola foto. Más abajo es mejor."
    ),
    columns=2,
    legend_columns=3,
    panels=(
        XYPanel(
            "Desconocidas aceptadas (FPIR)",
            THRESHOLD,
            Axis("FPIR", "fpir", logarithmic=True),
            (
                Series("HNSW del gestor", Style(BLUE, "o"), SQL_HNSW, OPEN_SET_CURVE),
                Series(
                    "búsqueda exacta",
                    Style(MUTED_INK, "x", dashed=True),
                    SQL_EXACT,
                    OPEN_SET_CURVE,
                ),
                *_threshold_marks(SQL_HNSW, OPEN_SET),
            ),
        ),
        XYPanel(
            "Registradas perdidas (FNIR)",
            THRESHOLD,
            Axis("FNIR", "fnir", logarithmic=True),
            (
                Series("HNSW del gestor", Style(BLUE, "o"), SQL_HNSW, OPEN_SET_CURVE),
                Series(
                    "búsqueda exacta",
                    Style(MUTED_INK, "x", dashed=True),
                    SQL_EXACT,
                    OPEN_SET_CURVE,
                ),
                *_threshold_marks(SQL_HNSW, OPEN_SET),
            ),
        ),
    ),
)

LATENCY_FIGURE = BreakdownFigure(
    filename="rostros-evaluacion-latencia",
    report=REPORT_NAME,
    title="Rostros: latencia de cada etapa con las fotos de LFW",
    subtitle=(
        "13 221 fotos de 250 x 250 px con rostro, un hilo; buscar es SIMILAR_TO(..., k = 1)\n"
        "por SQL en la galería de 1 679 personas. Barras: p50, p95 y p99. Más corto es mejor."
    ),
    panels=(
        BreakdownPanel(
            "Cada etapa del pipeline",
            MILLISECONDS,
            tuple(StackedBar(stage, stage, LATENCY) for stage in (*STAGES, SEARCH_STAGE)),
            _percentile_segments(),
        ),
        BreakdownPanel(
            "Buscar: HNSW o búsqueda exacta",
            MILLISECONDS,
            (
                StackedBar("HNSW del gestor", SQL_HNSW, IDENTIFICATION),
                StackedBar("exacta del gestor", SQL_EXACT, IDENTIFICATION),
            ),
            _percentile_segments(),
        ),
    ),
)

CACHE_FIGURE = XYFigure(
    filename="rostros-cache",
    report=CACHE_REPORT,
    title="Rostros en vivo: la caché de rastros según cuánto se mueve el rostro",
    subtitle=(
        "Un retrato que se desliza de lado a lado en cuadros de 640 x 480, a 10 por segundo,\n"
        "por el nginx del compose; 200 cuadros por velocidad, con la caché y sin ella."
    ),
    columns=3,
    legend_columns=4,
    panels=(
        XYPanel(
            "Rostros respondidos desde el rastro",
            SPEED_AXIS,
            Axis("tasa de aciertos", "tasa_de_aciertos"),
            (Series("con caché", Style(BLUE, "o"), WITH_CACHE, SPEED),),
        ),
        XYPanel(
            "Latencia por cuadro, p50",
            SPEED_AXIS,
            Axis("tiempo por cuadro (ms)", "p50_ms"),
            (
                Series("con caché", Style(BLUE, "o"), WITH_CACHE, SPEED),
                Series("sin caché", Style(ORANGE, "s"), WITHOUT_CACHE, SPEED),
            ),
        ),
        XYPanel(
            "Aciertos en cada segundo de video",
            Axis("segundo", "segundo"),
            Axis("tasa de aciertos", "tasa_de_aciertos"),
            (
                Series("0 px por cuadro", Style(LIGHT_BLUE, "o"), "0 px por cuadro", TIMELINE),
                Series("64 px por cuadro", Style(DARK_BLUE, "^"), "64 px por cuadro", TIMELINE),
                Series("128 px por cuadro", Style(AQUA, "D"), "128 px por cuadro", TIMELINE),
            ),
        ),
    ),
)

FIGURES: tuple[XYFigure | BreakdownFigure, ...] = (
    VERIFICATION,
    OPEN_SET_FIGURE,
    LATENCY_FIGURE,
    CACHE_FIGURE,
)
