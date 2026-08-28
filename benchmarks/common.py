"""Utilidades compartidas por los benchmarks.

Todo lo que se reporta se mide. Nada se estima, nada se escribe a mano: cada script guarda
sus resultados crudos en JSON y los imprime como tabla Markdown para pegarlos en el informe.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from storage.record import RecordSerializer
from storage.schema import Field, Schema
from storage.types import FieldType

MILLISECONDS = 1000.0
BYTES_PER_KIB = 1024.0
NAME_LENGTH = 24
CITY_LENGTH = 16
RESULTS_DIRECTORY = "results"


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
    return (time.perf_counter() - started) * MILLISECONDS


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


def build_rows(count: int, seed: int) -> list[tuple[int, str, str, float]]:
    """Filas con claves barajadas, para que ninguna estructura se beneficie del orden."""
    generator = random.Random(seed)
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


def sample_keys(count: int, queries: int, seed: int) -> list[int]:
    generator = random.Random(seed)
    return [generator.randrange(count) for _ in range(queries)]


def results_directory(root: Path) -> Path:
    return root / RESULTS_DIRECTORY
