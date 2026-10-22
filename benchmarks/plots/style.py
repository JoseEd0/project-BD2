"""Aspecto de las figuras: tinta, colores, medidas de la página y cómo se escribe cada número."""

from __future__ import annotations

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
