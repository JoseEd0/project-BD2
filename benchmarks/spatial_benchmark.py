"""Comparación experimental: búsqueda secuencial vs R-Tree vs GiST de PostgreSQL.

Mide lo que pide el enunciado: consultas por radio y de vecinos más cercanos sobre
conjuntos de varios tamaños, el tiempo de construcción del índice y el espacio que ocupa.

    .venv/bin/python -m benchmarks.spatial_benchmark --sizes 1000 10000 100000 --queries 100

Las tres técnicas responden **las mismas consultas sobre los mismos puntos**, y antes de
anotar ningún tiempo se comprueba que devuelven las mismas filas: un índice que acelera
cambiando la respuesta no es una comparación.

Con `--points-file` los puntos son reales: se toman de un CSV con una columna POINT, como
el que deja `demos/descargar_lugares.py` con los puntos de interés de OpenStreetMap. Sin
ese argumento se generan puntos sintéticos sobre Lima, que no necesitan descargar nada.

    .venv/bin/python demos/descargar_lugares.py --salida data/datasets/lugares_peru.csv
    .venv/bin/python -m benchmarks.spatial_benchmark --sizes 1000 10000 100000 \\
        --points-file data/datasets/lugares_peru.csv

GiST se mide contra un PostgreSQL con PostGIS, usando el tipo `geography` para que la
distancia sea geodésica como la del motor. La conexión se toma de `--postgres-dsn` o de la
variable `BENCH_POSTGRES_DSN`; sin ninguna de las dos se miden solo las dos técnicas del
motor. Requiere `pip install -e ".[postgres]"`.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import tempfile
import tracemalloc
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from benchmarks.common import BYTES_PER_KIB, Report, directory_size, results_directory, time_it
from config import EngineConfig
from index.rtree import RTree, SearchStats, SpatialIndex
from query.engine import Engine
from query.loader import infer_schema, read_values
from spatial.geometry import Point, Rectangle
from spatial.metrics import HAVERSINE
from storage.record_id import RecordId
from storage.types import FieldType

BENCHMARK_NAME = "espacial"
DEFAULT_SIZES = (1000, 10000)
DEFAULT_QUERIES = 100
DEFAULT_SEED = 20261004
DEFAULT_RADII_KM = (1.0, 5.0, 10.0)
DEFAULT_NEIGHBOURS = (10, 50, 100)
POSTGRES_DSN_VARIABLE = "BENCH_POSTGRES_DSN"

SEQUENTIAL = "búsqueda secuencial"
RTREE = "R-Tree"
RTREE_INSERTED = "R-Tree (inserción)"
GIST = "GiST (PostgreSQL)"

BUILD = "construcción"
INDEX_SPACE = "espacio del índice"
PEAK_MEMORY = "memoria pico"

SYNTHETIC_DATA = "sintéticos (Lima)"
TABLE = "puntos"
INDEX = "idx_puntos_ubicacion"
METRES_PER_KM = 1000.0

# Puntos sobre el área metropolitana de Lima. La mayoría se agrupa alrededor de unos pocos
# centros, como una ciudad real: con puntos uniformes un índice espacial nunca encuentra
# zonas vacías que descartar ni zonas densas donde lucirse.
REGION = Rectangle(min_lat=-12.25, min_lon=-77.15, max_lat=-11.85, max_lon=-76.85)
CLUSTER_COUNT = 12
CLUSTERED_SHARE = 0.8
CLUSTER_SPREAD_DEGREES = 0.012
QUERY_JITTER_DEGREES = 0.002
MEMORY_SAMPLE_QUERIES = 5
FAKE_SLOTS_PER_PAGE = 100

Answers = dict[str, list[list[int]]]
WorkloadSource = Callable[[int, int, int], "Workload"]


@dataclass(frozen=True, slots=True)
class Workload:
    """Los puntos de un tamaño de conjunto y los centros de sus consultas."""

    points: list[tuple[int, Point]]
    centers: list[Point]

    @property
    def size(self) -> int:
        return len(self.points)


@dataclass(frozen=True, slots=True)
class Query:
    """Una consulta del experimento, escrita para el motor y para PostGIS."""

    operation: str
    engine_sql: Callable[[Point], str]
    postgres_sql: str
    postgres_parameters: Callable[[Point], tuple[Any, ...]]
    replay: Callable[[SpatialIndex, Point, SearchStats], None]
    ordered: bool


def build_workload(size: int, queries: int, seed: int) -> Workload:
    """Puntos sintéticos y centros de consulta deterministas para una semilla dada."""
    generator = random.Random(seed + size)
    centers = [_uniform_point(generator) for _ in range(CLUSTER_COUNT)]
    points = [(number, _clustered_point(generator, centers)) for number in range(size)]
    asked = [_near(generator, generator.choice(points)[1], REGION) for _ in range(queries)]
    return Workload(points=points, centers=asked)


def load_points(path: Path) -> list[Point]:
    """Puntos distintos de la primera columna POINT de un CSV, en el orden del archivo.

    Dos filas en el mismo punto se cuentan una vez: no añaden nada a un índice espacial y
    dejarían sin definir cuál de las dos es «la décima más cercana».

    Raises:
        ValueError: si el archivo no tiene ninguna columna POINT.
    """
    config = EngineConfig()
    schema = infer_schema(path, config)
    position = next(
        (number for number, field in enumerate(schema) if field.type is FieldType.POINT), None
    )
    if position is None:
        raise ValueError(f"'{path.name}' no tiene ninguna columna POINT")
    distinct: dict[Point, None] = {}
    for row in read_values(path, schema, config):
        point = row[position]
        if isinstance(point, Point):
            distinct.setdefault(point)
    return list(distinct)


def sampled_workloads(points: Sequence[Point]) -> WorkloadSource:
    """Cargas de trabajo tomadas de puntos reales: una muestra por tamaño y, como centros
    de consulta, lugares junto a puntos de esa muestra."""

    def build(size: int, queries: int, seed: int) -> Workload:
        if size > len(points):
            raise ValueError(f"se pidieron {size} puntos y el archivo tiene {len(points)}")
        generator = random.Random(seed + size)
        chosen = generator.sample(points, size)
        region = Rectangle.enclosing(map(Rectangle.around, chosen))
        asked = [_near(generator, generator.choice(chosen), region) for _ in range(queries)]
        return Workload(points=list(enumerate(chosen)), centers=asked)

    return build


def _uniform_point(generator: random.Random) -> Point:
    return Point(
        generator.uniform(REGION.min_lat, REGION.max_lat),
        generator.uniform(REGION.min_lon, REGION.max_lon),
    )


def _clustered_point(generator: random.Random, centers: Sequence[Point]) -> Point:
    if generator.random() >= CLUSTERED_SHARE:
        return _uniform_point(generator)
    center = generator.choice(centers)
    return _inside(
        REGION,
        generator.gauss(center.lat, CLUSTER_SPREAD_DEGREES),
        generator.gauss(center.lon, CLUSTER_SPREAD_DEGREES),
    )


def _near(generator: random.Random, point: Point, region: Rectangle) -> Point:
    """Un punto junto a uno existente: las consultas caen donde hay datos."""
    return _inside(
        region,
        point.lat + generator.uniform(-QUERY_JITTER_DEGREES, QUERY_JITTER_DEGREES),
        point.lon + generator.uniform(-QUERY_JITTER_DEGREES, QUERY_JITTER_DEGREES),
    )


def _inside(region: Rectangle, lat: float, lon: float) -> Point:
    return Point(
        min(max(lat, region.min_lat), region.max_lat),
        min(max(lon, region.min_lon), region.max_lon),
    )


def build_queries(radii_km: Sequence[float], neighbours: Sequence[int]) -> list[Query]:
    return [*map(_radius_query, radii_km), *map(_nearest_query, neighbours)]


def _radius_query(radius_km: float) -> Query:
    metres = radius_km * METRES_PER_KM
    return Query(
        operation=f"radio {radius_km:g} km",
        engine_sql=lambda center: (
            f"SELECT id FROM {TABLE} WHERE distancia(ubicacion, {_literal(center)}) < {metres!r}"
        ),
        # `false` pide la distancia sobre la esfera, el mismo modelo que Haversine; por
        # omisión PostGIS mide sobre el elipsoide y los resultados no serían comparables.
        postgres_sql=(
            f"SELECT id FROM {TABLE} WHERE ST_DWithin("
            "ubicacion, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, false)"
        ),
        postgres_parameters=lambda center: (center.lon, center.lat, metres),
        replay=lambda index, center, stats: _drain(
            index.within_radius(center, metres, HAVERSINE, stats)
        ),
        ordered=False,
    )


def _nearest_query(count: int) -> Query:
    return Query(
        operation=f"k-NN k={count}",
        engine_sql=lambda center: (
            f"SELECT id FROM {TABLE} "
            f"ORDER BY distancia(ubicacion, {_literal(center)}) LIMIT {count}"
        ),
        postgres_sql=(
            f"SELECT id FROM {TABLE} ORDER BY ubicacion <-> "
            "ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography LIMIT %s"
        ),
        postgres_parameters=lambda center: (center.lon, center.lat, count),
        replay=lambda index, center, stats: _drain(
            islice(index.nearest(center, HAVERSINE, stats), count)
        ),
        ordered=True,
    )


def _drain(results: Iterable[object]) -> None:
    deque(results, maxlen=0)


def _literal(point: Point) -> str:
    return f"POINT({point.lat!r}, {point.lon!r})"


def run(
    sizes: list[int],
    queries: int,
    seed: int,
    radii_km: list[float],
    neighbours: list[int],
    postgres_dsn: str | None,
    workspace: Path,
    points_file: Path | None = None,
) -> Report:
    report = Report(
        name=BENCHMARK_NAME,
        parameters={
            "sizes": sizes,
            "queries": queries,
            "seed": seed,
            "radii_km": radii_km,
            "neighbours": neighbours,
            "postgres": postgres_dsn is not None,
            "page_size": EngineConfig().page_size,
            "datos": SYNTHETIC_DATA if points_file is None else points_file.name,
        },
    )
    asked = build_queries(radii_km, neighbours)
    workload_of = (
        build_workload if points_file is None else sampled_workloads(load_points(points_file))
    )
    for size in sizes:
        workload = workload_of(size, queries, seed)
        directory = workspace / f"size-{size}"
        config = EngineConfig(data_directory=directory)
        expected = _measure_engine(report, workload, asked, config)
        _measure_insertion_build(report, workload, config)
        if postgres_dsn is not None:
            _measure_postgres(report, workload, asked, postgres_dsn, expected)
    return report


def _measure_engine(
    report: Report, workload: Workload, asked: list[Query], config: EngineConfig
) -> Answers:
    """Mide las dos técnicas del motor sobre la misma tabla: primero sin índice y luego,
    tras crearlo, con R-Tree. Devuelve las respuestas, que son la referencia de las demás."""
    size = workload.size
    with Engine(config) as engine:
        engine.execute(f"CREATE TABLE {TABLE} (id INT, ubicacion POINT)")
        table = engine.table(TABLE)
        for number, point in workload.points:
            table.insert((number, point))
        scanned = _measure_queries(report, SEQUENTIAL, engine, workload, asked)
        _measure_memory(report, SEQUENTIAL, engine, workload, asked)
        report.add(SEQUENTIAL, INDEX_SPACE, size, 0.0, kib=0.0, nota="no usa índice")

        create = f"CREATE INDEX {INDEX} ON {TABLE} USING RTREE (ubicacion)"
        report.add(
            RTREE, BUILD, size, time_it(lambda: engine.execute(create)), metodo="carga masiva STR"
        )
        searched = _measure_queries(report, RTREE, engine, workload, asked)
        _measure_memory(report, RTREE, engine, workload, asked)
        _require_same_answers(asked, scanned, searched)
        _annotate_node_visits(report, engine, workload, asked)
    index_file = config.data_directory / f"{TABLE}.{INDEX}.idx"
    heap_file = config.data_directory / f"{TABLE}.heap"
    report.add(
        RTREE,
        INDEX_SPACE,
        size,
        0.0,
        kib=round(directory_size([index_file]), 1),
        tabla_kib=round(directory_size([heap_file]), 1),
    )
    return scanned


def _measure_queries(
    report: Report, technique: str, engine: Engine, workload: Workload, asked: list[Query]
) -> Answers:
    """Tiempo medio por consulta de cada tipo, y las filas que devolvió cada una.

    Antes de medir se lanza una consulta de calentamiento, igual que en PostgreSQL.
    """
    answers: Answers = {}
    for query in asked:
        found: list[list[int]] = []
        engine.execute(query.engine_sql(workload.centers[0]))

        def run_all(query: Query = query, found: list[list[int]] = found) -> None:
            for center in workload.centers:
                rows = engine.execute(query.engine_sql(center)).rows
                found.append([int(row[0]) for row in rows])

        elapsed = time_it(run_all)
        answers[query.operation] = found
        report.add(
            technique,
            query.operation,
            workload.size,
            elapsed / len(workload.centers),
            consultas=len(workload.centers),
            filas_promedio=round(sum(map(len, found)) / len(found), 1),
        )
    return answers


def _measure_memory(
    report: Report, technique: str, engine: Engine, workload: Workload, asked: list[Query]
) -> None:
    """Pico de memoria de Python al responder unas pocas consultas de cada tipo.

    Va aparte de los tiempos porque `tracemalloc` frena la ejecución varias veces.
    """
    tracemalloc.start()
    for query in asked:
        for center in workload.centers[:MEMORY_SAMPLE_QUERIES]:
            engine.execute(query.engine_sql(center))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report.add(technique, PEAK_MEMORY, workload.size, 0.0, kib=round(peak / BYTES_PER_KIB, 1))


def _require_same_answers(asked: list[Query], expected: Answers, found: Answers) -> None:
    for query in asked:
        for number, (first, second) in enumerate(
            zip(expected[query.operation], found[query.operation], strict=True)
        ):
            if _comparable(query, first) != _comparable(query, second):
                raise RuntimeError(
                    f"'{query.operation}', consulta {number}: el índice devolvió filas "
                    "distintas de las del recorrido secuencial"
                )


def _comparable(query: Query, ids: list[int]) -> list[int]:
    return ids if query.ordered else sorted(ids)


def _annotate_node_visits(
    report: Report, engine: Engine, workload: Workload, asked: list[Query]
) -> None:
    """Añade a cada medida del R-Tree cuántos nodos abrió de media.

    Las búsquedas se repiten directamente sobre el índice, fuera de la medida de tiempo,
    solo para contar: es el dato que explica por qué el índice tarda lo que tarda.
    """
    index = engine.table(TABLE).spatial_index(INDEX)
    for query in asked:
        stats = SearchStats()
        for center in workload.centers:
            query.replay(index, center, stats)
        measurement = next(
            item
            for item in report.measurements
            if item.technique == RTREE
            and item.operation == query.operation
            and item.dataset_size == workload.size
        )
        measurement.extra["nodos_visitados_promedio"] = round(
            stats.nodes_visited / len(workload.centers), 1
        )
        measurement.extra["nodos_del_arbol"] = index.node_count


def _measure_insertion_build(report: Report, workload: Workload, config: EngineConfig) -> None:
    """Construye el mismo árbol insertando punto a punto, que es lo que cuesta mantenerlo
    cuando las filas llegan después de crear el índice."""
    path = config.data_directory / "insertado.rtree"
    with RTree(path, config) as tree:

        def insert_all() -> None:
            for number, point in workload.points:
                address = RecordId(
                    1 + number // FAKE_SLOTS_PER_PAGE, number % FAKE_SLOTS_PER_PAGE
                )
                tree.insert(point, address)

        elapsed = time_it(insert_all)
        nodes = tree.node_count
    report.add(
        RTREE_INSERTED, BUILD, workload.size, elapsed, metodo="inserción uno a uno", nodos=nodes
    )
    report.add(
        RTREE_INSERTED, INDEX_SPACE, workload.size, 0.0, kib=round(directory_size([path]), 1)
    )


def _measure_postgres(
    report: Report, workload: Workload, asked: list[Query], dsn: str, expected: Answers
) -> None:
    import psycopg

    size = workload.size
    with psycopg.connect(dsn, autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        cursor.execute(f"CREATE TEMP TABLE {TABLE} (id integer, ubicacion geography(Point, 4326))")
        with cursor.copy(f"COPY {TABLE} (id, ubicacion) FROM STDIN") as copy:
            for number, point in workload.points:
                copy.write_row((number, f"SRID=4326;POINT({point.lon!r} {point.lat!r})"))
        create = f"CREATE INDEX {INDEX} ON {TABLE} USING GIST (ubicacion)"
        report.add(GIST, BUILD, size, time_it(lambda: cursor.execute(create)))
        cursor.execute(f"ANALYZE {TABLE}")
        # Con pocas filas PostgreSQL prefiere recorrer la tabla; aquí se mide el índice.
        cursor.execute("SET enable_seqscan = off")
        for query in asked:
            _measure_postgres_query(report, cursor, workload, query, expected[query.operation])
        cursor.execute("SELECT pg_relation_size(%s), pg_relation_size(%s)", (INDEX, TABLE))
        index_bytes, table_bytes = cursor.fetchone()
        report.add(
            GIST,
            INDEX_SPACE,
            size,
            0.0,
            kib=round(index_bytes / BYTES_PER_KIB, 1),
            tabla_kib=round(table_bytes / BYTES_PER_KIB, 1),
        )


def _measure_postgres_query(
    report: Report, cursor: Any, workload: Workload, query: Query, expected: list[list[int]]
) -> None:
    found: list[list[int]] = []
    # La primera consulta de cada tipo paga cargar PostGIS y planificar; igual que en el
    # motor, se lanza una vez antes de medir para que no caiga en la media.
    cursor.execute(query.postgres_sql, query.postgres_parameters(workload.centers[0]))
    cursor.fetchall()

    def run_all() -> None:
        for center in workload.centers:
            cursor.execute(query.postgres_sql, query.postgres_parameters(center))
            found.append([int(row[0]) for row in cursor.fetchall()])

    elapsed = time_it(run_all)
    matching = sum(
        1
        for ours, theirs in zip(expected, found, strict=True)
        if _comparable(query, ours) == _comparable(query, theirs)
    )
    first = workload.centers[0]
    cursor.execute("EXPLAIN " + query.postgres_sql, query.postgres_parameters(first))
    plan = " ".join(str(row[0]) for row in cursor.fetchall())
    report.add(
        GIST,
        query.operation,
        workload.size,
        elapsed / len(workload.centers),
        consultas=len(workload.centers),
        filas_promedio=round(sum(map(len, found)) / len(found), 1),
        coinciden_con_el_motor=f"{matching}/{len(found)}",
        usa_el_indice=INDEX in plan,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--queries", type=int, default=DEFAULT_QUERIES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--radii-km", type=float, nargs="+", default=list(DEFAULT_RADII_KM))
    parser.add_argument("--neighbours", type=int, nargs="+", default=list(DEFAULT_NEIGHBOURS))
    parser.add_argument(
        "--postgres-dsn",
        default=os.environ.get(POSTGRES_DSN_VARIABLE),
        help=f"conexión a PostgreSQL con PostGIS (por omisión, ${POSTGRES_DSN_VARIABLE})",
    )
    parser.add_argument(
        "--points-file",
        type=Path,
        help="CSV con una columna POINT del que tomar puntos reales en vez de generarlos",
    )
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    if arguments.postgres_dsn is None:
        print(f"Sin --postgres-dsn ni ${POSTGRES_DSN_VARIABLE}: no se mide GiST.")
    workspace = Path(tempfile.mkdtemp(prefix="minigestor-bench-"))
    try:
        report = run(
            arguments.sizes,
            arguments.queries,
            arguments.seed,
            arguments.radii_km,
            arguments.neighbours,
            arguments.postgres_dsn,
            workspace,
            arguments.points_file,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    path = report.save(arguments.output)
    print(report.render())
    print(f"\nResultados crudos en {path}")


if __name__ == "__main__":
    main()
