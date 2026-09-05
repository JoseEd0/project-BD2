"""Comparación experimental: heap file vs archivo secuencial paginado.

Mide lo que pide el enunciado: tiempo de inserción para varios tamaños, tiempo de búsqueda
por clave primaria, espacio en disco y tiempo de reorganización.

    .venv/bin/python benchmarks/storage_benchmark.py --sizes 1000 10000 --queries 100
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from benchmarks.common import (
    Report,
    benchmark_schema,
    build_rows,
    directory_size,
    pack_all,
    results_directory,
    sample_keys,
    time_it,
)
from config import EngineConfig
from storage.heap import HeapFile
from storage.record import RecordSerializer
from storage.sequential import SequentialFile

BENCHMARK_NAME = "almacenamiento"
DEFAULT_SIZES = (1000, 10000)
DEFAULT_QUERIES = 100
DEFAULT_SEED = 20260827
HEAP = "heap file"
SEQUENTIAL = "archivo secuencial"


def run(sizes: list[int], queries: int, seed: int, page_size: int, workspace: Path) -> Report:
    report = Report(
        name=BENCHMARK_NAME,
        parameters={"sizes": sizes, "queries": queries, "seed": seed, "page_size": page_size},
    )
    serializer = RecordSerializer(benchmark_schema())
    for size in sizes:
        rows = build_rows(size, seed)
        records = pack_all(serializer, rows)
        keys = sample_keys(size, queries, seed)
        directory = workspace / f"size-{size}"
        directory.mkdir(parents=True, exist_ok=True)
        config = EngineConfig(page_size=page_size, data_directory=directory)
        _measure_heap(report, config, serializer, records, keys, size)
        _measure_sequential(report, config, serializer, records, keys, size)
    return report


def _measure_heap(
    report: Report,
    config: EngineConfig,
    serializer: RecordSerializer,
    records: list[bytes],
    keys: list[int],
    size: int,
) -> None:
    path = config.data_directory / "bench.heap"
    with HeapFile(path, serializer.size, config) as heap:
        report.add(HEAP, "inserción", size, time_it(lambda: _insert_heap(heap, records)))
        report.add(
            HEAP,
            "búsqueda por clave",
            size,
            time_it(lambda: _search_heap(heap, serializer, keys)),
            consultas=len(keys),
        )
        heap.flush()
        report.add(HEAP, "espacio en disco", size, 0.0, kib=round(directory_size([path]), 1))
        report.add(
            HEAP,
            "reorganización",
            size,
            0.0,
            nota="no aplica: el heap reutiliza las ranuras libres al vuelo",
        )


def _insert_heap(heap: HeapFile, records: list[bytes]) -> None:
    for record in records:
        heap.insert(record)


def _search_heap(heap: HeapFile, serializer: RecordSerializer, keys: list[int]) -> None:
    """Sin índice, buscar por clave en un heap file es recorrerlo entero."""
    wanted = set(keys)
    found = 0
    for _, record in heap.scan():
        if serializer.unpack_field(record, 0) in wanted:
            found += 1
    if found == 0:
        raise RuntimeError("el recorrido no encontró ninguna de las claves buscadas")


def _measure_sequential(
    report: Report,
    config: EngineConfig,
    serializer: RecordSerializer,
    records: list[bytes],
    keys: list[int],
    size: int,
) -> None:
    path = config.data_directory / "bench.seq"
    overflow = path.with_name(path.name + ".overflow")
    with SequentialFile(path, serializer, "id", config) as sequential:
        report.add(
            SEQUENTIAL, "inserción", size, time_it(lambda: _insert_sequential(sequential, records))
        )
        report.add(
            SEQUENTIAL,
            "búsqueda por clave",
            size,
            time_it(lambda: [sequential.search(key) for key in keys]),
            consultas=len(keys),
        )
        sequential.flush()
        report.add(
            SEQUENTIAL,
            "espacio en disco",
            size,
            0.0,
            kib=round(directory_size([path, overflow]), 1),
        )
        wasted = round(sequential.waste_ratio, 3)
        report.add(
            SEQUENTIAL,
            "reorganización",
            size,
            time_it(sequential.reorganize),
            desperdicio_previo=wasted,
        )


def _insert_sequential(sequential: SequentialFile, records: list[bytes]) -> None:
    for record in records:
        sequential.insert(record)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(DEFAULT_SIZES))
    parser.add_argument("--queries", type=int, default=DEFAULT_QUERIES)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--page-size", type=int, default=EngineConfig().page_size)
    parser.add_argument("--output", type=Path, default=results_directory(Path(__file__).parent))
    arguments = parser.parse_args()
    workspace = Path(tempfile.mkdtemp(prefix="minigestor-bench-"))
    try:
        report = run(
            arguments.sizes, arguments.queries, arguments.seed, arguments.page_size, workspace
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    print(report.render())
    print(f"\nResultados crudos en {report.save(arguments.output)}")


if __name__ == "__main__":
    main()
