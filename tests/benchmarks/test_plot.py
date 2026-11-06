"""Las figuras: qué puntos se dibujan y dónde caen los rótulos.

Dibujar de verdad solo se prueba si matplotlib está instalado, que es opcional.
"""

from itertools import pairwise
from math import log10
from pathlib import Path

import pytest
from benchmarks.common import RESULTS_DIRECTORY
from benchmarks.plots import FIGURES
from benchmarks.plots.drawing import (
    bar_sizes,
    bar_value,
    by_rows,
    compose,
    draw,
    horizontal_limits,
    load,
    points_of,
    short_label,
    spread_labels,
    ticks_between,
    title_detail,
    vertical_scale,
)
from benchmarks.plots.model import (
    DISK_SPACE,
    NODES,
    TOTAL_TIME,
    BarFigure,
    BarGroup,
    BarPanel,
    Figure,
    Line,
    Style,
    Technique,
    compare,
)
from benchmarks.plots.style import BASELINE_CLEARANCE, BLUE, format_number

RESULTS = Path(__file__).parents[2] / "benchmarks" / RESULTS_DIRECTORY
# Holgura, en píxeles, al comprobar que un texto cabe: lo que redondea el dibujo.
SLACK = 1.0
# Aire mínimo, en píxeles, entre una nota y la línea más alta de la rejilla.
NOTE_CLEARANCE = 4.0

REPORT = {
    "name": "prueba",
    "measurements": [
        {"technique": "a", "operation": "leer", "dataset_size": 10_000, "milliseconds": 4.0, "extra": {}},
        {"technique": "a", "operation": "leer", "dataset_size": 1_000, "milliseconds": 2.0, "extra": {}},
        {"technique": "b", "operation": "leer", "dataset_size": 1_000, "milliseconds": 0.0, "extra": {"nota": "no soportado"}},
        {"technique": "a", "operation": "espacio", "dataset_size": 1_000, "milliseconds": 0.0, "extra": {"kib": 64.0}},
        {"technique": "b", "operation": "espacio", "dataset_size": 1_000, "milliseconds": 0.0, "extra": {"kib": 0.0}},
    ],
}
STYLE = Style(BLUE, "o")


def test_points_come_sorted_by_dataset_size():
    assert points_of(REPORT, Line("a", STYLE, "a", "leer")) == [(1_000, 2.0), (10_000, 4.0)]


def test_points_can_come_from_an_extra_field():
    assert points_of(REPORT, Line("a", STYLE, "a", "espacio", "kib")) == [(1_000, 64.0)]


def test_unsupported_operations_and_zeros_are_left_out():
    """Un cero es «no lo hace» o «no ocupa»: no tiene sitio en un eje logarítmico."""
    assert points_of(REPORT, Line("b", STYLE, "b", "leer")) == []
    assert points_of(REPORT, Line("b", STYLE, "b", "espacio", "kib")) == []


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (47623.26, "47\N{NARROW NO-BREAK SPACE}623"),
        (751.4, "751"),
        (95.37, "95.4"),
        (11.0, "11.0"),
        (8.03, "8.03"),
        (4.2, "4.20"),
        (0.644, "0.64"),
        (0.058, "0.058"),
    ],
)
def test_numbers_are_written_like_in_the_report_tables(value: float, text: str):
    assert format_number(value) == text


@pytest.mark.parametrize(("value", "text"), [(610.0, "610 nodos"), (17.42, "17.4 nodos"), (5.0, "5.0 nodos")])
def test_an_average_count_keeps_one_decimal(value: float, text: str):
    assert NODES.label(value) == text


def test_a_value_is_labelled_with_its_unit():
    assert TOTAL_TIME.label(3838.2) == "3\N{NARROW NO-BREAK SPACE}838 ms"
    assert DISK_SPACE.label(64.0) == "64.0 KiB"


def test_ticks_are_the_round_numbers_inside_the_limits():
    assert ticks_between(3.0, 2500.0, (1.0,)) == (10.0, 100.0, 1000.0)
    assert ticks_between(0.8, 30.0, (1.0, 2.0, 5.0)) == (1.0, 2.0, 5.0, 10.0, 20.0)


def test_the_vertical_scale_holds_every_value_with_room_to_spare():
    scale = vertical_scale([7.66, 744.0, 3838.0], headroom=0.0)
    assert scale.low < 7.66 and scale.high > 3838.0
    assert scale.ticks == (10.0, 100.0, 1000.0, 10000.0)


def test_a_narrow_range_gets_intermediate_ticks():
    assert vertical_scale([3.41, 53.5], headroom=0.0).ticks == (5.0, 10.0, 20.0, 50.0)


def test_a_single_value_still_gets_a_whole_decade():
    scale = vertical_scale([64.0], headroom=0.0)
    assert log10(scale.high / scale.low) >= 1.0
    assert scale.low < 64.0 < scale.high


def test_the_lowest_tick_never_sits_on_the_baseline():
    """27.1 con margen deja la base un poco por debajo de 10: se baja hasta separarla."""
    scale = vertical_scale([27.1, 49742.0], headroom=0.0)
    assert scale.ticks[0] == 10.0
    gap = log10(scale.ticks[0] / scale.low) / log10(scale.high / scale.low)
    assert gap == pytest.approx(BASELINE_CLEARANCE)


def test_headroom_adds_a_band_without_ticks_above_the_values():
    plain = vertical_scale([2.63, 194.0], headroom=0.0)
    roomy = vertical_scale([2.63, 194.0], headroom=0.34)
    assert roomy.low == plain.low and roomy.high > plain.high
    assert roomy.ticks == plain.ticks
    assert max(roomy.ticks) <= plain.high


def test_the_last_point_leaves_the_asked_share_of_the_axis_free():
    low, high = horizontal_limits(1_000, 100_000, 0.4)
    assert low < 1_000
    assert log10(high / 100_000) / log10(high / low) == pytest.approx(0.4)


def test_a_grid_filled_by_columns_reads_by_rows():
    """Con 7 entradas en 4 columnas, matplotlib llena columnas de 2, 2, 2 y 1."""
    assert by_rows(list("abcdefg"), 4) == list("aebfcgd")
    assert by_rows(list("abc"), 3) == list("abc")


def test_labels_that_do_not_collide_stay_where_they_are():
    assert spread_labels([10.0, 50.0, 90.0], gap=11.0) == [10.0, 50.0, 90.0]


def test_colliding_labels_are_spread_around_their_middle():
    assert spread_labels([40.0, 42.0], gap=10.0) == [36.0, 46.0]


def test_spreading_keeps_the_order_and_the_minimum_gap():
    positions = [30.0, 31.0, 29.0, 80.0, 33.0]
    spread = spread_labels(positions, gap=12.0)
    order = sorted(range(len(positions)), key=positions.__getitem__)
    placed = [spread[index] for index in order]
    assert placed == sorted(placed)
    assert all(later - earlier >= 12.0 - 1e-9 for earlier, later in pairwise(placed))
    assert spread[3] == 80.0


LINE_FIGURES = [figure for figure in FIGURES if isinstance(figure, Figure)]
BAR_FIGURES = [figure for figure in FIGURES if isinstance(figure, BarFigure)]


def test_every_figure_has_a_distinct_file_and_fits_its_grid():
    names = [figure.filename for figure in FIGURES]
    assert len(names) == len(set(names))
    for figure in LINE_FIGURES:
        assert figure.panels
        assert all(panel.lines for panel in figure.panels)
    for bars in BAR_FIGURES:
        assert bars.panels
        assert all(panel.groups and panel.techniques for panel in bars.panels)


def test_every_panel_says_what_it_measures_and_in_which_unit():
    """Un panel se lee solo: el eje vertical nombra la magnitud y, si tiene unidad, es la
    misma que acompaña a cada valor rotulado."""
    for bars in BAR_FIGURES:
        assert bars.sizes_label and all(panel.axis for panel in bars.panels)
    for figure in LINE_FIGURES:
        assert figure.rows_label
        for panel in figure.panels:
            assert panel.title and panel.measure.axis and panel.measure.unit
            assert panel.measure.unit in panel.measure.axis


def test_a_technique_can_be_named_differently_in_the_legend():
    panel = compare("leer", "Leer", TOTAL_TIME, (Technique("a", STYLE, "la técnica a"),))
    assert panel.lines == (Line("la técnica a", STYLE, "a", "leer"),)


BAR_REPORT = {
    "name": "barras",
    "parameters": {"k": 10},
    "measurements": [
        {"technique": "a", "operation": "corta", "dataset_size": 100, "milliseconds": 1.0, "extra": {"precision": 0.5}},
        {"technique": "a", "operation": "larga", "dataset_size": 100, "milliseconds": 1.0, "extra": {"precision": 0.25}},
        {"technique": "b", "operation": "corta", "dataset_size": 100, "milliseconds": 1.0, "extra": {"precision": 1.0}},
        {"technique": "a", "operation": "corta", "dataset_size": 1_000, "milliseconds": 1.0, "extra": {"precision": 0.0}},
        {"technique": "a", "operation": "construcción", "dataset_size": 5_000, "milliseconds": 9.0, "extra": {}},
    ],
}
BARS = BarFigure(
    filename="barras",
    report="barras",
    title="Barras",
    subtitle="Subtítulo",
    sizes_label="documentos",
    panels=(
        BarPanel(
            "Precision@{k}",
            "Precision@k",
            "precision",
            (BarGroup("corta", "corta"), BarGroup("larga", "larga")),
            (Technique("a", STYLE), Technique("b", Style(BLUE, "s"))),
        ),
    ),
)


def test_a_bar_takes_its_value_from_the_field_and_a_missing_one_is_none():
    assert bar_value(BAR_REPORT, "a", "larga", 100, "precision") == 0.25
    assert bar_value(BAR_REPORT, "a", "corta", 1_000, "precision") == 0.0
    assert bar_value(BAR_REPORT, "b", "larga", 100, "precision") is None
    assert bar_value(BAR_REPORT, "a", "construcción", 5_000, "precision") is None


def test_a_bar_figure_has_one_row_per_size_that_has_the_field():
    assert bar_sizes(BAR_REPORT, BARS) == [100, 1_000]


def test_a_bar_figure_is_written_to_disk_with_a_label_per_bar(tmp_path: Path):
    pytest.importorskip("matplotlib")
    from matplotlib import pyplot

    canvas = compose(BARS, BAR_REPORT)
    labels = [text.get_text() for axis in canvas.axes for text in axis.texts]
    assert sorted(labels) == ["0.00", "0.25", "0.50", "1.00"]
    assert canvas.axes[0].get_title(loc="left") == "Precision@10 · 100 documentos"
    pyplot.close(canvas)
    path = draw(BARS, BAR_REPORT, tmp_path)
    assert path.stat().st_size > 10_000


def test_a_figure_is_written_to_disk(tmp_path: Path):
    pytest.importorskip("matplotlib")
    figure = Figure(
        filename="prueba",
        report="prueba",
        title="Título",
        subtitle="Subtítulo\nen dos líneas",
        rows_label="filas",
        columns=2,
        panels=(
            compare("leer", "Leer", TOTAL_TIME, (Technique("a", STYLE), Technique("b", Style(BLUE, "s")))),
            compare("espacio", "Espacio", DISK_SPACE, (Technique("a", STYLE),), field="kib", note="nota"),
            compare("borrar", "Nadie lo midió", TOTAL_TIME, (Technique("a", STYLE),)),
        ),
    )
    path = draw(figure, REPORT, tmp_path)
    assert path == tmp_path / "prueba.png"
    assert path.stat().st_size > 10_000


@pytest.mark.parametrize("figure", FIGURES, ids=lambda figure: figure.filename)
def test_no_text_is_cut_off_or_runs_into_the_next_panel(figure: Figure):
    """Con los resultados versionados: todo texto cabe en la imagen, y lo que se escribe
    dentro de un panel —título, nota, valores— no pasa de su borde derecho."""
    pytest.importorskip("matplotlib")
    from matplotlib import pyplot
    from matplotlib.text import Text

    canvas = compose(figure, load(RESULTS / f"{figure.report}.json"))
    canvas.canvas.draw()
    renderer = canvas.canvas.get_renderer()
    width, height = canvas.canvas.get_width_height()
    for text in canvas.findobj(Text):
        if not text.get_text().strip() or not text.get_visible():
            continue
        box = Text.get_window_extent(text, renderer)
        assert box.x0 >= -SLACK and box.x1 <= width + SLACK, text.get_text()
        assert box.y0 >= -SLACK and box.y1 <= height + SLACK, text.get_text()
    for axis in canvas.axes:
        edge = axis.get_window_extent(renderer).x1
        for text in axis.texts:
            assert Text.get_window_extent(text, renderer).x1 <= edge + SLACK, text.get_text()
    pyplot.close(canvas)


@pytest.mark.parametrize("figure", LINE_FIGURES, ids=lambda figure: figure.filename)
def test_a_note_stays_clear_of_the_grid(figure: Figure):
    """La nota de un panel va en una franja sin marcas: queda por encima de la rejilla."""
    pytest.importorskip("matplotlib")
    from matplotlib import pyplot
    from matplotlib.text import Text

    canvas = compose(figure, load(RESULTS / f"{figure.report}.json"))
    canvas.canvas.draw()
    renderer = canvas.canvas.get_renderer()
    for axis, panel in zip(canvas.axes, figure.panels, strict=True):
        notes = [text for text in axis.texts if panel.note and text.get_text() == panel.note]
        highest_tick = max(axis.get_yticks())
        grid_line = axis.transData.transform((axis.get_xlim()[0], highest_tick))[1]
        for note in notes:
            assert Text.get_window_extent(note, renderer).y0 > grid_line + NOTE_CLEARANCE
    pyplot.close(canvas)


def test_a_gallery_label_is_readable_and_fits_its_cell():
    assert short_label("CELLULAR_PHONE_CASE") == "cellular phone case"
    assert short_label("FINENECKLACEBRACELETANKLET") == "finenecklacebracele\N{HORIZONTAL ELLIPSIS}"


def test_a_clip_is_captioned_with_what_its_title_has_in_parentheses():
    assert title_detail("six (33903159, toma 0)") == "33903159, toma 0"
    assert title_detail("sin detalle") == ""
