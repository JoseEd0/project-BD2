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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import ceil, floor, log10
from pathlib import Path
from typing import Any, TypeVar

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
POINTS_PER_INCH = 72.0
# Paneles estrechos y letra grande: en una página el ancho se reparte entre tres paneles,
# y lo que tiene que seguir leyéndose al reducir la imagen es el texto.
PANEL_WIDTH = 3.35
PANEL_HEIGHT = 2.7
LABEL_SIZE = 11.0
PANEL_TITLE_SIZE = 12.5
TITLE_SIZE = 16.0
# Cabecera, en pulgadas: margen, título, cada línea de subtítulo, cada fila de leyenda y
# el aire que queda hasta los paneles.
HEADER_TOP = 0.16
TITLE_HEIGHT = 0.33
SUBTITLE_LINE_HEIGHT = 0.225
LEGEND_ROW_HEIGHT = 0.27
HEADER_BOTTOM = 0.22
# Entradas de leyenda que caben en una fila por cada columna de paneles.
LEGEND_ENTRIES_PER_COLUMN = 2
PANEL_GAP = 1.0
ROW_GAP = 2.0
LINE_WIDTH = 2.0
MARKER_SIZE = 7.5
MARKER_RING = 1.6
TITLE_PAD = 9.0
# Del eje a su rótulo vertical: el ancho de la marca más larga de la figura más este aire.
# Es el mismo en todos los paneles, para que rótulos y títulos queden en columna.
GUTTER_PADDING = 9.0
END_LABEL_OFFSET = 9.0
END_LABEL_PADDING = 3.0
END_LABEL_GAP = 14.0
LEADER_THRESHOLD = 3.0
LEADER_EXTRA = 4.0
LEFT_MARGIN_FACTOR = 0.72
# Antes de maquetar no se conoce el ancho del eje; basta con que la última marca no caiga
# en el borde. Después se deja a la derecha justo lo que ocupa el rótulo más ancho.
EXPECTED_LABEL_SHARE = 0.4
MAXIMUM_LABEL_SHARE = 0.7
# Aire sobre el punto más alto y bajo el más bajo, como parte de las décadas que cubren.
VERTICAL_MARGIN = 0.16
MINIMUM_DECADES = 1.0
# Parte del alto del eje que separa, como poco, la marca más baja de la base: una rejilla
# pegada a la línea del eje se ve como una línea doble y su número pisa los de abajo.
BASELINE_CLEARANCE = 0.10
# Con menos de este número de décadas en el eje vertical, una marca por década dejaría el
# panel con una sola referencia; se añaden las marcas 2 y 5 de cada década.
DECADES_NEEDING_SUBTICKS = 2.0
SPARSE_TICKS = (1.0,)
DENSE_TICKS = (1.0, 2.0, 5.0)
# Parte del alto del eje, en décadas, que se deja libre arriba por cada línea de nota. En
# esa franja no hay marcas ni rejilla, así que la nota no pisa nada.
NOTE_LINE_HEADROOM = 0.22
NOTE_INSET = (2.0, -3.0)
DASHES = (4.0, 2.2)
THOUSANDS = "\N{NARROW NO-BREAK SPACE}"
# Decimales según el tamaño del número, los mismos que usan las tablas del informe: tres
# cifras a partir de 1 y dos decimales por debajo.
DECIMALS_FROM = ((100.0, 0), (10.0, 1), (0.1, 2))
SMALLEST_DECIMALS = 3
WHOLE_FROM = 100.0

Item = TypeVar("Item")


def format_tick(value: float) -> str:
    """Marca de un eje: el número tal cual, sin ceros de relleno."""
    if value >= 1000:
        return f"{value:,.0f}".replace(",", THOUSANDS)
    return f"{value:g}"


def format_number(value: float) -> str:
    """Una medida con la precisión con la que se lee: `3 838`, `97.2`, `5.74`, `0.64`."""
    decimals = next(
        (digits for floor_value, digits in DECIMALS_FROM if value >= floor_value),
        SMALLEST_DECIMALS,
    )
    return f"{value:,.{decimals}f}".replace(",", THOUSANDS)


def format_average(value: float) -> str:
    """La media de un recuento: un decimal, o ninguno si ya es grande."""
    if value >= WHOLE_FROM:
        return format_number(value)
    return f"{value:.1f}"


@dataclass(frozen=True, slots=True)
class Style:
    """Aspecto de una serie. El marcador cambia con el color: quien no distinga dos
    tonos, o imprima en gris, sigue distinguiendo un círculo de un cuadrado."""

    color: str
    marker: str
    dashed: bool = False


@dataclass(frozen=True, slots=True)
class Technique:
    """Una técnica medida: su nombre en los resultados y cómo se pinta.

    `label` es lo que dice la leyenda cuando el nombre de los resultados se queda corto.
    """

    name: str
    style: Style
    label: str = ""


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
        number: cómo se escribe un valor de esta magnitud.
    """

    axis: str
    unit: str
    number: Callable[[float], str] = format_number

    def label(self, value: float) -> str:
        return f"{self.number(value)} {self.unit}"


TOTAL_TIME = Measure("tiempo total (ms)", "ms")
QUERY_TIME = Measure("tiempo por consulta (ms)", "ms")
DISK_SPACE = Measure("espacio en disco (KiB)", "KiB")
MEMORY = Measure("memoria (KiB)", "KiB")
NODES = Measure("número de nodos", "nodos", format_average)


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


@dataclass(frozen=True, slots=True)
class Scale:
    """Eje vertical de un panel: sus límites y las marcas que lleva."""

    low: float
    high: float
    ticks: tuple[float, ...]


def compare(
    operation: str,
    title: str,
    measure: Measure,
    techniques: Sequence[Technique],
    field: str | None = None,
    note: str = "",
) -> Panel:
    """Panel con una línea por técnica para una misma operación."""
    lines = tuple(
        Line(technique.label or technique.name, technique.style, technique.name, operation, field)
        for technique in techniques
    )
    return Panel(title, measure, lines, note)


HEAP = Technique("heap file", Style(BLUE, "o"))
SEQUENTIAL_FILE = Technique("archivo secuencial", Style(ORANGE, "s"))
STORAGE = (HEAP, SEQUENTIAL_FILE)

CLUSTERED = Technique("B+ agrupado", Style(BLUE, "o"))
UNCLUSTERED = Technique("B+ no agrupado", Style(ORANGE, "s"))
HASH = Technique("hash extendible", Style(AQUA, "^"))
INDEXES = (CLUSTERED, UNCLUSTERED, HASH)
ORDERED_INDEXES = (CLUSTERED, UNCLUSTERED)

SCAN = Technique("búsqueda secuencial", Style(BLUE, "o"))
RTREE = Technique("R-Tree", Style(ORANGE, "s"))
GIST = Technique("GiST (PostgreSQL)", Style(AQUA, "^"))
RTREE_INSERTED = Technique(
    "R-Tree (inserción)", Style(ORANGE, "D", dashed=True), "R-Tree, inserción uno a uno"
)
SPATIAL = (SCAN, RTREE, GIST)
SPATIAL_INDEXES = (RTREE, RTREE_INSERTED, GIST)

# Lo que hay que saber para leer cualquiera de las figuras, en dos líneas.
READING_GUIDE = (
    "Ejes logarítmicos: cada marca vale diez veces la anterior. Más abajo es mejor.\n"
    "El valor escrito al final de cada línea es el de la tabla más grande (100 000)."
)
TABLE_ROWS = "número de filas de la tabla"
TABLE_POINTS = "número de puntos de la tabla"
TREE_NODES = "nodos_del_arbol"
TREE_SIZE = "total del árbol"


def _visited(label: str, operation: str, color: str, marker: str) -> Line:
    return Line(label, Style(color, marker), "R-Tree", operation, "nodos_visitados_promedio")


def _whole_tree(operation: str) -> Line:
    return Line(TREE_SIZE, Style(MUTED_INK, "x", dashed=True), "R-Tree", operation, TREE_NODES)


FIGURES: tuple[Figure, ...] = (
    Figure(
        filename="almacenamiento",
        report="almacenamiento",
        title="Heap file vs archivo secuencial",
        subtitle=f"Costo de cada operación según el tamaño de la tabla.\n{READING_GUIDE}",
        rows_label=TABLE_ROWS,
        columns=2,
        panels=(
            compare("inserción", "Inserción de todas las filas", TOTAL_TIME, STORAGE),
            compare("búsqueda por clave", "100 búsquedas por clave", TOTAL_TIME, STORAGE),
            compare("espacio en disco", "Espacio en disco", DISK_SPACE, STORAGE, "kib"),
            compare(
                "reorganización",
                "Reorganización",
                TOTAL_TIME,
                (SEQUENTIAL_FILE,),
                note="El heap no se reorganiza:\nreutiliza los espacios libres.",
            ),
        ),
    ),
    Figure(
        filename="indices",
        report="indices",
        title="B+ agrupado vs B+ no agrupado vs hash extendible",
        subtitle=f"Costo de cada operación según el tamaño de la tabla.\n{READING_GUIDE}",
        rows_label=TABLE_ROWS,
        columns=3,
        panels=(
            compare("construcción", "Construcción del índice", TOTAL_TIME, INDEXES),
            compare("igualdad", "100 búsquedas por igualdad", TOTAL_TIME, INDEXES),
            compare(
                "rango",
                "100 búsquedas por rango",
                TOTAL_TIME,
                ORDERED_INDEXES,
                note="El hash no soporta\nbúsquedas por rango.",
            ),
            compare(
                "recorrido ordenado",
                "Recorrido ordenado de la tabla",
                TOTAL_TIME,
                ORDERED_INDEXES,
                note="El hash no mantiene\nlas claves en orden.",
            ),
            compare("espacio", "Espacio en disco", DISK_SPACE, INDEXES, field="kib"),
            compare(
                "inserciones/eliminaciones",
                "Eliminar y reinsertar el 10 %",
                TOTAL_TIME,
                INDEXES,
            ),
        ),
    ),
    Figure(
        filename="espacial-radio",
        report="espacial",
        title="Consultas por rango: búsqueda secuencial vs R-Tree vs GiST",
        subtitle=f"Tiempo promedio por consulta (100 consultas).\n{READING_GUIDE}",
        rows_label=TABLE_POINTS,
        columns=3,
        panels=(
            compare("radio 1 km", "Radio de 1 km", QUERY_TIME, SPATIAL),
            compare("radio 5 km", "Radio de 5 km", QUERY_TIME, SPATIAL),
            compare("radio 10 km", "Radio de 10 km", QUERY_TIME, SPATIAL),
        ),
    ),
    Figure(
        filename="espacial-knn",
        report="espacial",
        title="Consultas k-NN: búsqueda secuencial vs R-Tree vs GiST",
        subtitle=f"Tiempo promedio por consulta (100 consultas).\n{READING_GUIDE}",
        rows_label=TABLE_POINTS,
        columns=3,
        panels=(
            compare("k-NN k=10", "10 vecinos más cercanos", QUERY_TIME, SPATIAL),
            compare("k-NN k=50", "50 vecinos más cercanos", QUERY_TIME, SPATIAL),
            compare("k-NN k=100", "100 vecinos más cercanos", QUERY_TIME, SPATIAL),
        ),
    ),
    Figure(
        filename="espacial-indice",
        report="espacial",
        title="Índice espacial: construcción, espacio en disco y memoria",
        subtitle=f"Costo de construir, almacenar y consultar el índice.\n{READING_GUIDE}",
        rows_label=TABLE_POINTS,
        columns=3,
        panels=(
            compare("construcción", "Construcción del índice", TOTAL_TIME, SPATIAL_INDEXES),
            compare(
                "espacio del índice",
                "Espacio en disco del índice",
                DISK_SPACE,
                SPATIAL_INDEXES,
                field="kib",
            ),
            compare(
                "memoria pico",
                "Memoria pico al consultar",
                MEMORY,
                (SCAN, RTREE),
                field="kib",
                note="Solo el gestor propio\n(medido con tracemalloc).",
            ),
        ),
    ),
    Figure(
        filename="espacial-nodos",
        report="espacial",
        title="Nodos del R-Tree visitados por consulta",
        subtitle=(
            "Promedio de 100 consultas, comparado con el total de nodos del árbol.\n"
            "Ejes logarítmicos. El valor escrito es el de la tabla más grande (100 000).\n"
            "La primera fila de la leyenda es del panel izquierdo; la segunda, del derecho."
        ),
        rows_label=TABLE_POINTS,
        columns=2,
        panels=(
            Panel(
                "Consultas por rango",
                NODES,
                (
                    _visited("radio 1 km", "radio 1 km", LIGHT_BLUE, "o"),
                    _visited("radio 5 km", "radio 5 km", BLUE, "s"),
                    _visited("radio 10 km", "radio 10 km", DARK_BLUE, "^"),
                    _whole_tree("radio 1 km"),
                ),
            ),
            Panel(
                "Consultas k-NN",
                NODES,
                (
                    _visited("k = 10", "k-NN k=10", LIGHT_BLUE, "o"),
                    _visited("k = 50", "k-NN k=50", BLUE, "s"),
                    _visited("k = 100", "k-NN k=100", DARK_BLUE, "^"),
                    _whole_tree("k-NN k=10"),
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


def series_of(report: dict[str, Any], panel: Panel) -> list[tuple[Line, list[tuple[int, float]]]]:
    """Las series del panel que tienen algo que dibujar, con sus puntos."""
    series = [(line, points_of(report, line)) for line in panel.lines]
    return [(line, points) for line, points in series if points]


def ticks_between(low: float, high: float, steps: Sequence[float]) -> tuple[float, ...]:
    """Marcas `paso * 10^n` que caen entre los dos límites, de menor a mayor."""
    decades = range(floor(log10(low)), ceil(log10(high)) + 1)
    found = (step * 10.0**decade for decade in decades for step in steps)
    return tuple(tick for tick in found if low <= tick <= high)


def vertical_scale(values: Sequence[float], headroom: float) -> Scale:
    """Eje logarítmico que contiene los valores con aire por arriba y por abajo.

    `headroom` es la parte del eje que se añade arriba, sin marcas, para una nota.
    """
    lowest, highest = min(values), max(values)
    covered = log10(highest / lowest)
    padding = (
        max(covered, MINIMUM_DECADES) * VERTICAL_MARGIN + max(MINIMUM_DECADES - covered, 0.0) / 2
    )
    low, high = lowest / 10.0**padding, highest * 10.0**padding
    crowded = log10(high / low) >= DECADES_NEEDING_SUBTICKS
    ticks = ticks_between(low, high, SPARSE_TICKS if crowded else DENSE_TICKS)
    if ticks and log10(ticks[0] / low) < BASELINE_CLEARANCE * log10(high / low):
        low = 10.0 ** (
            (log10(ticks[0]) - BASELINE_CLEARANCE * log10(high)) / (1.0 - BASELINE_CLEARANCE)
        )
    return Scale(low, high * (high / low) ** headroom, ticks)


def horizontal_limits(smallest: int, largest: int, label_share: float) -> tuple[float, float]:
    """Límites del eje horizontal que dejan a la derecha del último punto la parte
    `label_share` del ancho, que es donde va su rótulo."""
    low = smallest * LEFT_MARGIN_FACTOR
    decades = log10(largest / low) / (1.0 - min(label_share, MAXIMUM_LABEL_SHARE))
    return low, low * 10.0**decades


def by_rows(items: Sequence[Item], columns: int) -> list[Item]:
    """Reordena los elementos para que una rejilla que se llena por columnas, como la
    leyenda de matplotlib, se lea por filas."""
    rows = -(-len(items) // columns)
    positions = (row * columns + column for column in range(columns) for row in range(rows))
    return [items[position] for position in positions if position < len(items)]


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


def compose(figure: Figure, report: dict[str, Any]) -> Any:
    """Monta la figura de una comparación y la devuelve sin guardarla."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": FONT_FAMILY, "font.size": LABEL_SIZE})
    rows = -(-len(figure.panels) // figure.columns)
    legend_columns = LEGEND_ENTRIES_PER_COLUMN * figure.columns
    legend_rows = -(-len(_legend_labels(figure)) // legend_columns)
    header = (
        HEADER_TOP
        + TITLE_HEIGHT
        + SUBTITLE_LINE_HEIGHT * (figure.subtitle.count("\n") + 1)
        + LEGEND_ROW_HEIGHT * legend_rows
        + HEADER_BOTTOM
    )
    width = PANEL_WIDTH * figure.columns
    height = PANEL_HEIGHT * rows + header
    canvas, grid = plt.subplots(
        rows, figure.columns, figsize=(width, height), dpi=DPI, squeeze=False
    )
    canvas.set_facecolor(SURFACE)
    axes = [axis for row in grid for axis in row]
    drawn = [
        (axis, panel)
        for axis, panel in zip(axes, figure.panels, strict=False)
        if series_of(report, panel)
    ]
    for axis in axes:
        axis.set_visible(any(axis is used for used, _ in drawn))
    scales = [_scale_of(report, panel) for _, panel in drawn]
    gutter = GUTTER_PADDING + max(
        (_text_width(canvas, format_tick(tick)) for scale in scales for tick in scale.ticks),
        default=0.0,
    )
    for (axis, panel), scale in zip(drawn, scales, strict=True):
        _draw_panel(axis, panel, report, scale, figure.rows_label, gutter)
    _draw_header(canvas, figure, height, legend_columns)
    canvas.tight_layout(
        rect=(0.0, 0.0, 1.0, 1.0 - header / height), w_pad=PANEL_GAP, h_pad=ROW_GAP
    )
    room = max(
        (
            _text_width(canvas, panel.measure.label(points[-1][1]))
            for _, panel in drawn
            for _, points in series_of(report, panel)
        ),
        default=0.0,
    )
    for axis, panel in drawn:
        _label_ends(axis, panel, report, room)
    return canvas


def draw(figure: Figure, report: dict[str, Any], output: Path) -> Path:
    """Escribe la imagen de una comparación y devuelve su ruta."""
    import matplotlib.pyplot as plt

    canvas = compose(figure, report)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{figure.filename}.png"
    canvas.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(canvas)
    return path


def _scale_of(report: dict[str, Any], panel: Panel) -> Scale:
    values = [value for _, points in series_of(report, panel) for _, value in points]
    return vertical_scale(values, NOTE_LINE_HEADROOM * len(panel.note.splitlines()))


def _text_width(canvas: Any, text: str) -> float:
    """Ancho en puntos de un texto escrito al tamaño de los rótulos."""
    from matplotlib.font_manager import FontProperties

    font = FontProperties(family=FONT_FAMILY, size=LABEL_SIZE)
    width, _, _ = canvas.canvas.get_renderer().get_text_width_height_descent(text, font, False)
    return float(width) * POINTS_PER_INCH / float(canvas.dpi)


def _legend_labels(figure: Figure) -> list[str]:
    """Series de la figura, cada una una vez, en el orden en que aparecen."""
    labels = [line.label for panel in figure.panels for line in panel.lines]
    return list(dict.fromkeys(labels))


def _draw_header(canvas: Any, figure: Figure, height: float, legend_columns: int) -> None:
    """Título, subtítulo y leyenda, alineados a la izquierda sobre los paneles."""
    left = 0.14 / canvas.get_figwidth()
    top = 1.0 - HEADER_TOP / height
    canvas.text(
        left, top, figure.title, color=INK, fontsize=TITLE_SIZE, fontweight="bold", va="top"
    )
    canvas.text(
        left,
        top - TITLE_HEIGHT / height,
        figure.subtitle,
        color=SECONDARY_INK,
        fontsize=LABEL_SIZE + 0.5,
        va="top",
        linespacing=1.4,
    )
    subtitle_lines = figure.subtitle.count("\n") + 1
    legend_top = top - (TITLE_HEIGHT + SUBTITLE_LINE_HEIGHT * subtitle_lines) / height
    handles: dict[str, Any] = {}
    for axis in canvas.axes:
        for handle, label in zip(*axis.get_legend_handles_labels(), strict=True):
            handles.setdefault(label, handle)
    columns = min(len(handles), legend_columns)
    entries = by_rows(list(handles.items()), columns)
    canvas.legend(
        [handle for _, handle in entries],
        [label for label, _ in entries],
        loc="upper left",
        bbox_to_anchor=(0.0, legend_top),
        ncols=columns,
        frameon=False,
        fontsize=LABEL_SIZE + 0.5,
        labelcolor=SECONDARY_INK,
        handlelength=2.4,
        columnspacing=1.6,
    )


def _draw_panel(
    axis: Any,
    panel: Panel,
    report: dict[str, Any],
    scale: Scale,
    rows_label: str,
    gutter: float,
) -> None:
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator
    from matplotlib.transforms import ScaledTranslation

    axis.set_facecolor(SURFACE)
    series = series_of(report, panel)
    for line, points in series:
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
    sizes = sorted({size for _, points in series for size, _ in points})
    axis.set_xscale("log")
    axis.set_yscale("log")
    axis.set_xlim(*horizontal_limits(sizes[0], sizes[-1], EXPECTED_LABEL_SHARE))
    axis.set_ylim(scale.low, scale.high)
    axis.xaxis.set_major_locator(FixedLocator(sizes))
    axis.yaxis.set_major_locator(FixedLocator(scale.ticks))
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
    axis.set_xlabel(rows_label, color=SECONDARY_INK, fontsize=LABEL_SIZE, labelpad=4)
    axis.set_ylabel(panel.measure.axis, color=SECONDARY_INK, fontsize=LABEL_SIZE)
    to_the_left = ScaledTranslation(
        -(gutter + LABEL_SIZE / 2) / POINTS_PER_INCH, 0.0, axis.figure.dpi_scale_trans
    )
    axis.yaxis.set_label_coords(0.0, 0.5, transform=axis.transAxes + to_the_left)
    axis.annotate(
        panel.title,
        xy=(0.0, 1.0),
        xycoords="axes fraction",
        xytext=(-(gutter + LABEL_SIZE), TITLE_PAD),
        textcoords="offset points",
        color=INK,
        fontsize=PANEL_TITLE_SIZE,
        ha="left",
        va="bottom",
        annotation_clip=False,
    )
    if panel.note:
        axis.annotate(
            panel.note,
            xy=(0.0, 1.0),
            xycoords="axes fraction",
            xytext=NOTE_INSET,
            textcoords="offset points",
            color=MUTED_INK,
            fontsize=LABEL_SIZE - 0.5,
            ha="left",
            va="top",
            linespacing=1.3,
            annotation_clip=False,
        )


def _label_ends(axis: Any, panel: Panel, report: dict[str, Any], room: float) -> None:
    """Rotula el valor de cada serie en su último punto, que es el que se compara.

    Solo el extremo: un número junto a cada punto no lo lee nadie. `room` es el ancho del
    rótulo más largo de la figura: todos los paneles le dejan el mismo sitio, de modo que
    los tres tamaños caen en el mismo lugar en todos. Si dos extremos casi coinciden, los
    rótulos se separan y se unen a su punto con un trazo fino.
    """
    series = series_of(report, panel)
    ends = [points[-1] for _, points in series]
    sizes = [size for _, points in series for size, _ in points]
    to_points = POINTS_PER_INCH / axis.figure.dpi
    reserved = END_LABEL_OFFSET + LEADER_EXTRA + room + END_LABEL_PADDING
    share = reserved / (axis.get_window_extent().width * to_points)
    axis.set_xlim(*horizontal_limits(min(sizes), max(sizes), share))
    wanted = [axis.transData.transform(end)[1] * to_points for end in ends]
    heights = spread_labels(wanted, END_LABEL_GAP)
    for end, anchor, height in zip(ends, wanted, heights, strict=True):
        displacement = height - anchor
        moved = abs(displacement) > LEADER_THRESHOLD
        axis.annotate(
            panel.measure.label(end[1]),
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
