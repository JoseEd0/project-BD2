"""Lo que se dibuja, declarado como datos: técnicas, series, magnitudes, paneles y figuras.

Hay dos clases principales de figura: `Figure`, con una línea por técnica frente al tamaño
de los datos en ejes logarítmicos, y `BarFigure`, con barras agrupadas en un eje lineal de 0
a 1 para medidas que son fracciones, como la precisión. `BreakdownFigure` desglosa en barras
apiladas las cifras de una sola ejecución, `XYFigure` dibuja curvas de dos magnitudes
cualesquiera guardadas como listas, como una ROC, e `IntervalFigure` pone cada medida con su
intervalo de confianza.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from benchmarks.plots.style import format_average, format_number

# Lo que hay que saber para leer cualquiera de las figuras, en dos líneas.
READING_GUIDE = (
    "Ejes logarítmicos: cada marca vale diez veces la anterior. Más abajo es mejor.\n"
    "El valor escrito al final de cada línea es el de la tabla más grande (100 000)."
)
TABLE_ROWS = "número de filas de la tabla"


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
    `legend_columns` fija cuántas series caben en una fila de la leyenda cuando sus nombres
    son largos; con 0, dos por columna de paneles.
    """

    filename: str
    report: str
    title: str
    subtitle: str
    rows_label: str
    columns: int
    panels: tuple[Panel, ...]
    legend_columns: int = 0


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


@dataclass(frozen=True, slots=True)
class BarGroup:
    """Un grupo de barras: la operación de los resultados y su rótulo en el eje."""

    label: str
    operation: str


@dataclass(frozen=True, slots=True)
class BarPanel:
    """Barras agrupadas de una fracción: un grupo por operación y una barra por técnica.

    `title` puede citar los parámetros de la ejecución, como `Precision@{k}`; `field` es la
    clave de `extra` de donde sale cada valor.
    """

    title: str
    axis: str
    field: str
    groups: tuple[BarGroup, ...]
    techniques: tuple[Technique, ...]


@dataclass(frozen=True, slots=True)
class BarFigure:
    """Una imagen de barras: una columna por panel y una fila por cada tamaño medido.

    `sizes_label` dice qué cuenta el tamaño en el título de cada panel: documentos, filas.
    """

    filename: str
    report: str
    title: str
    subtitle: str
    sizes_label: str
    panels: tuple[BarPanel, ...]


@dataclass(frozen=True, slots=True)
class CurvePanel:
    """Una curva de calidad contra latencia por técnica, con las medidas de `operation`
    en el tamaño más grande que se midió."""

    title: str
    operation: str
    techniques: tuple[Technique, ...]


@dataclass(frozen=True, slots=True)
class CurveFigure:
    """Una imagen de curvas: la latencia en un eje logarítmico y una fracción de 0 a 1 en
    el otro; cada punto es un esfuerzo de búsqueda.

    `field` es la clave de `extra` de la fracción, `order_field` la del esfuerzo, que une
    los puntos en orden, y `default_field` la que marca el del esfuerzo por omisión, que
    se dibuja con un anillo. `sizes_label` dice qué cuenta el tamaño del título y
    `legend_columns`, como en `Figure`, cuántas series van en una fila de la leyenda.
    """

    filename: str
    report: str
    title: str
    subtitle: str
    columns: int
    x_axis: str
    y_axis: str
    field: str
    order_field: str
    default_field: str
    sizes_label: str
    panels: tuple[CurvePanel, ...]
    legend_columns: int = 0


@dataclass(frozen=True, slots=True)
class GalleryFigure:
    """Una galería: por fila, una consulta y sus primeros vecinos, con el borde del color
    de si comparten su clase.

    `cases` son las claves de `extra` con las listas de casos (éxitos, errores) y `images`
    dice si cada celda muestra el archivo o solo su clase, como con el audio.
    """

    filename: str
    report: str
    title: str
    subtitle: str
    technique: str
    operation: str
    cases: tuple[tuple[str, str], ...]
    images: bool


@dataclass(frozen=True, slots=True)
class Segment:
    """Un tramo de una barra apilada: la clave de `extra` de donde sale y su color."""

    label: str
    field: str
    color: str


@dataclass(frozen=True, slots=True)
class StackedBar:
    """Una barra: su rótulo y la medida de la que salen sus tramos."""

    label: str
    technique: str
    operation: str


@dataclass(frozen=True, slots=True)
class BreakdownPanel:
    """Barras horizontales apiladas en un eje lineal, una por paso o por objeto; cada una
    suma los tramos de `segments` que tenga su medida."""

    title: str
    measure: Measure
    bars: tuple[StackedBar, ...]
    segments: tuple[Segment, ...]


@dataclass(frozen=True, slots=True)
class BreakdownFigure:
    """Una imagen de desgloses: un panel por columna, para cifras de una sola ejecución
    (cuánto tardó cada paso, cuánto ocupa cada tabla) en vez de una serie por tamaño."""

    filename: str
    report: str
    title: str
    subtitle: str
    panels: tuple[BreakdownPanel, ...]


@dataclass(frozen=True, slots=True)
class Axis:
    """Un eje de un panel XY: su rótulo, con la magnitud y su unidad, la clave de `extra`
    de donde salen sus valores y si es logarítmico."""

    label: str
    field: str
    logarithmic: bool = False


@dataclass(frozen=True, slots=True)
class Series:
    """Una serie de un panel XY: las medidas de `technique` y `operation`, cuyos `extra`
    traen en los campos de los ejes una lista de valores o uno solo. `points_only` la
    dibuja como puntos sueltos, para marcar puntos de operación sobre una curva."""

    label: str
    style: Style
    technique: str
    operation: str
    points_only: bool = False


@dataclass(frozen=True, slots=True)
class XYPanel:
    title: str
    x: Axis
    y: Axis
    series: tuple[Series, ...]


@dataclass(frozen=True, slots=True)
class XYFigure:
    """Una imagen de curvas con cualquier par de magnitudes, como una ROC (TAR contra FAR)
    o una tasa a lo largo del tiempo; `legend_columns`, como en `Figure`."""

    filename: str
    report: str
    title: str
    subtitle: str
    columns: int
    panels: tuple[XYPanel, ...]
    legend_columns: int = 0


# Las claves de `extra` con los extremos del intervalo de una medida `campo`: `campo_bajo` y
# `campo_alto`, como las guarda la evaluación de la tienda.
LOW_SUFFIX = "_bajo"
HIGH_SUFFIX = "_alto"


@dataclass(frozen=True, slots=True)
class Shade:
    """Qué clase de barra es (una señal sola, una fusión, una referencia): su rótulo en la
    leyenda y su color."""

    label: str
    color: str


@dataclass(frozen=True, slots=True)
class IntervalBar:
    """Una barra con su intervalo: su rótulo, la medida de la que sale y su clase."""

    label: str
    technique: str
    operation: str
    shade: Shade


@dataclass(frozen=True, slots=True)
class IntervalPanel:
    """Barras horizontales de una medida con su intervalo de confianza: el valor sale de
    `field` y los extremos, de `field` con `LOW_SUFFIX` y `HIGH_SUFFIX`. Con `label_field`,
    el rótulo de cada barra sale de esa clave de su medida (lo que solo se sabe al medir,
    como los pesos ganadores de una búsqueda)."""

    title: str
    axis: str
    field: str
    bars: tuple[IntervalBar, ...]
    label_field: str | None = None

    @property
    def low_field(self) -> str:
        return f"{self.field}{LOW_SUFFIX}"

    @property
    def high_field(self) -> str:
        return f"{self.field}{HIGH_SUFFIX}"


@dataclass(frozen=True, slots=True)
class IntervalFigure:
    """Una imagen de medidas con intervalo, de una sola ejecución: los paneles van en filas
    de `columns`, y cada uno tiene su propio eje lineal desde 0."""

    filename: str
    report: str
    title: str
    subtitle: str
    columns: int
    panels: tuple[IntervalPanel, ...]
