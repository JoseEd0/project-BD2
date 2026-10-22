"""Comparación experimental: B+ agrupado vs B+ no agrupado vs hash extendible.

Mide lo que pide el enunciado: tiempo de construcción, búsqueda por igualdad, búsqueda por
rango, recorrido ordenado, espacio adicional y coste de inserciones y borrados.

Las tres técnicas hacen el mismo trabajo: guardar las filas y poder encontrarlas. En el B+
agrupado las filas viven en las hojas; los otros dos guardan direcciones, así que cada uno
tiene su propio heap, y llenarlo o cambiarlo cuenta en su construcción y en sus borrados e
inserciones.

    .venv/bin/python -m benchmarks.index_benchmark --sizes 1000 10000 --queries 100
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

from benchmarks.common import (
    Report,
    benchmark_schema,
    build_rows,
    directory_size,
    integer_of,
    pack_all,
    results_directory,
    sample_keys,
    time_it,
)
from config import EngineConfig
from index.bplustree import ClusteredBPlusIndex, UnclusteredBPlusIndex
from index.hash import ExtendibleHashIndex
from index.hash.extendible_hash import DIRECTORY_SUFFIX
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
MUTATION_LABEL = "inserciones/eliminaciones"
CLUSTERED = "B+ agrupado"
UNCLUSTERED = "B+ no agrupado"
HASH = "hash extendible"
WITH_HEAP_NOTE = "incluye el heap"
INDEX_ONLY_DIGITS = 2


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
        _measure_unclustered(report, config, serializer, records, keys, size)
        _measure_hash(report, config, serializer, records, keys, size)
    return report


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
            time_it(lambda: _insert_records(index, records)),
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
        churn = [
            (key, record)
            for key, record in zip(
                [integer_of(serializer.unpack_field(item, 0)) for item in records],
                records,
                strict=True,
            )
            if key % MUTATION_FRACTION == 0
        ]
        report.add(
            CLUSTERED,
            MUTATION_LABEL,
            size,
            time_it(lambda: _churn_clustered(index, churn)),
            operaciones=len(churn) * 2,
        )


def _measure_unclustered(
    report: Report,
    config: EngineConfig,
    serializer: RecordSerializer,
    records: list[bytes],
    keys: list[int],
    size: int,
) -> None:
    path = config.data_directory / "unclustered.bpt"
    heap_path = config.data_directory / "unclustered.heap"
    with (
        UnclusteredBPlusIndex(path, serializer, "id", config) as index,
        HeapFile(heap_path, serializer.size, config) as heap,
    ):
        addresses: list[RecordId] = []
        report.add(
            UNCLUSTERED,
            "construcción",
            size,
            time_it(lambda: addresses.extend(_load_addressed(heap, index, records))),
            nota=WITH_HEAP_NOTE,
        )
        report.add(
            UNCLUSTERED,
            "igualdad",
            size,
            time_it(lambda: [_resolve(heap, index.search(key)) for key in keys]),
            consultas=len(keys),
            nota="incluye el salto al heap",
            solo_indice_ms=_index_only(lambda: [index.search(key) for key in keys]),
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
            solo_indice_ms=_index_only(lambda: list(index.scan())),
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
        churn = [
            (record, address)
            for record, address in zip(records, addresses, strict=True)
            if integer_of(serializer.unpack_field(record, 0)) % MUTATION_FRACTION == 0
        ]
        report.add(
            UNCLUSTERED,
            MUTATION_LABEL,
            size,
            time_it(lambda: _churn_unclustered(heap, index, churn)),
            operaciones=len(churn) * 2,
            nota=WITH_HEAP_NOTE,
        )


def _measure_hash(
    report: Report,
    config: EngineConfig,
    serializer: RecordSerializer,
    records: list[bytes],
    keys: list[int],
    size: int,
) -> None:
    path = config.data_directory / "hash.idx"
    heap_path = config.data_directory / "hash.heap"
    codec = ScalarKeyCodec(serializer.schema.fields[0])
    with (
        ExtendibleHashIndex(path, codec, RECORD_ID_SIZE, config) as index,
        HeapFile(heap_path, serializer.size, config) as heap,
    ):
        keyed = [(integer_of(serializer.unpack_field(record, 0)), record) for record in records]
        entries: list[tuple[int, RecordId]] = []
        report.add(
            HASH,
            "construcción",
            size,
            time_it(lambda: entries.extend(_load_keyed(heap, index, keyed))),
            nota=WITH_HEAP_NOTE,
        )
        report.add(
            HASH,
            "igualdad",
            size,
            time_it(lambda: [_resolve_raw(heap, index.search(key)) for key in keys]),
            consultas=len(keys),
            nota="incluye el salto al heap",
            solo_indice_ms=_index_only(lambda: [index.search(key) for key in keys]),
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
            kib=round(directory_size([path, path.with_name(path.name + DIRECTORY_SUFFIX)]), 1),
            profundidad_global=index.global_depth,
        )
        churn = [
            (key, record, address)
            for (key, record), (_, address) in zip(keyed, entries, strict=True)
            if key % MUTATION_FRACTION == 0
        ]
        report.add(
            HASH,
            MUTATION_LABEL,
            size,
            time_it(lambda: _churn_hash(heap, index, churn)),
            operaciones=len(churn) * 2,
            nota=WITH_HEAP_NOTE,
        )


def _insert_records(index: ClusteredBPlusIndex, records: list[bytes]) -> None:
    for record in records:
        index.insert(record)


def _index_only(lookup: Callable[[], object]) -> float:
    """La misma lectura sin ir al heap: separa lo que cuesta el índice de lo que cuesta el
    salto a la fila."""
    return round(time_it(lookup), INDEX_ONLY_DIGITS)


def _load_addressed(
    heap: HeapFile, index: UnclusteredBPlusIndex, records: list[bytes]
) -> list[RecordId]:
    """Como carga el motor una tabla con índice secundario: cada fila al heap y su
    dirección al índice."""
    addresses = []
    for record in records:
        address = heap.insert(record)
        index.insert(record, address)
        addresses.append(address)
    return addresses


def _load_keyed(
    heap: HeapFile, index: ExtendibleHashIndex, keyed: list[tuple[int, bytes]]
) -> list[tuple[int, RecordId]]:
    entries = []
    for key, record in keyed:
        address = heap.insert(record)
        index.insert(key, address.pack())
        entries.append((key, address))
    return entries


def _churn_clustered(index: ClusteredBPlusIndex, entries: list[tuple[int, bytes]]) -> None:
    """Borra y reinserta: mide el coste de un índice sometido a cambios frecuentes."""
    for key, _ in entries:
        index.delete(key)
    for _, record in entries:
        index.insert(record)


def _churn_unclustered(
    heap: HeapFile, index: UnclusteredBPlusIndex, entries: list[tuple[bytes, RecordId]]
) -> None:
    for record, address in entries:
        index.delete(record, address)
        heap.delete(address)
    for record, _ in entries:
        index.insert(record, heap.insert(record))


def _churn_hash(
    heap: HeapFile, index: ExtendibleHashIndex, entries: list[tuple[int, bytes, RecordId]]
) -> None:
    for key, _, address in entries:
        index.delete(key)
        heap.delete(address)
    for key, record, _ in entries:
        index.insert(key, heap.insert(record).pack())


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
