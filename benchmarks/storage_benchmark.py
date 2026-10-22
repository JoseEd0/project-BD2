"""Comparación experimental: heap file vs archivo secuencial paginado.

Mide lo que pide el enunciado: tiempo de inserción para varios tamaños, tiempo de búsqueda
por clave primaria, tiempo de borrado, espacio en disco y tiempo de reorganización.

    .venv/bin/python -m benchmarks.storage_benchmark --sizes 1000 10000 --queries 100

Cada búsqueda y cada borrado es una operación independiente, como la de una sentencia con
`WHERE id = k`: el heap, sin índice, recorre el archivo hasta dar con la clave.

La reorganización no se fuerza: se sigue borrando hasta que el borrado que lleva el
desperdicio por encima del umbral configurado la dispara, y se mide ese borrado.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from functools import partial
from pathlib import Path

from benchmarks.common import (
    Report,
    benchmark_schema,
    build_rows,
    deletion_order,
    directory_size,
    pack_all,
    results_directory,
    sample_keys,
    time_it,
)
from config import EngineConfig
from storage.heap import HeapFile
from storage.record import RecordSerializer
from storage.record_id import RecordId
from storage.sequential import SequentialFile

BENCHMARK_NAME = "almacenamiento"
DEFAULT_SIZES = (1000, 10000)
DEFAULT_QUERIES = 100
DEFAULT_SEED = 20260827
HEAP = "heap file"
SEQUENTIAL = "archivo secuencial"
KEY_FIELD = "id"


def run(sizes: list[int], queries: int, seed: int, page_size: int, workspace: Path) -> Report:
    report = Report(
        name=BENCHMARK_NAME,
        parameters={"sizes": sizes, "queries": queries, "seed": seed, "page_size": page_size},
    )
    serializer = RecordSerializer(benchmark_schema())
    for size in sizes:
        records = pack_all(serializer, build_rows(size, seed))
        keys = sample_keys(size, queries, seed)
        deleted_keys = deletion_order(size, seed)
        directory = workspace / f"size-{size}"
        directory.mkdir(parents=True, exist_ok=True)
        config = EngineConfig(page_size=page_size, data_directory=directory)
        _measure_heap(report, config, serializer, records, keys, deleted_keys[:queries])
        _measure_sequential(report, config, serializer, records, keys, deleted_keys, queries)
    return report


def _measure_heap(
    report: Report,
    config: EngineConfig,
    serializer: RecordSerializer,
    records: list[bytes],
    keys: list[int],
    deleted_keys: list[int],
) -> None:
    size = len(records)
    path = config.data_directory / "bench.heap"
    with HeapFile(path, serializer.size, config) as heap:
        report.add(HEAP, "inserción", size, time_it(lambda: _insert_heap(heap, records)))
        report.add(
            HEAP,
            "búsqueda por clave",
            size,
            time_it(lambda: [_find_in_heap(heap, serializer, key) for key in keys]),
            consultas=len(keys),
        )
        heap.flush()
        report.add(HEAP, "espacio en disco", size, 0.0, kib=round(directory_size([path]), 1))
        report.add(
            HEAP,
            "borrado",
            size,
            time_it(lambda: _delete_from_heap(heap, serializer, deleted_keys)),
            borrados=len(deleted_keys),
        )
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


def _find_in_heap(heap: HeapFile, serializer: RecordSerializer, key: int) -> RecordId:
    """Sin índice, buscar una clave en un heap file es recorrerlo hasta dar con ella: la
    mitad del archivo de media, porque la clave primaria no se repite."""
    for record_id, record in heap.scan():
        if serializer.unpack_field(record, 0) == key:
            return record_id
    raise RuntimeError(f"el recorrido del heap no encontró la clave {key}")


def _delete_from_heap(heap: HeapFile, serializer: RecordSerializer, keys: list[int]) -> None:
    for key in keys:
        heap.delete(_find_in_heap(heap, serializer, key))


def _measure_sequential(
    report: Report,
    config: EngineConfig,
    serializer: RecordSerializer,
    records: list[bytes],
    keys: list[int],
    deleted_keys: list[int],
    deletions: int,
) -> None:
    size = len(records)
    path = config.data_directory / "bench.seq"
    with SequentialFile(path, serializer, KEY_FIELD, config) as sequential:
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
            kib=round(directory_size(SequentialFile.files_of(path)), 1),
        )
        _measure_sequential_deletion(report, sequential, deleted_keys[:deletions], size)
        _measure_threshold_reorganization(
            report, sequential, deleted_keys[deletions:], size, config.sequential_waste_ratio
        )


def _insert_sequential(sequential: SequentialFile, records: list[bytes]) -> None:
    for record in records:
        sequential.insert(record)


def _measure_sequential_deletion(
    report: Report, sequential: SequentialFile, deleted_keys: list[int], size: int
) -> None:
    """Los borrados dejan lápidas; si alguno cruza el umbral, su reorganización entra en
    el tiempo, como le pasaría a quien lanzó ese `DELETE`."""
    tombstones_before = sequential.deleted_count
    elapsed = time_it(lambda: _delete_from_sequential(sequential, deleted_keys))
    reorganized = sequential.deleted_count < tombstones_before + len(deleted_keys)
    report.add(
        SEQUENTIAL,
        "borrado",
        size,
        elapsed,
        borrados=len(deleted_keys),
        incluye_reorganizacion=reorganized,
    )


def _delete_from_sequential(sequential: SequentialFile, keys: list[int]) -> None:
    for key in keys:
        if sequential.delete(key) == 0:
            raise RuntimeError(f"el archivo secuencial no encontró la clave {key}")


def _measure_threshold_reorganization(
    report: Report,
    sequential: SequentialFile,
    remaining_keys: list[int],
    size: int,
    threshold: float,
) -> None:
    """Sigue borrando hasta que un borrado dispara la reorganización y mide ese borrado.

    Un borrado que encuentra su clave suma una lápida; solo la reorganización deja el
    contador de lápidas en cero, así que así se sabe cuál la disparó.
    """
    for key in remaining_keys:
        waste_before = sequential.waste_ratio
        elapsed = time_it(partial(_delete_from_sequential, sequential, [key]))
        if sequential.deleted_count == 0:
            report.add(
                SEQUENTIAL,
                "reorganización",
                size,
                elapsed,
                desperdicio_previo=round(waste_before, 3),
                umbral=threshold,
                registros=sequential.record_count,
            )
            return
    raise RuntimeError("se borraron todas las claves sin que el umbral disparara la reorganización")


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
