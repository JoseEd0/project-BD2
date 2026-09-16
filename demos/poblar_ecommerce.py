"""Genera y carga un dataset de e-commerce para demostrar el gestor.

Cinco tablas relacionadas, cada una con una **organización física distinta**, para que la
demo enseñe de un vistazo por qué existen las tres:

    categorias ──┐
                 ├──< productos ──┐
                                  ├──< detalle_pedidos >── pedidos >── clientes
                                  │
    heap file · B+ agrupado · archivo secuencial · índices hash y B+ secundarios

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

MILLISECONDS = 1000.0
CSV_DIRECTORY = "csv"
DEFAULT_SEED = 20260828
DEFAULT_SCALE = 1.0

BASE_CLIENTES = 2000
BASE_PRODUCTOS = 800
BASE_PEDIDOS = 6000
BASE_DETALLES = 18000

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

START_DATE = date(2024, 1, 1)
DAYS_RANGE = 800
PRICE_RANGE = (12.0, 4200.0)
STOCK_RANGE = (0, 400)
QUANTITY_RANGE = (1, 5)


@dataclass(frozen=True, slots=True)
class TableSpec:
    """Una tabla del dataset: cómo se crea y de qué CSV se carga."""

    name: str
    ddl: str
    header: tuple[str, ...]
    description: str


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
