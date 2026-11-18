"""Histograma de longitudes de los fragmentos, a partir del manifiesto del corpus.

Necesita matplotlib (`uv sync --all-packages --extra plots`). Usa la misma tinta y el
mismo azul que las figuras de `benchmarks/figures/`, para que se lean como una serie.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as pyplot

from .errors import CorpusError

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BAR_COLOR = "#2a78d6"
FONT_FAMILY = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
FIGURE_SIZE_INCHES = (7.2, 3.9)
DPI = 170
TITLE_SIZE = 14.0
LABEL_SIZE = 10.5
# Las barras se separan con un hilo del color del fondo para que se lean una a una.
BAR_GAP_WIDTH = 1.2
MARKER_DASHES = (4.0, 2.2)
MARKER_LABEL_OFFSET = (4.0, -4.0)
MARKERS = (("p50", "mediana"), ("p95", "p95"))


def draw_chunk_lengths(manifest: Mapping[str, object], output: Path) -> None:
    """Escribe en `output` el histograma de `chunk_words` del manifiesto, con la mediana,
    el p95 y los límites de tamaño de los fragmentos marcados.

    Raises:
        CorpusError: si el manifiesto no trae la distribución de longitudes.
    """
    distribution = _mapping(manifest, "chunk_words")
    histogram = _mapping(distribution, "histogram")
    counts = [int(str(count)) for count in _list(histogram, "counts")]
    bin_words = int(str(histogram["bin_words"]))
    chunk_settings = _mapping(_mapping(manifest, "parameters"), "chunks")
    pyplot.rcParams.update({"font.family": FONT_FAMILY, "font.size": LABEL_SIZE})
    figure, axes = pyplot.subplots(figsize=FIGURE_SIZE_INCHES, facecolor=SURFACE)
    _style_axes(axes)
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
    for key, label in MARKERS:
        _mark(axes, int(str(distribution[key])), f"{label} {distribution[key]}")
    axes.set_xlim(0, max(len(counts) * bin_words, int(str(chunk_settings["max_words"]))))
    axes.set_xlabel("palabras por fragmento", color=SECONDARY_INK)
    axes.set_ylabel("fragmentos", color=SECONDARY_INK)
    counts_summary = _mapping(manifest, "counts")
    figure.suptitle(
        "Corpus del RAG: longitud de los fragmentos",
        x=0.02,
        ha="left",
        fontsize=TITLE_SIZE,
        color=INK,
    )
    axes.set_title(
        f"{distribution['count']} fragmentos de {counts_summary['papers']} documentos; "
        f"límites {chunk_settings['min_words']} a {chunk_settings['max_words']} palabras, "
        f"objetivo {chunk_settings['target_words']}; barras de {bin_words} palabras",
        loc="left",
        fontsize=LABEL_SIZE,
        color=SECONDARY_INK,
    )
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=DPI, facecolor=SURFACE)
    pyplot.close(figure)


def _style_axes(axes: Any) -> None:
    axes.set_facecolor(SURFACE)
    axes.grid(axis="y", color=GRID, linewidth=0.8)
    axes.set_axisbelow(True)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(AXIS)
    axes.tick_params(colors=SECONDARY_INK)


def _mark(axes: Any, words: int, label: str) -> None:
    axes.axvline(words, color=INK, linewidth=1.0, dashes=MARKER_DASHES)
    axes.annotate(
        label,
        xy=(words, 1.0),
        xycoords=("data", "axes fraction"),
        xytext=MARKER_LABEL_OFFSET,
        textcoords="offset points",
        va="top",
        color=INK,
    )


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
