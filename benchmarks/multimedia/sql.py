"""El gestor de punta a punta: `CREATE INDEX` y `SIMILAR_TO` por SQL.

Es lo que paga quien usa el gestor: `CREATE INDEX` extrae, entrena el vocabulario,
cuantiza y construye el índice; cada `SIMILAR_TO` analiza el SQL, planifica, extrae la
consulta, busca y lee las filas. Comparado con la misma búsqueda sobre el índice y sobre
FAISS directo, dice cuánto cuesta cada capa.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from benchmarks.common import time_it
from benchmarks.multimedia.collection import MediaFile
from config import EngineConfig
from index.vector import VectorMetric
from query.engine import Engine
from storage.types import Value

TABLE = "medios"
PATH_LENGTH = 200
LABEL_LENGTH = 60
FIRST_ID = 1


@dataclass(frozen=True, slots=True)
class SqlIndex:
    """El método del índice en el SQL y el nombre de su esfuerzo en el `WITH`."""

    method: str
    effort_option: str


SQL_HNSW_INDEX = SqlIndex("HNSW", "ef_search")
SQL_IVF_INDEX = SqlIndex("IVF", "probes")


class SqlCollection:
    """Una tabla del gestor con la colección, sobre la que se crean y se borran índices."""

    def __init__(self, files: Sequence[MediaFile], config: EngineConfig) -> None:
        self._engine = Engine(config)
        self._engine.execute(
            f"CREATE TABLE {TABLE} (id INT, ruta VARCHAR({PATH_LENGTH}), "
            f"clase VARCHAR({LABEL_LENGTH}))"
        )
        table = self._engine.table(TABLE)
        for position, item in enumerate(files):
            row: tuple[Value, ...] = (FIRST_ID + position, item.path, item.label)
            table.insert(row)

    def create_index(self, index: SqlIndex, metric: VectorMetric) -> float:
        """Milisegundos de `CREATE INDEX`, con la extracción y el vocabulario incluidos."""
        statement = (
            f"CREATE INDEX idx_{index.method.lower()} ON {TABLE} USING {index.method} (ruta) "
            f"WITH (METRIC = {metric.value})"
        )
        return time_it(lambda: self._engine.execute(statement))

    def drop_index(self, index: SqlIndex) -> None:
        self._engine.execute(f"DROP INDEX idx_{index.method.lower()}")

    def similar(self, index: SqlIndex, query: str, k: int, effort: int | None) -> list[int]:
        """Las posiciones en la colección de las `k` filas que da `SIMILAR_TO`."""
        options = "" if effort is None else f" WITH {index.effort_option} = {effort}"
        statement = (
            f"SELECT id FROM {TABLE} WHERE SIMILAR_TO('{query}', k = {k}) "
            f"USING {index.method}{options}"
        )
        return [_position_of(row[0]) for row in self._engine.execute(statement).rows]

    def close(self) -> None:
        self._engine.close()


def sql_config(config: EngineConfig, data_directory: Path, media: Path) -> EngineConfig:
    """La configuración de la medida con sus archivos en `data_directory` y los medios del
    dataset, en `media`, como directorio de importación."""
    return replace(config, data_directory=data_directory, import_directories=(media,))


def _position_of(value: Value) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"el id de una fila del experimento es un entero, no {value!r}")
    return value - FIRST_ID
