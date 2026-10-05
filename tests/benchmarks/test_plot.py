"""Las figuras: qué puntos se dibujan y dónde caen los rótulos.

Dibujar de verdad solo se prueba si matplotlib está instalado, que es opcional.
"""

from itertools import pairwise
from pathlib import Path

import pytest
from benchmarks.plot import (
    BLUE,
    DISK_SPACE,
    FIGURES,
    TOTAL_TIME,
    Figure,
    Line,
    Style,
    compare,
    draw,
    format_number,
    points_of,
    spread_labels,
)

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
    [(47623.26, "47\N{NARROW NO-BREAK SPACE}623"), (751.4, "751"), (95.37, "95.4"), (8.03, "8.03"), (4.2, "4.2"), (0.134, "0.134")],
)
def test_numbers_keep_three_readable_figures(value: float, text: str):
    assert format_number(value) == text


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


def test_every_figure_has_a_distinct_file_and_fits_its_grid():
    names = [figure.filename for figure in FIGURES]
    assert len(names) == len(set(names))
    for figure in FIGURES:
        assert figure.panels
        assert all(panel.lines for panel in figure.panels)


def test_every_panel_says_what_it_measures_and_in_which_unit():
    """Un panel se lee solo: el eje vertical nombra la magnitud y, si tiene unidad, es la
    misma que acompaña a cada valor rotulado."""
    for figure in FIGURES:
        assert figure.rows_label
        for panel in figure.panels:
            assert panel.title and panel.measure.axis and panel.measure.unit
            assert panel.measure.unit in panel.measure.axis


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
            compare("leer", "Leer", TOTAL_TIME, (("a", STYLE), ("b", Style(BLUE, "s")))),
            compare("espacio", "Espacio", DISK_SPACE, (("a", STYLE),), field="kib", note="nota"),
        ),
    )
    path = draw(figure, REPORT, tmp_path)
    assert path == tmp_path / "prueba.png"
    assert path.stat().st_size > 10_000
