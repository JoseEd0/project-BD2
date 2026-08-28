"""Comparación experimental: B+ agrupado vs B+ no agrupado vs hash extendible.

Mide lo que pide el enunciado: tiempo de construcción, búsqueda por igualdad, búsqueda por
rango, recorrido ordenado, espacio adicional y coste de inserciones y borrados.

    .venv/bin/python -m benchmarks.index_benchmark --sizes 1000 10000 --queries 100
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from collections.abc import Iterable
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
from index.bplustree import ClusteredBPlusIndex, UnclusteredBPlusIndex
from index.hash import ExtendibleHashIndex
from index.keys import ScalarKeyCodec
from storage.heap import HeapFile
from storage.record import RecordSerializer
from storage.record_id import RECORD_ID_SIZE, RecordId

BENCHMARK_NAME = "indices"
DEFAULT_SIZES = (1000, 10000)
DEFAULT_QUERIES = 100
DEFAULT_SEED = 20260827
RANGE_WIDTH = 50
MUTATION_FRACTION = 10
CLUSTERED = "B+ agrupado"
UNCLUSTERED = "B+ no agrupado"
HASH = "hash extendible"


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
        _measure_clustered(report, config, serializer, records, keys, size)
        addresses = _fill_heap(config, serializer, records)
        _measure_unclustered(report, config, serializer, records, addresses, keys, size)
        _measure_hash(report, config, serializer, records, addresses, keys, size)
    return report


def _fill_heap(
    config: EngineConfig, serializer: RecordSerializer, records: list[bytes]
) -> list[RecordId]:
    path = config.data_directory / "datos.heap"
    with HeapFile(path, serializer.size, config) as heap:
        return [heap.insert(record) for record in records]


def _measure_clustered(
    report: Report,
    config: EngineConfig,
    serializer: RecordSerializer,
    records: list[bytes],
    keys: list[int],
    size: int,
) -> None:
    path = config.data_directory / "clustered.bpt"
    with ClusteredBPlusIndex(path, serializer, "id", config) as index:
        report.add(
            CLUSTERED,
            "construcción",
            size,
            time_it(lambda: [index.insert(record) for record in records]),
        )
        report.add(
            CLUSTERED,
            "igualdad",
            size,
            time_it(lambda: [index.search(key) for key in keys]),
            consultas=len(keys),
        )
        report.add(
            CLUSTERED,
            "rango",
            size,
            time_it(lambda: [list(index.range_search(key, key + RANGE_WIDTH)) for key in keys]),
            ancho=RANGE_WIDTH,
        )
        report.add(CLUSTERED, "recorrido ordenado", size, time_it(lambda: list(index.scan())))
        index.flush()
        report.add(
            CLUSTERED,
            "espacio",
            size,
            0.0,
            kib=round(directory_size([path]), 1),
            altura=index.height,
            nota="incluye las filas: el índice es la tabla",
        )
        victims = [key for key in range(size) if key % MUTATION_FRACTION == 0]
        report.add(
            CLUSTERED,
            "borrado",
            size,
            time_it(lambda: [index.delete(key) for key in victims]),
            filas=len(victims),
        )


def _measure_unclustered(
    report: Report,
    config: EngineConfig,
    serializer: RecordSerializer,
    records: list[bytes],
    addresses: list[RecordId],
    keys: list[int],
    size: int,
) -> None:
    path = config.data_directory / "unclustered.bpt"
    heap_path = config.data_directory / "datos.heap"
    with (
        UnclusteredBPlusIndex(path, serializer, "id", config) as index,
        HeapFile(heap_path, serializer.size, config) as heap,
    ):
        report.add(
            UNCLUSTERED,
            "construcción",
            size,
            time_it(
                lambda: [
                    index.insert(record, address)
                    for record, address in zip(records, addresses, strict=True)
                ]
            ),
        )
        report.add(
            UNCLUSTERED,
            "igualdad",
            size,
            time_it(lambda: [_resolve(heap, index.search(key)) for key in keys]),
            consultas=len(keys),
            nota="incluye el salto al heap",
        )
        report.add(
            UNCLUSTERED,
            "rango",
            size,
            time_it(
                lambda: [
                    _resolve_range(heap, index.range_search(key, key + RANGE_WIDTH))
                    for key in keys
                ]
            ),
            ancho=RANGE_WIDTH,
        )
        report.add(
            UNCLUSTERED,
            "recorrido ordenado",
            size,
            time_it(lambda: [heap.read(address) for _, address in index.scan()]),
        )
        index.flush()
        report.add(
            UNCLUSTERED,
            "espacio",
            size,
            0.0,
            kib=round(directory_size([path]), 1),
            altura=index.height,
            nota="solo el índice; las filas están en el heap",
        )
        victims = [
            (record, address)
            for record, address in zip(records, addresses, strict=True)
            if serializer.unpack_field(record, 0) % MUTATION_FRACTION == 0
        ]
        report.add(
            UNCLUSTERED,
            "borrado",
            size,
            time_it(lambda: [index.delete(record, address) for record, address in victims]),
            filas=len(victims),
        )


def _measure_hash(
    report: Report,
    config: EngineConfig,
    serializer: RecordSerializer,
    records: list[bytes],
    addresses: list[RecordId],
    keys: list[int],
    size: int,
) -> None:
    path = config.data_directory / "hash.idx"
    heap_path = config.data_directory / "datos.heap"
    codec = ScalarKeyCodec(serializer.schema.fields[0])
    with (
        ExtendibleHashIndex(path, codec, RECORD_ID_SIZE, config) as index,
        HeapFile(heap_path, serializer.size, config) as heap,
    ):
        entries = [
            (serializer.unpack_field(record, 0), address)
            for record, address in zip(records, addresses, strict=True)
        ]
        report.add(
            HASH,
            "construcción",
            size,
            time_it(lambda: [index.insert(key, address.pack()) for key, address in entries]),
        )
        report.add(
            HASH,
            "igualdad",
            size,
            time_it(lambda: [_resolve_raw(heap, index.search(key)) for key in keys]),
            consultas=len(keys),
            nota="incluye el salto al heap",
        )
        report.add(HASH, "rango", size, 0.0, nota="no soportado: el hash no guarda orden")
        report.add(
            HASH, "recorrido ordenado", size, 0.0, nota="no soportado: haría falta ordenar"
        )
        index.flush()
        report.add(
            HASH,
            "espacio",
            size,
            0.0,
            kib=round(directory_size([path, path.with_name(path.name + ".dir")]), 1),
            profundidad_global=index.global_depth,
        )
        victims = [key for key, _ in entries if key % MUTATION_FRACTION == 0]
        report.add(
            HASH,
            "borrado",
            size,
            time_it(lambda: [index.delete(key) for key in victims]),
            filas=len(victims),
        )


def _resolve_range(heap: HeapFile, found: Iterable[tuple[object, RecordId]]) -> list[bytes]:
    return [heap.read(address) for _, address in found]


def _resolve(heap: HeapFile, found: list[RecordId]) -> list[bytes]:
    return [heap.read(address) for address in found]


def _resolve_raw(heap: HeapFile, found: list[bytes]) -> list[bytes]:
    return [heap.read(RecordId.unpack(raw)) for raw in found]


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
