"""Genera y carga un dataset de e-commerce para demostrar el gestor.

Cinco tablas relacionadas, cada una con una **organización física distinta**, para que la
demo enseñe de un vistazo por qué existen las tres:

    categorias ──┐
                 ├──< productos ──┐
                                  ├──< detalle_pedidos >── pedidos >── clientes
                                  │
    heap file · B+ agrupado · archivo secuencial · índices hash y B+ secundarios

Y una sexta, `tiendas`, con la ubicación de cada local en Lima y un índice R-Tree, para
las consultas espaciales: por radio, por cercanía y por distrito.

Ejecutar (requiere `pip install -e .`):

    .venv/bin/python demos/poblar_ecommerce.py --data-dir ./data
    .venv/bin/python demos/poblar_ecommerce.py --data-dir ./data --escala 3 --reiniciar

Deja además los CSV —por defecto en `<data-dir>/csv/`, o donde diga `--csv-dir`—, que
sirven para probar la carga de archivos desde la interfaz. Los de `demos/samples/` salen
de este mismo script con la semilla y la escala por defecto.
"""

from __future__ import annotations

import argparse
import csv
import random
import shutil
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from config import EngineConfig
from query.engine import Engine
from query.loader import read_values
from spatial.geometry import Point, Polygon

MILLISECONDS = 1000.0
CSV_DIRECTORY = "csv"
DEFAULT_SEED = 20260828
DEFAULT_SCALE = 1.0

BASE_CLIENTES = 2000
BASE_PRODUCTOS = 800
BASE_PEDIDOS = 6000
BASE_DETALLES = 18000
BASE_TIENDAS = 3000

CIUDADES = [
    "Lima", "Arequipa", "Trujillo", "Cusco", "Piura",
    "Chiclayo", "Iquitos", "Huancayo", "Tacna", "Puno",
]
NOMBRES = [
    "Ana", "Luis", "Sara", "Marco", "Elena", "Diego", "Rosa", "Julio", "Carmen", "Pablo",
    "Lucía", "Andrés", "Valeria", "Jorge", "Paola", "Renzo", "Camila", "Iván", "Nadia", "Óscar",
]
APELLIDOS = [
    "Quispe", "Huamán", "Flores", "Rojas", "Vargas", "Mendoza", "Castillo", "Ramos",
    "Chávez", "Salazar", "Paredes", "Ríos", "Aguilar", "Espinoza", "Cárdenas", "Ponce",
]
CATEGORIAS = [
    "Laptops", "Celulares", "Audio", "Monitores", "Teclados", "Almacenamiento",
    "Redes", "Impresión", "Accesorios", "Componentes", "Cámaras", "Gaming",
]
MARCAS = ["Nexo", "Volta", "Kairo", "Sierra", "Andes", "Lumen", "Tacna", "Orion"]
MODELOS = ["Pro", "Lite", "Max", "Air", "Plus", "One", "Ultra", "Core"]
ESTADOS = ["pendiente", "pagado", "enviado", "entregado", "cancelado"]

RUBROS = [
    "supermercado", "farmacia", "gasolinera", "restaurante",
    "ferretería", "librería", "electrónica", "panadería",
]
COORDINATE_DECIMALS = 6


@dataclass(frozen=True, slots=True)
class District:
    """Un distrito de Lima: su contorno simplificado y su peso en el reparto de tiendas."""

    name: str
    weight: int
    outline: Polygon


def _outline(*vertices: tuple[float, float]) -> Polygon:
    return Polygon(tuple(Point(lat, lon) for lat, lon in vertices))


# Contornos a mano alzada, no límites oficiales: polígonos de cuatro a seis vértices que no
# se solapan y caen en tierra firme. Bastan para que «tiendas dentro de un distrito» tenga
# una respuesta comprobable, que es para lo que sirve la demostración.
DISTRICTS: tuple[District, ...] = (
    District("Lima", 5, _outline(
        (-12.036, -77.085), (-12.030, -77.045), (-12.040, -77.015),
        (-12.065, -77.020), (-12.065, -77.085),
    )),
    District("Jesús María", 2, _outline(
        (-12.067, -77.060), (-12.067, -77.040), (-12.090, -77.042), (-12.088, -77.062),
    )),
    District("Lince", 2, _outline(
        (-12.078, -77.038), (-12.078, -77.022), (-12.093, -77.024), (-12.093, -77.038),
    )),
    District("San Isidro", 3, _outline(
        (-12.095, -77.056), (-12.095, -77.018), (-12.108, -77.016),
        (-12.110, -77.050), (-12.104, -77.058),
    )),
    District("Miraflores", 4, _outline(
        (-12.112, -77.046), (-12.112, -77.010), (-12.136, -77.008),
        (-12.136, -77.026), (-12.126, -77.036),
    )),
    District("Barranco", 1, _outline(
        (-12.138, -77.022), (-12.138, -77.008), (-12.158, -77.008),
        (-12.158, -77.018), (-12.150, -77.022),
    )),
    District("San Borja", 3, _outline(
        (-12.088, -77.004), (-12.086, -76.982), (-12.112, -76.980), (-12.114, -77.006),
    )),
    District("Surco", 4, _outline(
        (-12.118, -77.004), (-12.118, -76.970), (-12.150, -76.965),
        (-12.165, -76.985), (-12.160, -77.004),
    )),
    District("La Molina", 3, _outline(
        (-12.068, -76.968), (-12.065, -76.925), (-12.090, -76.915),
        (-12.105, -76.940), (-12.100, -76.970),
    )),
    District("San Miguel", 2, _outline(
        (-12.068, -77.106), (-12.068, -77.080), (-12.082, -77.078), (-12.082, -77.100),
    )),
    District("Pueblo Libre", 2, _outline(
        (-12.068, -77.076), (-12.068, -77.064), (-12.085, -77.064), (-12.085, -77.076),
    )),
    District("Los Olivos", 3, _outline(
        (-11.957, -77.083), (-11.955, -77.060), (-12.003, -77.058), (-12.005, -77.085),
    )),
)

START_DATE = date(2024, 1, 1)
DAYS_RANGE = 800
PRICE_RANGE = (12.0, 4200.0)
STOCK_RANGE = (0, 400)
QUANTITY_RANGE = (1, 5)


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Una tabla del dataset: cómo se crea y de qué CSV se carga.

    `after_load` son las sentencias que se ejecutan con las filas ya dentro. Un índice
    creado ahí se construye de una vez sobre todos los datos, que es más rápido y deja un
    árbol mejor que irlo llenando fila a fila.
    """

    name: str
    ddl: str
    header: tuple[str, ...]
    description: str
    after_load: tuple[str, ...] = ()


SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        name="categorias",
        ddl=(
            "CREATE TABLE categorias ("
            " id INT PRIMARY KEY,"
            " nombre VARCHAR(24) )"
        ),
        header=("id", "nombre"),
        description="heap file: tabla pequeña de catálogo",
    ),
    TableSpec(
        name="clientes",
        ddl=(
            "CREATE TABLE clientes ("
            " id INT PRIMARY KEY,"
            " nombre VARCHAR(40),"
            " email VARCHAR(48),"
            " ciudad VARCHAR(16) INDEX HASH,"
            " fecha_alta DATE )"
        ),
        header=("id", "nombre", "email", "ciudad", "fecha_alta"),
        description="heap file + índice hash en 'ciudad' (igualdad en O(1))",
    ),
    TableSpec(
        name="productos",
        ddl=(
            "CREATE TABLE productos ("
            " id INT PRIMARY KEY INDEX BTREE,"
            " nombre VARCHAR(40),"
            " categoria_id INT,"
            " precio FLOAT,"
            " stock INT )"
        ),
        header=("id", "nombre", "categoria_id", "precio", "stock"),
        description="B+ agrupado: las filas viven en las hojas, ordenadas por id",
    ),
    TableSpec(
        name="pedidos",
        ddl=(
            "CREATE TABLE pedidos ("
            " id INT PRIMARY KEY INDEX SEQ,"
            " cliente_id INT,"
            " fecha DATE,"
            " estado VARCHAR(12),"
            " total FLOAT )"
        ),
        header=("id", "cliente_id", "fecha", "estado", "total"),
        description="archivo secuencial: ordenado por id, rangos sin índice extra",
    ),
    TableSpec(
        name="detalle_pedidos",
        ddl=(
            "CREATE TABLE detalle_pedidos ("
            " id INT PRIMARY KEY,"
            " pedido_id INT INDEX BTREE,"
            " producto_id INT,"
            " cantidad INT,"
            " precio_unitario FLOAT )"
        ),
        header=("id", "pedido_id", "producto_id", "cantidad", "precio_unitario"),
        description="heap file + índice B+ secundario en 'pedido_id'",
    ),
    TableSpec(
        name="tiendas",
        ddl=(
            "CREATE TABLE tiendas ("
            " id INT PRIMARY KEY,"
            " nombre VARCHAR(40),"
            " rubro VARCHAR(16),"
            " distrito VARCHAR(16),"
            " ubicacion POINT )"
        ),
        header=("id", "nombre", "rubro", "distrito", "ubicacion"),
        description="heap file + índice R-Tree en 'ubicacion' (radio, k-NN, polígono)",
        after_load=("CREATE INDEX idx_tiendas_ubicacion ON tiendas USING RTREE (ubicacion)",),
    ),
)


class Generator:
    """Produce filas verosímiles y coherentes entre tablas."""

    def __init__(self, scale: float, seed: int) -> None:
        self._random = random.Random(seed)
        self.categorias = len(CATEGORIAS)
        self.clientes = max(1, int(BASE_CLIENTES * scale))
        self.productos = max(1, int(BASE_PRODUCTOS * scale))
        self.pedidos = max(1, int(BASE_PEDIDOS * scale))
        self.detalles = max(1, int(BASE_DETALLES * scale))
        self.tiendas = max(1, int(BASE_TIENDAS * scale))

    def rows_of(self, table: str) -> Iterator[Sequence[object]]:
        return getattr(self, f"_{table}")()

    def _categorias(self) -> Iterator[Sequence[object]]:
        yield from ((number, nombre) for number, nombre in enumerate(CATEGORIAS, start=1))

    def _clientes(self) -> Iterator[Sequence[object]]:
        for number in range(1, self.clientes + 1):
            nombre = f"{self._pick(NOMBRES)} {self._pick(APELLIDOS)}"
            usuario = nombre.lower().replace(" ", ".")
            yield (
                number,
                nombre,
                f"{usuario}{number}@correo.pe",
                self._pick(CIUDADES),
                self._date(),
            )

    def _productos(self) -> Iterator[Sequence[object]]:
        for number in range(1, self.productos + 1):
            categoria = self._random.randint(1, self.categorias)
            nombre = f"{self._pick(MARCAS)} {CATEGORIAS[categoria - 1][:-1]} {self._pick(MODELOS)}"
            yield (
                number,
                nombre[:40],
                categoria,
                round(self._random.uniform(*PRICE_RANGE), 2),
                self._random.randint(*STOCK_RANGE),
            )

    def _pedidos(self) -> Iterator[Sequence[object]]:
        for number in range(1, self.pedidos + 1):
            yield (
                number,
                self._random.randint(1, self.clientes),
                self._date(),
                self._pick(ESTADOS),
                round(self._random.uniform(*PRICE_RANGE), 2),
            )

    def _detalle_pedidos(self) -> Iterator[Sequence[object]]:
        for number in range(1, self.detalles + 1):
            yield (
                number,
                self._random.randint(1, self.pedidos),
                self._random.randint(1, self.productos),
                self._random.randint(*QUANTITY_RANGE),
                round(self._random.uniform(*PRICE_RANGE), 2),
            )

    def _tiendas(self) -> Iterator[Sequence[object]]:
        weights = [district.weight for district in DISTRICTS]
        for number in range(1, self.tiendas + 1):
            district = self._random.choices(DISTRICTS, weights)[0]
            rubro = self._pick(RUBROS)
            point = self._point_inside(district.outline)
            yield (
                number,
                f"{rubro.capitalize()} {self._pick(APELLIDOS)} {number}",
                rubro,
                district.name,
                f"POINT({point.lat:.{COORDINATE_DECIMALS}f}, {point.lon:.{COORDINATE_DECIMALS}f})",
            )

    def _point_inside(self, outline: Polygon) -> Point:
        """Punto uniforme dentro del contorno: se sortea en su rectángulo hasta acertar."""
        box = outline.bounding_box
        while True:
            point = Point(
                round(self._random.uniform(box.min_lat, box.max_lat), COORDINATE_DECIMALS),
                round(self._random.uniform(box.min_lon, box.max_lon), COORDINATE_DECIMALS),
            )
            if outline.contains(point):
                return point

    def _pick(self, options: Sequence[str]) -> str:
        return self._random.choice(options)

    def _date(self) -> date:
        return START_DATE + timedelta(days=self._random.randrange(DAYS_RANGE))


def write_csv(
    directory: Path, spec: TableSpec, rows: Iterator[Sequence[object]]
) -> tuple[Path, int]:
    """Vuelca la tabla a CSV y devuelve su ruta y cuántas filas escribió."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{spec.name}.csv"
    written = 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(spec.header)
        for row in rows:
            writer.writerow(row)
            written += 1
    return path, written


def load(engine: Engine, spec: TableSpec, path: Path) -> tuple[int, float]:
    """Crea la tabla y carga el CSV. Devuelve filas insertadas y milisegundos."""
    engine.execute(spec.ddl)
    table = engine.table(spec.name)
    started = time.perf_counter()
    inserted = 0
    for values in read_values(path, table.schema, engine.config):
        table.insert(values)
        inserted += 1
    for statement in spec.after_load:
        engine.execute(statement)
    return inserted, (time.perf_counter() - started) * MILLISECONDS


def report(rows: list[tuple[TableSpec, int, float]]) -> None:
    print(f"\n{'Tabla':<18}{'Filas':>9}{'Carga (ms)':>13}   Organización")
    print("-" * 92)
    for spec, count, elapsed in rows:
        print(f"{spec.name:<18}{count:>9,}{elapsed:>13.0f}   {spec.description}")
    total_rows = sum(count for _, count, _ in rows)
    total_time = sum(elapsed for _, _, elapsed in rows)
    print("-" * 92)
    print(f"{'TOTAL':<18}{total_rows:>9,}{total_time:>13.0f}")


def suggest_queries() -> None:
    print("\nConsultas para la demo (mira el panel de plan de ejecución en cada una):\n")
    for description, query in (
        ("índice hash, O(1)", "SELECT * FROM clientes WHERE ciudad = 'Cusco' LIMIT 20;"),
        ("recorrido completo: 'email' no tiene índice",
         "SELECT * FROM clientes WHERE email = 'ana.quispe1@correo.pe';"),
        ("B+ agrupado: rango sin saltos al heap",
         "SELECT * FROM productos WHERE id BETWEEN 100 AND 140;"),
        ("archivo secuencial: aprovecha su propio orden",
         "SELECT * FROM pedidos WHERE id BETWEEN 500 AND 560;"),
        ("índice B+ secundario + salto al heap",
         "SELECT * FROM detalle_pedidos WHERE pedido_id = 4210;"),
        ("hashing externo",
         "SELECT estado, COUNT(*) AS total FROM pedidos GROUP BY estado ORDER BY total DESC;"),
        ("grace hash join de tres tablas",
         "SELECT c.nombre, p.estado, p.total FROM pedidos AS p "
         "JOIN clientes AS c ON p.cliente_id = c.id WHERE c.ciudad = 'Lima' "
         "ORDER BY p.total DESC LIMIT 15;"),
        ("R-Tree: tiendas a menos de 5 km de la Plaza de Armas",
         "SELECT nombre, distrito, ubicacion FROM tiendas "
         "WHERE distancia(ubicacion, POINT(-12.0464, -77.0428)) < 5000;"),
        ("R-Tree: las 10 tiendas más cercanas (k-NN)",
         "SELECT nombre, rubro, ubicacion FROM tiendas "
         "ORDER BY distancia(ubicacion, POINT(-12.0464, -77.0428)) LIMIT 10;"),
    ):
        print(f"  -- {description}\n  {query}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--escala", type=float, default=DEFAULT_SCALE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--csv-dir",
        type=Path,
        default=None,
        help=f"dónde dejar los CSV (por defecto <data-dir>/{CSV_DIRECTORY})",
    )
    parser.add_argument(
        "--reiniciar",
        action="store_true",
        help="borra el contenido del directorio de datos antes de cargar",
    )
    arguments = parser.parse_args()

    if arguments.reiniciar and arguments.data_dir.exists():
        shutil.rmtree(arguments.data_dir)
    config = EngineConfig(data_directory=arguments.data_dir)
    generator = Generator(arguments.escala, arguments.seed)
    csv_directory = arguments.csv_dir or arguments.data_dir / CSV_DIRECTORY

    print(f"Generando CSV en {csv_directory}")
    measured: list[tuple[TableSpec, int, float]] = []
    with Engine(config) as engine:
        for spec in SPECS:
            path, written = write_csv(csv_directory, spec, generator.rows_of(spec.name))
            inserted, elapsed = load(engine, spec, path)
            print(f"  {spec.name:<18} {written:>8,} filas → {inserted:,} insertadas")
            measured.append((spec, inserted, elapsed))
    report(measured)
    suggest_queries()


if __name__ == "__main__":
    main()
