"""El experimento espacial, en pequeño y sin PostgreSQL.

No mide nada: comprueba que el experimento sigue funcionando y, de paso, que el recorrido
secuencial y el R-Tree responden lo mismo sobre un conjunto de puntos que ningún otro test
usa. El propio experimento aborta si no coinciden.
"""

from pathlib import Path

import pytest
from benchmarks.spatial_benchmark import (
    BUILD,
    INDEX_SPACE,
    REGION,
    RTREE,
    RTREE_INSERTED,
    SEQUENTIAL,
    SYNTHETIC_DATA,
    build_queries,
    build_workload,
    load_points,
    run,
    sampled_workloads,
)

from spatial.geometry import Point

SEED = 7
SIZE = 400
QUERIES = 6
REAL_POINTS = 300
REAL_SIZE = 250


def test_workload_is_deterministic_and_inside_the_region():
    first = build_workload(SIZE, QUERIES, SEED)
    second = build_workload(SIZE, QUERIES, SEED)
    assert first == second
    assert first.size == SIZE
    assert len(first.centers) == QUERIES
    assert all(REGION.contains_point(point) for _, point in first.points)
    assert build_workload(SIZE, QUERIES, SEED + 1) != first


def test_queries_cover_every_radius_and_neighbour_count():
    operations = [query.operation for query in build_queries([1.0, 2.5], [10])]
    assert operations == ["radio 1 km", "radio 2.5 km", "k-NN k=10"]


def test_both_engine_techniques_are_measured_and_agree(tmp_path: Path):
    report = run(
        sizes=[SIZE],
        queries=QUERIES,
        seed=SEED,
        radii_km=[2.0, 8.0],
        neighbours=[5, 40],
        postgres_dsn=None,
        workspace=tmp_path,
    )
    measured = {(item.technique, item.operation) for item in report.measurements}
    for technique in (SEQUENTIAL, RTREE):
        for operation in ("radio 2 km", "radio 8 km", "k-NN k=5", "k-NN k=40"):
            assert (technique, operation) in measured
    assert {(RTREE, BUILD), (RTREE_INSERTED, BUILD), (RTREE, INDEX_SPACE)} <= measured
    by_radius = {
        item.technique: item.extra["filas_promedio"]
        for item in report.measurements
        if item.operation == "radio 8 km"
    }
    assert by_radius[SEQUENTIAL] == by_radius[RTREE] > 0
    visits = next(
        item.extra
        for item in report.measurements
        if item.technique == RTREE and item.operation == "k-NN k=5"
    )
    assert 0 < visits["nodos_visitados_promedio"] <= visits["nodos_del_arbol"]


def _write_places(path: Path, count: int) -> list[Point]:
    """Un CSV como el del descargador: puntos distintos, con uno repetido y otro vacío."""
    points = [Point(-12.0 - number * 0.001, -77.0 + (number % 17) * 0.002) for number in range(count)]
    lines = ["id,nombre,ubicacion"]
    lines += [
        f'{number},lugar {number},"POINT({point.lat!r}, {point.lon!r})"'
        for number, point in enumerate(points)
    ]
    lines.append(f'{count},repetido,"POINT({points[0].lat!r}, {points[0].lon!r})"')
    lines.append(f"{count + 1},sin ubicación,")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return points


def test_points_are_read_once_each_in_file_order(tmp_path: Path):
    written = _write_places(tmp_path / "lugares.csv", REAL_POINTS)
    assert load_points(tmp_path / "lugares.csv") == written


def test_a_file_without_a_point_column_is_rejected(tmp_path: Path):
    plain = tmp_path / "plano.csv"
    plain.write_text("id,nombre\n1,uno\n", encoding="utf-8")
    with pytest.raises(ValueError, match="POINT"):
        load_points(plain)


def test_real_workloads_sample_the_file_without_repeating_points(tmp_path: Path):
    written = _write_places(tmp_path / "lugares.csv", REAL_POINTS)
    build = sampled_workloads(written)
    first = build(REAL_SIZE, QUERIES, SEED)
    assert first == build(REAL_SIZE, QUERIES, SEED)
    assert first != build(REAL_SIZE, QUERIES, SEED + 1)
    sampled = [point for _, point in first.points]
    assert len(set(sampled)) == REAL_SIZE
    assert set(sampled) <= set(written)
    assert [number for number, _ in first.points] == list(range(REAL_SIZE))
    assert len(first.centers) == QUERIES


def test_asking_for_more_points_than_the_file_has_is_an_error(tmp_path: Path):
    build = sampled_workloads(_write_places(tmp_path / "lugares.csv", REAL_POINTS))
    with pytest.raises(ValueError, match=str(REAL_POINTS)):
        build(REAL_POINTS + 1, QUERIES, SEED)


def test_the_experiment_runs_on_points_from_a_file(tmp_path: Path):
    _write_places(tmp_path / "lugares.csv", REAL_POINTS)
    report = run(
        sizes=[REAL_SIZE],
        queries=QUERIES,
        seed=SEED,
        radii_km=[5.0],
        neighbours=[10],
        postgres_dsn=None,
        workspace=tmp_path / "trabajo",
        points_file=tmp_path / "lugares.csv",
    )
    assert report.parameters["datos"] == "lugares.csv"
    found = {
        item.technique: item.extra["filas_promedio"]
        for item in report.measurements
        if item.operation == "radio 5 km"
    }
    assert found[SEQUENTIAL] == found[RTREE] > 0


def test_synthetic_runs_say_so(tmp_path: Path):
    report = run(
        sizes=[SIZE],
        queries=QUERIES,
        seed=SEED,
        radii_km=[2.0],
        neighbours=[5],
        postgres_dsn=None,
        workspace=tmp_path,
    )
    assert report.parameters["datos"] == SYNTHETIC_DATA
