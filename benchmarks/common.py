"""Utilidades compartidas por los benchmarks.

Todo lo que se reporta se mide. Nada se estima, nada se escribe a mano: cada script guarda
sus resultados crudos en JSON y los imprime como tabla Markdown para pegarlos en el informe.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
import tracemalloc
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from query.plan import MILLISECONDS_PER_SECOND
from storage.record import RecordSerializer
from storage.record_id import RecordId
from storage.schema import Field, Schema
from storage.types import FieldType, Value

BYTES_PER_KIB = 1024.0
NAME_LENGTH = 24
CITY_LENGTH = 16
RESULTS_DIRECTORY = "results"
POSTGRES_DSN_VARIABLE = "BENCH_POSTGRES_DSN"
ROWS_STREAM = "filas"
QUERIES_STREAM = "consultas"
DELETIONS_STREAM = "borrados"
# Ranuras por página de las direcciones inventadas: cualquier número que quepa en el campo
# de la ranura sirve, porque nadie va a leer esas páginas.
SYNTHETIC_SLOTS_PER_PAGE = 100


@dataclass
class Measurement:
    """Una medida con su etiqueta."""

    technique: str
    operation: str
    dataset_size: int
    milliseconds: float
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    """Conjunto de medidas de un benchmark, con lo necesario para reproducirlo."""

    name: str
    parameters: dict[str, Any]
    measurements: list[Measurement] = field(default_factory=list)

    def add(
        self,
        technique: str,
        operation: str,
        dataset_size: int,
        milliseconds: float,
        **extra: Any,
    ) -> None:
        self.measurements.append(
            Measurement(technique, operation, dataset_size, milliseconds, extra)
        )

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.name}.json"
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def render(self) -> str:
        """Tabla Markdown con una fila por medida."""
        header = "| Técnica | Operación | Tamaño | Tiempo (ms) | Detalle |"
        separator = "|---|---|---:|---:|---|"
        lines = [header, separator]
        for item in self.measurements:
            detail = ", ".join(f"{key}={value}" for key, value in item.extra.items())
            lines.append(
                f"| {item.technique} | {item.operation} | {item.dataset_size} | "
                f"{item.milliseconds:.2f} | {detail} |"
            )
        return "\n".join(lines)


def time_it(action: Callable[[], Any]) -> float:
    """Milisegundos que tarda `action`."""
    started = time.perf_counter()
    action()
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def peak_memory_kib(action: Callable[[], Any]) -> float:
    """Pico de memoria de Python, en KiB, mientras corre `action`.

    Se mide aparte de los tiempos: `tracemalloc` frena la ejecución varias veces.
    """
    tracemalloc.start()
    try:
        action()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return round(peak / BYTES_PER_KIB, 1)


def add_postgres_argument(parser: argparse.ArgumentParser) -> None:
    """`--postgres-dsn`, que por omisión toma la variable `BENCH_POSTGRES_DSN`."""
    parser.add_argument(
        "--postgres-dsn",
        default=os.environ.get(POSTGRES_DSN_VARIABLE),
        help=f"conexión al PostgreSQL de referencia (por omisión, ${POSTGRES_DSN_VARIABLE})",
    )


def connect_postgres(dsn: str) -> Any:
    """Conexión en modo autocommit al PostgreSQL de referencia.

    `psycopg` es un extra opcional (`.[postgres]`): se importa solo si se mide contra él.
    """
    import psycopg

    return psycopg.connect(dsn, autocommit=True)


def relation_kib(cursor: Any, relation: str) -> float:
    """KiB que ocupa en disco una tabla o un índice de PostgreSQL."""
    cursor.execute("SELECT pg_relation_size(%s)", (relation,))
    (size,) = cursor.fetchone()
    return round(int(size) / BYTES_PER_KIB, 1)


def benchmark_schema() -> Schema:
    """Esquema común: una clave entera, dos textos y un número."""
    return Schema(
        [
            Field("id", FieldType.INT, nullable=False),
            Field("nombre", FieldType.STRING, NAME_LENGTH),
            Field("ciudad", FieldType.STRING, CITY_LENGTH),
            Field("nota", FieldType.FLOAT),
        ]
    )


def random_stream(seed: int, purpose: str) -> random.Random:
    """Generador propio de un uso de la semilla: las filas, las consultas, los borrados.

    Dos `Random(seed)` sacan los mismos números, y `shuffle` y `sample` los recorren al
    revés: con un solo generador por semilla, las claves que se consultan o se borran
    serían justo las últimas insertadas, que es un caso particular y no uno al azar.
    """
    return random.Random(f"{seed}:{purpose}")


def build_rows(count: int, seed: int) -> list[tuple[int, str, str, float]]:
    """Filas con claves barajadas, para que ninguna estructura se beneficie del orden."""
    generator = random_stream(seed, ROWS_STREAM)
    cities = ["lima", "cusco", "piura", "tacna", "trujillo"]
    keys = list(range(count))
    generator.shuffle(keys)
    return [
        (key, f"nombre-{key}", generator.choice(cities), generator.random() * 20)
        for key in keys
    ]


def pack_all(serializer: RecordSerializer, rows: Sequence[tuple[Any, ...]]) -> list[bytes]:
    return [serializer.pack(row) for row in rows]


def directory_size(paths: Sequence[Path]) -> float:
    """Kibibytes ocupados por los archivos indicados."""
    return sum(path.stat().st_size for path in paths if path.exists()) / BYTES_PER_KIB


def integer_of(value: Value) -> int:
    """Un valor que el experimento sabe entero: el id de una fila del dataset sintético.

    Raises:
        TypeError: si no lo es, que sería un error del propio experimento.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"se esperaba un entero y llegó {value!r}")
    return value


def sample_keys(count: int, queries: int, seed: int) -> list[int]:
    """Claves al azar para consultar, independientes del orden en que se insertaron."""
    generator = random_stream(seed, QUERIES_STREAM)
    return [generator.randrange(count) for _ in range(queries)]


def deletion_order(count: int, seed: int) -> list[int]:
    """Todas las claves, en un orden al azar independiente del de inserción."""
    return random_stream(seed, DELETIONS_STREAM).sample(range(count), count)


def synthetic_record_id(position: int) -> RecordId:
    """Una dirección de fila válida para la fila número `position`, para medir un índice
    sin la tabla detrás. La página 0 es la cabecera de un archivo paginado: se empieza en 1.
    """
    return RecordId(1 + position // SYNTHETIC_SLOTS_PER_PAGE, position % SYNTHETIC_SLOTS_PER_PAGE)


def results_directory(root: Path) -> Path:
    return root / RESULTS_DIRECTORY
