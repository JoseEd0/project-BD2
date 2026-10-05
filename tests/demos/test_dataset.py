"""El dataset de demostración: lo que prometen el guion y los atajos del frontend.

Los atajos espaciales dan por hecho que cada tienda cae dentro del contorno de su distrito
y de ningún otro, y que los CSV versionados son los que produce el generador.
"""

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType

import pytest

from query.loader import POINT_PATTERN
from spatial.geometry import Point

REPOSITORY = Path(__file__).resolve().parents[2]
SAMPLES = REPOSITORY / "demos" / "samples"


@pytest.fixture(scope="module")
def dataset() -> ModuleType:
    path = REPOSITORY / "demos" / "poblar_ecommerce.py"
    spec = importlib.util.spec_from_file_location("poblar_ecommerce", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Las dataclasses buscan su módulo en `sys.modules` para resolver las anotaciones.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
