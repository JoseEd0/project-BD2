"""Figuras comparativas a partir de los resultados crudos de los benchmarks.

    .venv/bin/pip install -e ".[plots]"
    .venv/bin/python -m benchmarks.plot

Lee los JSON de `results/` y escribe en `figures/` una imagen por comparación del
enunciado. Cada imagen es una fila de paneles pequeños, uno por operación, con el tamaño
del conjunto de datos en el eje horizontal y una línea por técnica.

Cada panel se tiene que entender solo: su título dice la operación, el eje vertical dice
qué se mide y en qué unidad, y el valor que se rotula al final de cada línea lleva esa
misma unidad.

Los dos ejes son logarítmicos: los tamaños van de 1 000 a 100 000 y los tiempos cubren
cuatro órdenes de magnitud, así que en escala lineal solo se vería la técnica más lenta.
En un eje logarítmico una recta es una ley de potencia y su pendiente dice cómo escala la
técnica: plana si no depende del tamaño, a 45° si crece en proporción.

Qué se dibuja está declarado en `FIGURES`; el resto del módulo solo sabe pintar.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.common import RESULTS_DIRECTORY

FIGURES_DIRECTORY = "figures"

# Tinta y superficie del gráfico. Los textos van siempre en tinta, nunca en el color de
# la serie: un rótulo aguamarina sobre fondo claro no se lee.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY_INK = "#52514e"
MUTED_INK = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

# Colores de serie en orden fijo. Los tres primeros se distinguen entre sí también con
# protanopia y deuteranopia (comprobado con el validador de paleta, ΔE ≥ 9 en OKLab).
BLUE = "#2a78d6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"
# Un solo tono de claro a oscuro, para series que tienen orden (radio pequeño → grande).
LIGHT_BLUE = "#86b6ef"
DARK_BLUE = "#104281"

FONT_FAMILY = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
DPI = 170
PANEL_WIDTH = 3.45
PANEL_HEIGHT = 3.05
HEADER_HEIGHT = 1.12
SUBTITLE_LINE_HEIGHT = 0.21
LEGEND_ROW_HEIGHT = 0.26
# Entradas de leyenda que caben en una fila por cada columna de paneles.
LEGEND_ENTRIES_PER_COLUMN = 2
LINE_WIDTH = 2.0
MARKER_SIZE = 7.5
MARKER_RING = 1.6
LABEL_SIZE = 10.0
TITLE_SIZE = 14.5
END_LABEL_OFFSET = 9.0
END_LABEL_GAP = 12.5
LEADER_THRESHOLD = 3.0
LEADER_EXTRA = 4.0
# Sitio a la derecha del último punto para su rótulo, que lleva el valor y la unidad.
RIGHT_MARGIN_FACTOR = 6.5
# Con menos de este número de décadas en el eje vertical, una marca por década dejaría el
# panel con una sola referencia; se añaden las marcas 2 y 5 de cada década.
DECADES_NEEDING_SUBTICKS = 2.0
SPARSE_TICKS = (1.0,)
DENSE_TICKS = (1.0, 2.0, 5.0)
LEFT_MARGIN_FACTOR = 0.72
# Parte del alto del eje, en décadas, que se deja libre arriba cuando el panel lleva una
# nota: así la nota no pisa ninguna línea.
NOTE_HEADROOM = 0.30
DASHES = (4.0, 2.2)
THOUSANDS = "\N{NARROW NO-BREAK SPACE}"


@dataclass(frozen=True, slots=True)
class Style:
    """Aspecto de una serie. El marcador cambia con el color: quien no distinga dos
    tonos, o imprima en gris, sigue distinguiendo un círculo de un cuadrado."""

    color: str
    marker: str
    dashed: bool = False


@dataclass(frozen=True, slots=True)
class Line:
    """Una serie de un panel: de qué medidas sale y cómo se pinta.

    `field` es la clave de `extra` de donde se toma el valor; `None` toma el tiempo.
    """

    label: str
    style: Style
    technique: str
    operation: str
    field: str | None = None


@dataclass(frozen=True, slots=True)
class Measure:
    """Lo que representa el eje vertical de un panel.

    Attributes:
        axis: rótulo del eje, con la magnitud y su unidad: `tiempo total (ms)`.
        unit: unidad que acompaña a cada valor rotulado: `ms`.
    """

    axis: str
    unit: str


TOTAL_TIME = Measure("tiempo total (ms)", "ms")
QUERY_TIME = Measure("tiempo por consulta (ms)", "ms")
DISK_SPACE = Measure("espacio en disco (KiB)", "KiB")
MEMORY = Measure("memoria (KiB)", "KiB")
NODES = Measure("número de nodos", "nodos")


@dataclass(frozen=True, slots=True)
class Panel:
    title: str
    measure: Measure
    lines: tuple[Line, ...]
    note: str = ""


@dataclass(frozen=True, slots=True)
class Figure:
    """Una imagen: el informe del que sale y los paneles que la componen.

    `rows_label` dice qué cuenta el eje horizontal: filas de una tabla, puntos indexados.
    """

    filename: str
    report: str
    title: str
    subtitle: str
    rows_label: str
    columns: int
    panels: tuple[Panel, ...]


def compare(
    operation: str,
    title: str,
    measure: Measure,
    techniques: Sequence[tuple[str, Style]],
    field: str | None = None,
    note: str = "",
) -> Panel:
    """Panel con una línea por técnica para una misma operación."""
    lines = tuple(Line(name, style, name, operation, field) for name, style in techniques)
    return Panel(title, measure, lines, note)


HEAP = ("heap file", Style(BLUE, "o"))
SEQUENTIAL_FILE = ("archivo secuencial", Style(ORANGE, "s"))
STORAGE = (HEAP, SEQUENTIAL_FILE)

CLUSTERED = ("B+ agrupado", Style(BLUE, "o"))
UNCLUSTERED = ("B+ no agrupado", Style(ORANGE, "s"))
HASH = ("hash extendible", Style(AQUA, "^"))
INDEXES = (CLUSTERED, UNCLUSTERED, HASH)
ORDERED_INDEXES = (CLUSTERED, UNCLUSTERED)

SCAN = ("búsqueda secuencial", Style(BLUE, "o"))
RTREE = ("R-Tree", Style(ORANGE, "s"))
GIST = ("GiST (PostgreSQL)", Style(AQUA, "^"))
RTREE_INSERTED = ("R-Tree (inserción)", Style(ORANGE, "D", dashed=True))
SPATIAL = (SCAN, RTREE, GIST)
SPATIAL_INDEXES = (RTREE, RTREE_INSERTED, GIST)

# Lo que hay que saber para leer cualquiera de las figuras, en dos líneas.
READING_GUIDE = (
    "Ejes logarítmicos: cada marca vale diez veces la anterior. Más abajo es mejor.\n"
    "El valor rotulado al final de cada línea es el de la tabla más grande (100 000)."
)
TABLE_ROWS = "número de filas de la tabla"
TABLE_POINTS = "número de puntos de la tabla"


def _visited(label: str, operation: str, color: str, marker: str) -> Line:
    return Line(label, Style(color, marker), "R-Tree", operation, "nodos_visitados_promedio")


FIGURES: tuple[Figure, ...] = (
    Figure(
        filename="almacenamiento",
        report="almacenamiento",
        title="Heap file frente a archivo secuencial",
        subtitle=f"Cuánto cuesta cada operación según el tamaño de la tabla.\n{READING_GUIDE}",
        rows_label=TABLE_ROWS,
        columns=2,
        panels=(
            compare("inserción", "Insertar todas las filas", TOTAL_TIME, STORAGE),
            compare("búsqueda por clave", "100 búsquedas por clave", TOTAL_TIME, STORAGE),
            compare("espacio en disco", "Espacio que ocupa el archivo", DISK_SPACE, STORAGE, "kib"),
            compare(
                "reorganización",
                "Reorganizar el archivo",
                TOTAL_TIME,
                (SEQUENTIAL_FILE,),
                note="El heap no se reorganiza:\nreutiliza los huecos al insertar.",
            ),
        ),
    ),
    Figure(
        filename="indices",
        report="indices",
        title="B+ agrupado, B+ no agrupado y hash extendible",
        subtitle=f"Cuánto cuesta cada operación según el tamaño de la tabla.\n{READING_GUIDE}",
        rows_label=TABLE_ROWS,
        columns=3,
        panels=(
            compare("construcción", "Construir el índice", TOTAL_TIME, INDEXES),
            compare("igualdad", "100 búsquedas por igualdad", TOTAL_TIME, INDEXES),
            compare(
                "rango",
                "100 consultas por rango",
                TOTAL_TIME,
                ORDERED_INDEXES,
                note="El hash no admite rangos:\nno guarda orden.",
            ),
            compare(
                "recorrido ordenado",
                "Recorrer toda la tabla en orden",
                TOTAL_TIME,
                ORDERED_INDEXES,
                note="El hash no puede:\nhabría que ordenar aparte.",
            ),
            compare("espacio", "Espacio que ocupa el índice", DISK_SPACE, INDEXES, field="kib"),
            compare(
                "inserciones/eliminaciones",
                "Borrar y reinsertar el 10 %",
                TOTAL_TIME,
                INDEXES,
            ),
        ),
    ),
    Figure(
        filename="espacial-radio",
        report="espacial",
        title="Consultas por radio: recorrido secuencial, R-Tree y GiST",
        subtitle=f"Tiempo medio de una consulta (promedio de 100).\n{READING_GUIDE}",
        rows_label=TABLE_POINTS,
        columns=3,
        panels=(
            compare("radio 1 km", "Lugares a menos de 1 km", QUERY_TIME, SPATIAL),
            compare("radio 5 km", "Lugares a menos de 5 km", QUERY_TIME, SPATIAL),
            compare("radio 10 km", "Lugares a menos de 10 km", QUERY_TIME, SPATIAL),
        ),
    ),
    Figure(
        filename="espacial-knn",
        report="espacial",
        title="Vecinos más cercanos: recorrido secuencial, R-Tree y GiST",
        subtitle=f"Tiempo medio de una consulta (promedio de 100).\n{READING_GUIDE}",
        rows_label=TABLE_POINTS,
        columns=3,
        panels=(
            compare("k-NN k=10", "Los 10 lugares más cercanos", QUERY_TIME, SPATIAL),
            compare("k-NN k=50", "Los 50 lugares más cercanos", QUERY_TIME, SPATIAL),
            compare("k-NN k=100", "Los 100 lugares más cercanos", QUERY_TIME, SPATIAL),
        ),
    ),
    Figure(
        filename="espacial-indice",
        report="espacial",
        title="Lo que cuesta tener el índice espacial",
        subtitle=f"Construirlo, guardarlo y consultarlo, según el tamaño.\n{READING_GUIDE}",
        rows_label=TABLE_POINTS,
        columns=3,
        panels=(
            compare("construcción", "Construir el índice", TOTAL_TIME, SPATIAL_INDEXES),
            compare(
                "espacio del índice",
                "Espacio que ocupa el índice",
                DISK_SPACE,
                SPATIAL_INDEXES,
                field="kib",
            ),
            compare(
                "memoria pico",
                "Memoria máxima al consultar",
                MEMORY,
                (SCAN, RTREE),
                field="kib",
                note="Solo el motor propio:\nmedido con tracemalloc.",
            ),
        ),
    ),
    Figure(
        filename="espacial-nodos",
        report="espacial",
        title="Nodos del R-Tree que abre cada consulta",
        subtitle=(
            "Promedio de 100 consultas, frente a todos los nodos que tiene el árbol.\n"
            "Ejes logarítmicos. El valor rotulado es el de la tabla más grande (100 000)."
        ),
        rows_label=TABLE_POINTS,
        columns=2,
        panels=(
            Panel(
                "Consultas por radio",
                NODES,
                (
                    _visited("radio 1 km", "radio 1 km", LIGHT_BLUE, "o"),
                    _visited("radio 5 km", "radio 5 km", BLUE, "s"),
                    _visited("radio 10 km", "radio 10 km", DARK_BLUE, "^"),
                    Line(
                        "todos los nodos del árbol",
                        Style(MUTED_INK, "x", dashed=True),
                        "R-Tree",
                        "radio 1 km",
                        "nodos_del_arbol",
                    ),
                ),
            ),
            Panel(
                "Vecinos más cercanos",
                NODES,
                (
                    _visited("k = 10", "k-NN k=10", LIGHT_BLUE, "o"),
                    _visited("k = 50", "k-NN k=50", BLUE, "s"),
                    _visited("k = 100", "k-NN k=100", DARK_BLUE, "^"),
                    Line(
                        "todos los nodos del árbol",
                        Style(MUTED_INK, "x", dashed=True),
                        "R-Tree",
                        "k-NN k=10",
                        "nodos_del_arbol",
                    ),
                ),
            ),
        ),
    ),
)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def points_of(report: dict[str, Any], line: Line) -> list[tuple[int, float]]:
    """Pares (tamaño, valor) de la serie, de menor a mayor tamaño.

    Se dejan fuera los valores que no son positivos: marcan lo que una técnica no hace o
    no ocupa, y no tienen sitio en un eje logarítmico.
    """
    found = []
    for item in report["measurements"]:
        if item["technique"] != line.technique or item["operation"] != line.operation:
            continue
        value = item["milliseconds"] if line.field is None else item["extra"].get(line.field)
        if isinstance(value, int | float) and value > 0:
            found.append((int(item["dataset_size"]), float(value)))
    return sorted(found)


def format_tick(value: float) -> str:
    """Marca de un eje: el número tal cual, sin ceros de relleno."""
    if value >= 1000:
        return f"{value:,.0f}".replace(",", THOUSANDS)
    return f"{value:g}"


def format_number(value: float) -> str:
    """Número con la precisión que tiene sentido leer: tres cifras, sin ruido."""
    if value >= 100:
        return f"{value:,.0f}".replace(",", THOUSANDS)
    return f"{value:.3g}"


def spread_labels(positions: Sequence[float], gap: float) -> list[float]:
    """Separa posiciones verticales hasta que ninguna quede a menos de `gap` de otra.

    Los rótulos que se pisarían forman un grupo que se reparte a intervalos de `gap`,
    centrado en la media de donde quería estar cada uno. Así el desplazamiento es el
    mínimo, va a los dos lados por igual y el orden se conserva.
    """
    order = sorted(range(len(positions)), key=lambda index: positions[index])
    groups: list[tuple[float, int]] = []
    for index in order:
        groups.append((positions[index], 1))
        while len(groups) > 1 and _top(groups[-2], gap) + gap > _bottom(groups[-1], gap):
            total, count = groups.pop()
            previous_total, previous_count = groups.pop()
            groups.append((previous_total + total, previous_count + count))
    placed = [
        _bottom(group, gap) + step * gap for group in groups for step in range(group[1])
    ]
    spread = [0.0] * len(positions)
    for rank, index in enumerate(order):
        spread[index] = placed[rank]
    return spread


def _bottom(group: tuple[float, int], gap: float) -> float:
    total, count = group
    return total / count - (count - 1) * gap / 2


def _top(group: tuple[float, int], gap: float) -> float:
    total, count = group
    return total / count + (count - 1) * gap / 2


def draw(figure: Figure, report: dict[str, Any], output: Path) -> Path:
    """Escribe la imagen de una comparación y devuelve su ruta."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": FONT_FAMILY, "font.size": LABEL_SIZE})
    rows = -(-len(figure.panels) // figure.columns)
    legend_columns = LEGEND_ENTRIES_PER_COLUMN * figure.columns
    legend_rows = -(-len(_legend_labels(figure)) // legend_columns)
    subtitle_lines = figure.subtitle.count("\n") + 1
    header = (
        HEADER_HEIGHT
        + SUBTITLE_LINE_HEIGHT * (subtitle_lines - 1)
        + LEGEND_ROW_HEIGHT * (legend_rows - 1)
    )
    width = PANEL_WIDTH * figure.columns
    height = PANEL_HEIGHT * rows + header
    canvas, grid = plt.subplots(rows, figure.columns, figsize=(width, height), squeeze=False)
    canvas.set_facecolor(SURFACE)
    axes = [axis for row in grid for axis in row]
    for axis, panel in zip(axes, figure.panels, strict=False):
        _draw_panel(axis, panel, report, figure.rows_label)
    for axis in axes[len(figure.panels) :]:
        axis.set_visible(False)
    _draw_header(canvas, figure, height, legend_columns)
    canvas.tight_layout(rect=(0.0, 0.0, 1.0, 1.0 - header / height), w_pad=1.8, h_pad=2.2)
    for axis, panel in zip(axes, figure.panels, strict=False):
        _label_ends(axis, panel, report)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{figure.filename}.png"
    canvas.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(canvas)
    return path


def _legend_labels(figure: Figure) -> list[str]:
    """Series de la figura, cada una una vez, en el orden en que aparecen."""
    labels = [line.label for panel in figure.panels for line in panel.lines]
    return list(dict.fromkeys(labels))


def _draw_header(canvas: Any, figure: Figure, height: float, legend_columns: int) -> None:
    """Título, subtítulo y leyenda, alineados a la izquierda sobre los paneles."""
    top = 1.0 - 0.16 / height
    canvas.text(
        0.012, top, figure.title, color=INK, fontsize=TITLE_SIZE, fontweight="bold", va="top"
    )
    canvas.text(
        0.012,
        top - 0.30 / height,
        figure.subtitle,
        color=SECONDARY_INK,
        fontsize=LABEL_SIZE + 0.5,
        va="top",
        linespacing=1.4,
    )
    subtitle_lines = figure.subtitle.count("\n") + 1
    legend_top = top - (0.52 + SUBTITLE_LINE_HEIGHT * (subtitle_lines - 1)) / height
    handles: dict[str, Any] = {}
    for axis in canvas.axes:
        for handle, label in zip(*axis.get_legend_handles_labels(), strict=True):
            handles.setdefault(label, handle)
    canvas.legend(
        handles.values(),
        handles.keys(),
        loc="upper left",
        bbox_to_anchor=(0.004, legend_top),
        ncols=min(len(handles), legend_columns),
        frameon=False,
        fontsize=LABEL_SIZE + 0.5,
        labelcolor=SECONDARY_INK,
        handlelength=2.6,
        columnspacing=1.8,
    )


def _draw_panel(axis: Any, panel: Panel, report: dict[str, Any], rows_label: str) -> None:
    from math import log10

    from matplotlib.ticker import FixedLocator, FuncFormatter, LogLocator, NullLocator

    axis.set_facecolor(SURFACE)
    sizes: set[int] = set()
    for line in panel.lines:
        points = points_of(report, line)
        if not points:
            continue
        sizes.update(size for size, _ in points)
        axis.plot(
            [size for size, _ in points],
            [value for _, value in points],
            label=line.label,
            color=line.style.color,
            linewidth=LINE_WIDTH,
            linestyle=(0, DASHES) if line.style.dashed else "solid",
            marker=line.style.marker,
            markersize=MARKER_SIZE,
            markeredgecolor=line.style.color if line.style.marker == "x" else SURFACE,
            markeredgewidth=MARKER_RING,
            solid_capstyle="round",
            solid_joinstyle="round",
            zorder=3,
        )
    axis.set_xscale("log")
    axis.set_yscale("log")
    if sizes:
        axis.set_xlim(min(sizes) * LEFT_MARGIN_FACTOR, max(sizes) * RIGHT_MARGIN_FACTOR)
    axis.margins(y=0.16)
    low, high = axis.get_ylim()
    if panel.note:
        high *= (high / low) ** NOTE_HEADROOM
        axis.set_ylim(low, high)
    crowded = log10(high / low) >= DECADES_NEEDING_SUBTICKS
    axis.xaxis.set_major_locator(FixedLocator(sorted(sizes)))
    axis.yaxis.set_major_locator(LogLocator(subs=SPARSE_TICKS if crowded else DENSE_TICKS))
    axis.xaxis.set_minor_locator(NullLocator())
    axis.yaxis.set_minor_locator(NullLocator())
    axis.xaxis.set_major_formatter(FuncFormatter(lambda value, _: format_tick(value)))
    axis.yaxis.set_major_formatter(FuncFormatter(lambda value, _: format_tick(value)))
    axis.grid(visible=True, axis="y", color=GRID, linewidth=0.8, linestyle="solid", zorder=0)
    axis.set_axisbelow(True)
    for side in ("top", "right", "left"):
        axis.spines[side].set_visible(False)
    axis.spines["bottom"].set_color(AXIS)
    axis.tick_params(axis="both", colors=MUTED_INK, length=0, labelsize=LABEL_SIZE)
    axis.set_title(panel.title, loc="left", color=INK, fontsize=LABEL_SIZE + 1.5, pad=9)
    axis.set_xlabel(rows_label, color=SECONDARY_INK, fontsize=LABEL_SIZE, labelpad=4)
    axis.set_ylabel(panel.measure.axis, color=SECONDARY_INK, fontsize=LABEL_SIZE, labelpad=5)
    if panel.note:
        axis.text(
            0.04,
            0.95,
            panel.note,
            transform=axis.transAxes,
            color=MUTED_INK,
            fontsize=LABEL_SIZE - 0.5,
            va="top",
            linespacing=1.35,
            bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 2.5},
            zorder=2,
        )


def _label_ends(axis: Any, panel: Panel, report: dict[str, Any]) -> None:
    """Rotula el valor de cada serie en su último punto, que es el que se compara.

    Solo el extremo: un número junto a cada punto no lo lee nadie. Si dos extremos casi
    coinciden, los rótulos se separan y se unen a su punto con un trazo fino.
    """
    ends = [points[-1] for points in (points_of(report, line) for line in panel.lines) if points]
    if not ends:
        return
    to_points = 72.0 / axis.figure.dpi
    wanted = [axis.transData.transform(end)[1] * to_points for end in ends]
    heights = spread_labels(wanted, END_LABEL_GAP)
    for end, anchor, height in zip(ends, wanted, heights, strict=True):
        displacement = height - anchor
        moved = abs(displacement) > LEADER_THRESHOLD
        axis.annotate(
            f"{format_number(end[1])} {panel.measure.unit}",
            xy=end,
            xycoords="data",
            xytext=(END_LABEL_OFFSET + (LEADER_EXTRA if moved else 0.0), displacement),
            textcoords="offset points",
            color=SECONDARY_INK,
            fontsize=LABEL_SIZE,
            va="center",
            bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1.2},
            arrowprops={"arrowstyle": "-", "color": AXIS, "linewidth": 0.8} if moved else None,
            zorder=4,
        )


def main() -> None:
    here = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=here / RESULTS_DIRECTORY)
    parser.add_argument("--output", type=Path, default=here / FIGURES_DIRECTORY)
    arguments = parser.parse_args()
    for figure in FIGURES:
        source = arguments.results / f"{figure.report}.json"
        if not source.exists():
            print(f"falta {source}: no se dibuja {figure.filename}")
            continue
        print(draw(figure, load(source), arguments.output))


if __name__ == "__main__":
    main()
