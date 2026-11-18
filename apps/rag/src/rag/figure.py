"""Histograma de longitudes de los fragmentos, a partir del manifiesto del corpus.

Necesita matplotlib (`uv sync --all-packages --extra plots`). Usa la misma tinta y el
mismo azul que las figuras de `benchmarks/figures/`, para que se lean como una serie.

El eje de los fragmentos es logarítmico: casi todos quedan cerca del objetivo, y en escala
lineal la cola que llega hasta el máximo no se vería.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as pyplot
from matplotlib.ticker import FuncFormatter

from .errors import CorpusError

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BAR_COLOR = "#2a78d6"
FONT_FAMILY = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
FIGURE_SIZE_INCHES = (7.6, 4.4)
DPI = 170
TITLE_SIZE = 14.0
LABEL_SIZE = 10.5
GRID_WIDTH = 0.8
MARKER_WIDTH = 1.0
# Las barras se separan con un hilo del color del fondo para que se lean una a una.
BAR_GAP_WIDTH = 1.2
MARKER_DASHES = (4.0, 2.2)
LABEL_GAP_POINTS = 4.0
LABEL_ROW_POINTS = 15.0
# Aire sobre la barra más alta, en múltiplos de su altura: ahí van los rótulos de la
# mediana y el p95, que así no pisan ninguna barra.
HEADROOM_FACTOR = 6.0
THOUSANDS_SEPARATOR = "\N{NARROW NO-BREAK SPACE}"
SUBTITLE_POSITION = (0.02, 0.875)
TOP_MARGIN = 0.84


def draw_chunk_lengths(manifest: Mapping[str, object], output: Path) -> None:
    """Escribe en `output` el histograma de `chunk_words` del manifiesto, con la mediana y
    el p95 marcados y el eje horizontal entre los límites de tamaño de los fragmentos.

    Raises:
        CorpusError: si el manifiesto no trae la distribución de longitudes.
    """
    distribution = _mapping(manifest, "chunk_words")
    histogram = _mapping(distribution, "histogram")
    counts = [int(str(count)) for count in _list(histogram, "counts")]
    bin_words = int(str(histogram["bin_words"]))
    limits = _mapping(_mapping(manifest, "parameters"), "chunks")
    papers = _mapping(manifest, "counts")["papers"]
    pyplot.rcParams.update({"font.family": FONT_FAMILY, "font.size": LABEL_SIZE})
    figure, axes = pyplot.subplots(figsize=FIGURE_SIZE_INCHES, facecolor=SURFACE)
    _style_axes(axes)
    _draw_bars(axes, counts, bin_words)
    median, high = int(str(distribution["p50"])), int(str(distribution["p95"]))
    _mark(axes, median, f"mediana {median}", row=0)
    _mark(axes, high, f"p95 {high}", row=1)
    axes.set_xlim(int(str(limits["min_words"])) - bin_words, int(str(limits["max_words"])))
    axes.set_ylim(bottom=1, top=max(counts) * HEADROOM_FACTOR)
    axes.set_xlabel("palabras por fragmento", color=SECONDARY_INK)
    axes.set_ylabel("fragmentos (escala logarítmica)", color=SECONDARY_INK)
    figure.suptitle(
        "Corpus del RAG: longitud de los fragmentos",
        x=SUBTITLE_POSITION[0],
        ha="left",
        fontsize=TITLE_SIZE,
        color=INK,
    )
    figure.text(
        *SUBTITLE_POSITION,
        f"{_number(distribution['count'])} fragmentos de {_number(papers)} documentos, "
        f"entre {distribution['min']} y {distribution['max']} palabras.\n"
        f"Objetivo {limits['target_words']}, límites {limits['min_words']} a "
        f"{limits['max_words']}; barras de {bin_words} palabras.",
        color=SECONDARY_INK,
        va="top",
    )
    figure.tight_layout(rect=(0, 0, 1, TOP_MARGIN))
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=DPI, facecolor=SURFACE)
    pyplot.close(figure)


def _draw_bars(axes: Any, counts: list[int], bin_words: int) -> None:
    starts = [index * bin_words for index in range(len(counts))]
    axes.bar(
        starts,
        counts,
        width=bin_words,
        align="edge",
        color=BAR_COLOR,
        edgecolor=SURFACE,
        linewidth=BAR_GAP_WIDTH,
    )
    axes.set_yscale("log")
    axes.yaxis.set_major_formatter(FuncFormatter(lambda value, _position: _number(value)))


def _style_axes(axes: Any) -> None:
    axes.set_facecolor(SURFACE)
    axes.grid(axis="y", color=GRID, linewidth=GRID_WIDTH)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(AXIS)
    axes.tick_params(colors=SECONDARY_INK)


def _mark(axes: Any, words: int, label: str, row: int) -> None:
    """Una línea vertical en `words` con su rótulo a la derecha, en la fila `row` desde
    arriba: dos marcas cercanas no se pisan."""
    axes.axvline(words, color=INK, linewidth=MARKER_WIDTH, dashes=MARKER_DASHES)
    axes.annotate(
        label,
        xy=(words, 1.0),
        xycoords=("data", "axes fraction"),
        xytext=(LABEL_GAP_POINTS, -LABEL_GAP_POINTS - row * LABEL_ROW_POINTS),
        textcoords="offset points",
        ha="left",
        va="top",
        color=INK,
    )


def _number(value: object) -> str:
    return f"{round(float(str(value))):,}".replace(",", THOUSANDS_SEPARATOR)


def _mapping(container: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = container.get(key)
    if not isinstance(value, Mapping):
        raise CorpusError(f"al manifiesto le falta {key!r}")
    return value


def _list(container: Mapping[str, object], key: str) -> list[object]:
    value = container.get(key)
    if not isinstance(value, list):
        raise CorpusError(f"al manifiesto le falta la lista {key!r}")
    return value
