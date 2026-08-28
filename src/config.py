"""Parámetros de comportamiento del motor.

Todo valor que un experimento pueda querer variar vive aquí; ningún módulo define
constantes de comportamiento en el cuerpo de una función.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PAGE_SIZE = 4096
DEFAULT_BUFFER_POOL_PAGES = 128
DEFAULT_SEQUENTIAL_WASTE_RATIO = 0.30
DEFAULT_SEQUENTIAL_FILL_FACTOR = 0.80
DEFAULT_SORT_BUFFER_PAGES = 16
DEFAULT_MERGE_FAN_IN = 8
DEFAULT_HASH_PARTITIONS = 16
DEFAULT_TEXT_LENGTH = 256
DEFAULT_BLOB_LENGTH = 256
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_CSV_DELIMITER = ","
DEFAULT_CSV_ENCODING = "utf-8"
DATA_DIRECTORY_VARIABLE = "MINIGESTOR_DATA_DIR"
FALLBACK_DATA_DIRECTORY = "data"


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Configuración inmutable que atraviesa todas las capas del motor.

    Attributes:
        page_size: tamaño de página en bytes; fija la capacidad de todas las estructuras.
        buffer_pool_pages: páginas residentes en memoria por archivo abierto.
        sequential_waste_ratio: fracción de espacio desperdiciado que dispara la
            reorganización del archivo secuencial.
        sequential_fill_factor: fracción de cada página que la reorganización llena,
            dejando hueco para inserciones futuras.
        sort_buffer_pages: páginas que el ordenamiento externo mantiene en memoria por run.
        merge_fan_in: número de runs que el k-way merge combina en cada pasada.
        hash_partitions: particiones que genera el hashing externo.
        text_length: longitud fija que se asigna a las columnas TEXT.
        blob_length: longitud fija que se asigna a las columnas BLOB.
        lock_timeout_seconds: espera máxima de una transacción por un bloqueo.
        csv_delimiter: separador de los archivos que carga `CREATE TABLE ... FROM FILE`.
        csv_encoding: codificación de esos archivos.
        data_directory: raíz donde viven los archivos del gestor.
    """

    page_size: int = DEFAULT_PAGE_SIZE
    buffer_pool_pages: int = DEFAULT_BUFFER_POOL_PAGES
    sequential_waste_ratio: float = DEFAULT_SEQUENTIAL_WASTE_RATIO
    sequential_fill_factor: float = DEFAULT_SEQUENTIAL_FILL_FACTOR
    sort_buffer_pages: int = DEFAULT_SORT_BUFFER_PAGES
    merge_fan_in: int = DEFAULT_MERGE_FAN_IN
    hash_partitions: int = DEFAULT_HASH_PARTITIONS
    text_length: int = DEFAULT_TEXT_LENGTH
    blob_length: int = DEFAULT_BLOB_LENGTH
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS
    csv_delimiter: str = DEFAULT_CSV_DELIMITER
    csv_encoding: str = DEFAULT_CSV_ENCODING
    data_directory: Path = Path(FALLBACK_DATA_DIRECTORY)

    @classmethod
    def from_environment(cls) -> EngineConfig:
        """Construye la configuración por defecto tomando el directorio de datos del entorno."""
        directory = os.environ.get(DATA_DIRECTORY_VARIABLE, FALLBACK_DATA_DIRECTORY)
        return cls(data_directory=Path(directory))
