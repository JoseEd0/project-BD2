"""Lo que se dibuja, declarado como datos: técnicas, series, magnitudes, paneles y figuras."""

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
