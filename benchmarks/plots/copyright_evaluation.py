"""Figuras de la evaluación de la detección de copyright con remixes de verdad conocida: curvas
ROC por distorsión, precisión, recall y F1 al umbral elegido, IoU de los tramos localizados y
recall por largo del fragmento."""

from __future__ import annotations

import itertools

from benchmarks.copyright_evaluation_benchmark import (
    DEFAULT_LENGTH_BINS,
    DISTORTIONS,
    REPORT_NAME,
)
from benchmarks.plots.model import (
    Axis,
    BarFigure,
    BarGroup,
    BarPanel,
    Series,
    Style,
    Technique,
    XYFigure,
    XYPanel,
)
from benchmarks.plots.style import AQUA, AXIS, BLUE, DARK_BLUE, INK, LIGHT_BLUE, MUTED_INK, ORANGE
from benchmarks.remixes.evaluation import (
    ALL_VERSIONS,
    CHANCE,
    CHOSEN_POINT,
    F1,
    FALSE_POSITIVE_RATE,
    ORIGINAL_SPAN,
    PRECISION,
    RECALL,
    ROC,
    TRUE_POSITIVE_RATE,
    UPLOAD_SPAN,
    length_bin_label,
)

(
    ORIGINAL,
    NOISE_30,
    NOISE_20,
    NOISE_10,
    FASTER,
    SLOWER,
    PITCH_UP,
    PITCH_DOWN,
    MP3,
    LOW_PASS,
    VOLUME_1,
    VOLUME_3,
    VOLUME_12,
) = (distortion.name for distortion in DISTORTIONS)
NOISE_GROUPS = (
    BarGroup("original", ORIGINAL),
    BarGroup("MP3 64", MP3),
    BarGroup("ruido 30", NOISE_30),
    BarGroup("ruido 20", NOISE_20),
    BarGroup("ruido 10", NOISE_10),
)
LEVEL_GROUPS = (
    BarGroup("-1 dB", VOLUME_1),
    BarGroup("-3 dB", VOLUME_3),
    BarGroup("-12 dB", VOLUME_12),
    BarGroup("4 kHz", LOW_PASS),
)
TIME_GROUPS = (
    BarGroup("tempo +5", FASTER),
    BarGroup("tempo -5", SLOWER),
    BarGroup("tono +1", PITCH_UP),
    BarGroup("tono -1", PITCH_DOWN),
    BarGroup("todas", ALL_VERSIONS),
)
ORIGINAL_LINE = Technique(ORIGINAL, Style(DARK_BLUE, "o"), "sin distorsión")
ALL_LINE = Technique(ALL_VERSIONS, Style(MUTED_INK, "x", dashed=True), "todas juntas")
NOISE_LINES = (
    ORIGINAL_LINE,
    Technique(MP3, Style(ORANGE, "^")),
    Technique(NOISE_30, Style(LIGHT_BLUE, "s")),
    Technique(NOISE_20, Style(BLUE, "D")),
    Technique(NOISE_10, Style(AQUA, "v")),
)
LEVEL_LINES = (
    ORIGINAL_LINE,
    Technique(VOLUME_1, Style(LIGHT_BLUE, "s")),
    Technique(VOLUME_3, Style(BLUE, "D")),
    Technique(VOLUME_12, Style(AQUA, "v")),
    Technique(LOW_PASS, Style(ORANGE, "^", dashed=True)),
)
TIME_LINES = (
    ORIGINAL_LINE,
    Technique(FASTER, Style(ORANGE, "s")),
    Technique(SLOWER, Style(ORANGE, "D", dashed=True)),
    Technique(PITCH_UP, Style(AQUA, "^")),
    Technique(PITCH_DOWN, Style(AQUA, "v", dashed=True)),
    ALL_LINE,
)
DETECTION_BARS = (
    Technique(PRECISION, Style(BLUE, "s")),
    Technique(RECALL, Style(ORANGE, "o")),
    Technique(F1, Style(AQUA, "^")),
)
SPAN_BARS = (
    Technique(UPLOAD_SPAN, Style(BLUE, "s"), "tramo en la subida"),
    Technique(ORIGINAL_SPAN, Style(ORANGE, "o"), "tramo en el original"),
)
LENGTH_BARS = (
    ORIGINAL_LINE,
    Technique(MP3, Style(ORANGE, "^")),
    Technique(NOISE_30, Style(LIGHT_BLUE, "s")),
    Technique(VOLUME_1, Style(AQUA, "v")),
)
LENGTH_GROUPS = tuple(
    BarGroup(length_bin_label(low, high), length_bin_label(low, high))
    for low, high in itertools.pairwise(DEFAULT_LENGTH_BINS)
)
NOISE_TITLE = "MP3 y ruido (SNR, dB)"
LEVEL_TITLE = "Volumen y pasa bajos"
TIME_TITLE = "Tempo y tono (%, semitonos)"


FALSE_POSITIVES = Axis("tasa de falsos positivos", FALSE_POSITIVE_RATE)
TRUE_POSITIVES = Axis("tasa de verdaderos positivos (recall)", TRUE_POSITIVE_RATE)
CHANCE_LINE = Series("azar", Style(AXIS, "", dashed=True), CHANCE, ROC)
CHOSEN_STYLE = Style(INK, "o")


def _roc_panel(title: str, lines: tuple[Technique, ...]) -> XYPanel:
    """Una curva por versión, la diagonal del azar y, sobre cada curva, el punto del umbral
    elegido."""
    curves = tuple(Series(line.label or line.name, line.style, line.name, ROC) for line in lines)
    chosen = tuple(
        Series(CHOSEN_POINT, CHOSEN_STYLE, CHOSEN_POINT, line.name, points_only=True)
        for line in lines
    )
    return XYPanel(title, FALSE_POSITIVES, TRUE_POSITIVES, (CHANCE_LINE, *curves, *chosen))


def _bar_panels(axis: str, field: str, techniques: tuple[Technique, ...]) -> tuple[BarPanel, ...]:
    return (
        BarPanel(NOISE_TITLE, axis, field, NOISE_GROUPS, techniques),
        BarPanel(LEVEL_TITLE, axis, field, LEVEL_GROUPS, techniques),
        BarPanel(TIME_TITLE, axis, field, TIME_GROUPS, techniques),
    )


FIGURES: tuple[XYFigure | BarFigure, ...] = (
    XYFigure(
        filename="copyright-evaluacion-roc",
        report=REPORT_NAME,
        title="Copyright: curvas ROC de la detección por distorsión",
        subtitle=(
            "Recall de los fragmentos copiados contra la fracción de remixes sin copias con\n"
            "alguna coincidencia, al barrer el umbral. El punto negro es el umbral elegido con\n"
            "los remixes sin distorsión. Bajo la diagonal: el remix sin copias se parece más a\n"
            "algo del catálogo que la copia a su original."
        ),
        columns=3,
        legend_columns=5,
        panels=(
            _roc_panel(NOISE_TITLE, NOISE_LINES),
            _roc_panel(LEVEL_TITLE, LEVEL_LINES),
            _roc_panel(TIME_TITLE, TIME_LINES),
        ),
    ),
    BarFigure(
        filename="copyright-evaluacion-deteccion",
        report=REPORT_NAME,
        title="Copyright: precisión, recall y F1 por distorsión",
        subtitle=(
            "Al umbral elegido con la curva ROC de los remixes sin distorsión. Un acierto es la\n"
            "canción copiada con una coincidencia que llega al umbral; una falsa alarma, otra."
        ),
        sizes_label="remixes",
        panels=_bar_panels("fracción", "elegido", DETECTION_BARS),
    ),
    BarFigure(
        filename="copyright-evaluacion-iou",
        report=REPORT_NAME,
        title="Copyright: dónde se localiza la copia",
        subtitle=(
            "Mediana de la IoU entre el tramo informado y el verdadero, en los aciertos al umbral\n"
            "elegido; sin barra, ningún acierto."
        ),
        sizes_label="remixes",
        panels=_bar_panels("IoU", "iou", SPAN_BARS),
    ),
    BarFigure(
        filename="copyright-evaluacion-largo",
        report=REPORT_NAME,
        title="Copyright: recall según el largo del fragmento",
        subtitle=(
            "Al umbral elegido. Una copia de menos de 7,5 s no llena dos ventanas\n"
            "seguidas del catálogo."
        ),
        sizes_label="remixes",
        panels=(BarPanel("Recall por largo", "recall", "recall", LENGTH_GROUPS, LENGTH_BARS),),
    ),
)
