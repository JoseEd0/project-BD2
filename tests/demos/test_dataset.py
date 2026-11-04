"""El dataset de demostración: lo que prometen el guion y los atajos del frontend.

Los atajos espaciales dan por hecho que cada tienda cae dentro del contorno de su distrito
y de ningún otro, y que los CSV versionados son los que produce el generador.

La muestra ESCI de `demos/samples/esci/` no se genera: sale del corpus descargado, que no
está en el repositorio. Sin él se comprueba que la muestra tenga la forma que esperan la
carga y los atajos; con `ESCI_CORPUS_DIR` apuntando al corpus, además, que se regenere
idéntica byte a byte.
"""

import os
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest
from benchmarks.esci.corpus import (
    JUDGMENT_HEADER,
    JUDGMENTS_CSV,
    PRODUCT_HEADER,
    PRODUCTS_CSV,
    QUERIES_CSV,
    QUERY_HEADER,
    read_rows,
)
from benchmarks.esci.subsets import DEFAULT_SEED, load_corpus
from demos.muestra_esci import DEFAULT_PRODUCTS, DEMO_QUERIES, write_sample

from query.loader import POINT_PATTERN
from spatial.geometry import Point

REPOSITORY = Path(__file__).resolve().parents[2]
SAMPLES = REPOSITORY / "demos" / "samples"
ESCI_SAMPLE = SAMPLES / "esci"
ESCI_CORPUS = os.environ.get("ESCI_CORPUS_DIR")
ESCI_FILES = (PRODUCTS_CSV, QUERIES_CSV, JUDGMENTS_CSV)


@pytest.fixture(scope="module")
def stores(dataset: ModuleType) -> list[tuple[str, Point]]:
    generator = dataset.Generator(dataset.DEFAULT_SCALE, dataset.DEFAULT_SEED)
    located = []
    for _, _, _, district, written in generator.rows_of("tiendas"):
        matched = POINT_PATTERN.match(written)
        assert matched is not None, written
        located.append((district, Point(float(matched["lat"]), float(matched["lon"]))))
    return located


def test_every_store_is_inside_its_district_and_no_other(
    dataset: ModuleType, stores: list[tuple[str, Point]]
):
    outlines = {district.name: district.outline for district in dataset.DISTRICTS}
    assert len(stores) == dataset.BASE_TIENDAS
    for district, point in stores:
        containing = [name for name, outline in outlines.items() if outline.contains(point)]
        assert containing == [district], f"{point} figura en {district} y cae en {containing}"


def test_every_district_has_stores(dataset: ModuleType, stores: list[tuple[str, Point]]):
    counted = Counter(district for district, _ in stores)
    assert set(counted) == {district.name for district in dataset.DISTRICTS}
    assert min(counted.values()) > 50


def test_district_outlines_do_not_overlap(dataset: ModuleType):
    """Ningún vértice de un contorno cae dentro de otro. Junto con el test que coloca cada
    tienda en un único distrito, descarta que dos contornos compartan terreno."""
    for first in dataset.DISTRICTS:
        for second in dataset.DISTRICTS:
            if first is second:
                continue
            inside = [vertex for vertex in first.outline.vertices if second.outline.contains(vertex)]
            assert not inside, f"{first.name} entra en {second.name} por {inside}"


@pytest.mark.parametrize("spec_name", ["categorias", "clientes", "productos", "pedidos", "detalle_pedidos", "tiendas"])
def test_versioned_samples_are_what_the_generator_writes(
    dataset: ModuleType, tmp_path: Path, spec_name: str
):
    """Un cambio en el generador que no se regenere en `demos/samples/` rompería el guion."""
    generator = dataset.Generator(dataset.DEFAULT_SCALE, dataset.DEFAULT_SEED)
    for spec in dataset.SPECS:
        path, _ = dataset.write_csv(tmp_path, spec, generator.rows_of(spec.name))
        if spec.name == spec_name:
            assert path.read_bytes() == (SAMPLES / f"{spec.name}.csv").read_bytes()
            return
    pytest.fail(f"el generador no tiene la tabla {spec_name}")


def test_the_esci_tables_load_the_sample_files_in_the_corpus_format(dataset: ModuleType):
    expected = {
        PRODUCTS_CSV: PRODUCT_HEADER,
        QUERIES_CSV: QUERY_HEADER,
        JUDGMENTS_CSV: JUDGMENT_HEADER,
    }
    loaded = {sample.file_name: sample.spec.header for sample in dataset.ESCI_TABLES}
    assert loaded == expected
    assert dataset.ESCI_SAMPLE_DIRECTORY == ESCI_SAMPLE
    for file_name, header in expected.items():
        first = next(read_rows(ESCI_SAMPLE / file_name))
        assert tuple(first) == header


def test_the_esci_sample_has_every_demo_query_with_all_its_judged_products():
    sample = load_corpus(ESCI_SAMPLE)
    assert len(sample.product_ids) == DEFAULT_PRODUCTS
    assert len(set(sample.product_ids)) == DEFAULT_PRODUCTS
    assert sorted(query.text for query in sample.queries) == sorted(DEMO_QUERIES)
    for query in sample.queries:
        assert sample.relevant_products(query.identifier)
        assert sample.judged_products(query.identifier) <= set(sample.product_ids)


@pytest.mark.skipif(ESCI_CORPUS is None, reason="ESCI_CORPUS_DIR no apunta al corpus ESCI")
def test_the_esci_sample_is_what_the_corpus_gives(tmp_path: Path):
    assert ESCI_CORPUS is not None
    write_sample(Path(ESCI_CORPUS), tmp_path, DEFAULT_PRODUCTS, DEFAULT_SEED)
    for file_name in ESCI_FILES:
        assert (tmp_path / file_name).read_bytes() == (ESCI_SAMPLE / file_name).read_bytes()
