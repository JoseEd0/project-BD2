"""Los TEXT de las filas que un operador vuelca a disco.

El ordenamiento, el hashing y la reunión externos trabajan con registros de tamaño fijo, y
un TEXT no tiene longitud máxima. Mientras el operador se ejecuta, sus TEXT van a un archivo
de valores temporal en el directorio propio del operador, y el registro volcado guarda la
dirección: la misma forma que en una tabla, con el mismo `OutOfRowSerializer`.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType

from config import EngineConfig
from external.runs import remove_if_empty
from storage.out_of_row import OutOfRowSerializer
from storage.record import RecordSerializer
from storage.record_id import RecordId
from storage.schema import Schema
from storage.varlen import VariableLengthFile

SPILLED_VALUES_FILENAME = "values.text"


class SpilledValues:
    """Archivo de valores temporal de un operador.

    Se crea con el primer valor, así que un operador cuyas filas no tienen TEXT no crea
    nada, y se borra al cerrarlo junto con el directorio, si quedó vacío.
    """

    def __init__(self, directory: Path, config: EngineConfig) -> None:
        self._directory = directory
        self._config = config
        self._file: VariableLengthFile | None = None

    def put(self, value: bytes) -> RecordId:
        return self._opened().put(value)

    def get(self, address: RecordId) -> bytes:
        return self._opened().get(address)

    def replace(self, address: RecordId, value: bytes) -> RecordId:
        return self._opened().replace(address, value)

    def delete(self, address: RecordId) -> None:
        self._opened().delete(address)

    def close(self) -> None:
        """Borra el archivo y, si no le queda nada más, el directorio del operador."""
        if self._file is not None:
            self._file.close()
            self._file = None
            (self._directory / SPILLED_VALUES_FILENAME).unlink(missing_ok=True)
        remove_if_empty(self._directory)

    def __enter__(self) -> SpilledValues:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _opened(self) -> VariableLengthFile:
        if self._file is None:
            self._directory.mkdir(parents=True, exist_ok=True)
            self._file = VariableLengthFile(self._directory / SPILLED_VALUES_FILENAME, self._config)
        return self._file


def spill_serializer(schema: Schema, values: SpilledValues) -> OutOfRowSerializer:
    """El serializador de las filas de ese esquema que un operador vuelca a disco."""
    return OutOfRowSerializer(RecordSerializer(schema), values)
