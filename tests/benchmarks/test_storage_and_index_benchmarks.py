"""Los experimentos de almacenamiento y de índices, en pequeño.

No miden nada: comprueban que los dos experimentos siguen ejecutándose de principio a fin
y que registran cada técnica y cada operación que después citan las tablas y las gráficas.
"""

from pathlib import Path

from benchmarks.common import Report

from benchmarks import index_benchmark, storage_benchmark

SEED = 11
SIZE = 300
QUERIES = 12
PAGE_SIZE = 512


def measured(report: Report) -> set[tuple[str, str]]:
    return {(item.technique, item.operation) for item in report.measurements}


def extra_of(report: Report, technique: str, operation: str) -> dict:
    return next(
        item.extra
        for item in report.measurements
        if item.technique == technique and item.operation == operation
    )


def test_the_storage_experiment_measures_both_files(tmp_path: Path):
    report = storage_benchmark.run([SIZE], QUERIES, SEED, PAGE_SIZE, tmp_path)
    operations = ("inserción", "búsqueda por clave", "espacio en disco", "reorganización")
    assert measured(report) == {
        (technique, operation)
        for technique in (storage_benchmark.HEAP, storage_benchmark.SEQUENTIAL)
        for operation in operations
    }
    assert all(item.dataset_size == SIZE for item in report.measurements)
    heap_space = extra_of(report, storage_benchmark.HEAP, "espacio en disco")["kib"]
    sequential_space = extra_of(report, storage_benchmark.SEQUENTIAL, "espacio en disco")["kib"]
    assert 0 < heap_space < sequential_space
    assert "nota" in extra_of(report, storage_benchmark.HEAP, "reorganización")


def test_the_index_experiment_measures_the_three_structures(tmp_path: Path):
    report = index_benchmark.run([SIZE], QUERIES, SEED, PAGE_SIZE, tmp_path)
    operations = (
        "construcción",
        "igualdad",
        "rango",
        "recorrido ordenado",
        index_benchmark.MUTATION_LABEL,
        "espacio",
    )
    techniques = (index_benchmark.CLUSTERED, index_benchmark.UNCLUSTERED, index_benchmark.HASH)
    assert measured(report) == {
        (technique, operation) for technique in techniques for operation in operations
    }
    for technique in techniques:
        assert extra_of(report, technique, "espacio")["kib"] > 0
    for operation in ("rango", "recorrido ordenado"):
        assert "nota" in extra_of(report, index_benchmark.HASH, operation)
        assert "nota" not in extra_of(report, index_benchmark.CLUSTERED, operation)


def test_a_report_is_saved_and_rendered(tmp_path: Path):
    report = storage_benchmark.run([SIZE], QUERIES, SEED, PAGE_SIZE, tmp_path / "trabajo")
    path = report.save(tmp_path / "resultados")
    assert path.name == f"{storage_benchmark.BENCHMARK_NAME}.json"
    assert '"seed": 11' in path.read_text(encoding="utf-8")
    table = report.render().splitlines()
    assert table[0].startswith("| Técnica | Operación")
    assert len(table) == 2 + len(report.measurements)
