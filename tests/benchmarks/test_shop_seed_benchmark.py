"""El espacio de la carga de la tienda: cada archivo del gestor cuenta para su tabla, como
datos o como el índice que nombra."""

from pathlib import Path

from benchmarks.common import Report
from benchmarks.shop_seed_benchmark import DATA_FIELD, INDEX_FIELD, SPACE, TOTAL, record_space

MIB = 1024 * 1024
FILES = {
    "productos.heap": 2 * MIB,
    "productos.text": MIB,
    "productos.pk_productos.idx": MIB,
    "productos.productos_texto.idx.seg0.postings": 3 * MIB,
    "productos.productos_texto.idx.manifest": 0,
    "compras.seq": MIB,
    "compras.seq.overflow": 0,
    "catalog.json": 0,
    ".motor.lock": 0,
}


def test_each_file_counts_for_its_table_as_data_or_index(tmp_path: Path):
    for name, size in FILES.items():
        (tmp_path / name).write_bytes(b"\0" * size)
    report = Report("prueba", {})
    record_space(report, tmp_path)
    space = {item.technique: item.extra for item in report.measurements if item.operation == SPACE}
    assert (space["productos"][DATA_FIELD], space["productos"][INDEX_FIELD]) == (3.0, 4.0)
    assert space["productos"]["indices_mib"] == {"pk_productos": 1.0, "productos_texto": 3.0}
    assert (space["compras"][DATA_FIELD], space["compras"][INDEX_FIELD]) == (1.0, 0.0)
    assert space[TOTAL] == {"mib": 8.0}
