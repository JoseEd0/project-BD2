"""Parámetros de comportamiento del motor.

Todo valor que un experimento pueda querer variar vive aquí; ningún módulo define
constantes de comportamiento en el cuerpo de una función.
"""

from __future__ import annotations

import codecs
import math
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PAGE_SIZE = 4096
DEFAULT_BUFFER_POOL_PAGES = 2048
DEFAULT_SEQUENTIAL_WASTE_RATIO = 0.30
DEFAULT_SEQUENTIAL_FILL_FACTOR = 0.80
DEFAULT_SEQUENTIAL_MAX_CHAIN_PAGES = 16
DEFAULT_SORT_BUFFER_PAGES = 16
DEFAULT_MERGE_FAN_IN = 8
DEFAULT_HASH_PARTITIONS = 16
DEFAULT_HASH_MERGE_FILL = 0.50
DEFAULT_RTREE_MIN_FILL = 0.40
DEFAULT_RTREE_BULK_FILL = 0.90
DEFAULT_TEXT_LENGTH = 256
DEFAULT_BLOB_LENGTH = 256
DEFAULT_TEXT_MIN_TOKEN_LENGTH = 1
# Ninguna palabra de diccionario en español o inglés llega a 40 letras: lo que pasa de ahí
# son cadenas pegadas sin espacios, que solo inflarían el léxico.
DEFAULT_TEXT_MAX_TOKEN_LENGTH = 40
# Un bloque de SPIMI de 16 MiB es el doble del buffer pool. Con las 100 000 fichas de ESCI
# salen 9 bloques; entre 8 y 32 MiB el tiempo de construcción no cambia (lo domina el
# análisis del texto) y la memoria sí, así que no hay razón para un bloque mayor.
DEFAULT_SPIMI_BLOCK_BYTES = 16 * 1024 * 1024
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_CSV_DELIMITER = ","
DEFAULT_CSV_ENCODING = "utf-8"
DEFAULT_MAX_UPLOAD_BYTES = 64 * 1024 * 1024
DEFAULT_MAP_POINTS = 5000
DEFAULT_MAX_RESULT_ROWS = 10_000
DEFAULT_SESSION_IDLE_SECONDS = 15 * 60
FALLBACK_DATA_DIRECTORY = "data"
# Lo justo para la cabecera de un nodo y un par de entradas; con menos, ninguna estructura
# cabe en una página.
MIN_PAGE_SIZE = 32
# El número de ranuras de una página y la ranura de una dirección se guardan en 16 bits.
MAX_PAGE_SIZE = 1 << 15
# Una división cuadrática reparte las entradas de un nodo lleno en dos: ninguna de las dos
# mitades puede exigir más de la mitad.
MAX_RTREE_MIN_FILL = 0.5
MIN_MERGE_FAN_IN = 2
MIN_HASH_PARTITIONS = 2


class ConfigurationError(ValueError):
    """Un parámetro de configuración está fuera de su rango."""


@dataclass(frozen=True, slots=True)
class EngineConfig:
    """Configuración inmutable que atraviesa todas las capas del motor.

    Attributes:
        page_size: tamaño de página en bytes; fija la capacidad de todas las estructuras.
        buffer_pool_pages: páginas residentes en memoria entre todos los archivos abiertos
            (ver `storage/buffer_pool.py`).
        sequential_waste_ratio: fracción de espacio desperdiciado que dispara la
            reorganización del archivo secuencial.
        sequential_fill_factor: fracción de cada página que la reorganización llena,
            dejando hueco para inserciones futuras.
        sequential_max_chain_pages: páginas que puede tener la cadena de desbordamiento de
            una sola página antes de forzar la reorganización.
        sort_buffer_pages: páginas que el ordenamiento externo mantiene en memoria por run.
        merge_fan_in: número de runs que el k-way merge combina en cada pasada.
        hash_partitions: particiones que genera el hashing externo.
        hash_merge_fill: fracción de una cubeta del hash extendible que pueden sumar dos
            cubetas gemelas para fundirse tras un borrado. Por debajo de 1 deja hueco, y
            así la siguiente inserción no las vuelve a partir.
        rtree_min_fill: fracción mínima de ocupación de un nodo del R-Tree; por debajo de
            ella el nodo se disuelve y sus entradas se reinsertan.
        rtree_bulk_fill: fracción de cada nodo que llena la carga masiva del R-Tree,
            dejando hueco para inserciones futuras.
        text_length: longitud fija que se asigna a las columnas TEXT.
        blob_length: longitud fija que se asigna a las columnas BLOB.
        text_min_token_length: caracteres que necesita un token para indexarse. Con 1 se
            conservan las tallas y variantes de una letra («talla m», «usb-c»).
        text_max_token_length: caracteres a partir de los cuales un token se descarta.
        spimi_block_bytes: memoria que el bloque de SPIMI puede ocupar, según la estimación
            de `index/inverted/spimi.py`, antes de escribirse a disco como un bloque.
        lock_timeout_seconds: espera máxima de una transacción por un bloqueo.
        csv_delimiter: separador de los archivos que carga `CREATE TABLE ... FROM FILE`.
        csv_encoding: codificación de esos archivos.
        max_upload_bytes: tamaño máximo de un archivo subido por el API.
        map_points: puntos que el API entrega como máximo para dibujar una tabla en el mapa.
        max_result_rows: filas que el API devuelve como máximo por consulta; el resto se
            cuenta pero no viaja.
        session_idle_seconds: inactividad tras la que el API cierra una sesión y aborta su
            transacción, para que una pestaña cerrada no deje tablas bloqueadas.
        data_directory: raíz donde viven los archivos del gestor.
        import_directories: directorios, además de `data_directory`, de los que
            `CREATE TABLE ... FROM FILE` puede leer.
    """

    page_size: int = DEFAULT_PAGE_SIZE
    buffer_pool_pages: int = DEFAULT_BUFFER_POOL_PAGES
    sequential_waste_ratio: float = DEFAULT_SEQUENTIAL_WASTE_RATIO
    sequential_fill_factor: float = DEFAULT_SEQUENTIAL_FILL_FACTOR
    sequential_max_chain_pages: int = DEFAULT_SEQUENTIAL_MAX_CHAIN_PAGES
    sort_buffer_pages: int = DEFAULT_SORT_BUFFER_PAGES
    merge_fan_in: int = DEFAULT_MERGE_FAN_IN
    hash_partitions: int = DEFAULT_HASH_PARTITIONS
    hash_merge_fill: float = DEFAULT_HASH_MERGE_FILL
    rtree_min_fill: float = DEFAULT_RTREE_MIN_FILL
    rtree_bulk_fill: float = DEFAULT_RTREE_BULK_FILL
    text_length: int = DEFAULT_TEXT_LENGTH
    blob_length: int = DEFAULT_BLOB_LENGTH
    text_min_token_length: int = DEFAULT_TEXT_MIN_TOKEN_LENGTH
    text_max_token_length: int = DEFAULT_TEXT_MAX_TOKEN_LENGTH
    spimi_block_bytes: int = DEFAULT_SPIMI_BLOCK_BYTES
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS
    csv_delimiter: str = DEFAULT_CSV_DELIMITER
    csv_encoding: str = DEFAULT_CSV_ENCODING
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    map_points: int = DEFAULT_MAP_POINTS
    max_result_rows: int = DEFAULT_MAX_RESULT_ROWS
    session_idle_seconds: float = DEFAULT_SESSION_IDLE_SECONDS
    data_directory: Path = Path(FALLBACK_DATA_DIRECTORY)
    import_directories: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        """Rechaza un valor fuera de rango al crear la configuración, no a mitad de una
        consulta, donde el síntoma sería un error lejano o un bucle sin fin.

        Raises:
            ConfigurationError: si algún parámetro está fuera de su rango.
        """
        _require_between("page_size", self.page_size, MIN_PAGE_SIZE, MAX_PAGE_SIZE)
        for name in (
            "buffer_pool_pages",
            "sequential_max_chain_pages",
            "sort_buffer_pages",
            "text_length",
            "blob_length",
            "text_min_token_length",
            "spimi_block_bytes",
            "max_upload_bytes",
            "map_points",
            "max_result_rows",
        ):
            _require_between(name, getattr(self, name), 1, None)
        _require_between("merge_fan_in", self.merge_fan_in, MIN_MERGE_FAN_IN, None)
        _require_between("hash_partitions", self.hash_partitions, MIN_HASH_PARTITIONS, None)
        for name in (
            "sequential_waste_ratio",
            "sequential_fill_factor",
            "hash_merge_fill",
            "rtree_bulk_fill",
        ):
            _require_fraction(name, getattr(self, name), 1.0)
        _require_fraction("rtree_min_fill", self.rtree_min_fill, MAX_RTREE_MIN_FILL)
        if self.rtree_bulk_fill < self.rtree_min_fill:
            raise ConfigurationError("rtree_bulk_fill no puede ser menor que rtree_min_fill")
        _require_between(
            "text_max_token_length", self.text_max_token_length, self.text_min_token_length, None
        )
        _require_positive("lock_timeout_seconds", self.lock_timeout_seconds)
        _require_positive("session_idle_seconds", self.session_idle_seconds)
        if len(self.csv_delimiter) != 1:
            raise ConfigurationError("csv_delimiter tiene que ser un solo carácter")
        try:
            codecs.lookup(self.csv_encoding)
        except LookupError:
            raise ConfigurationError(f"codificación desconocida: {self.csv_encoding}") from None


def _require_between(name: str, value: int, low: int, high: int | None) -> None:
    """Escrito en positivo porque un NaN no es menor ni mayor que nada: con `value < low`
    pasaría todas las cotas."""
    if not (value >= low and (high is None or value <= high)):
        bounds = f"al menos {low}" if high is None else f"entre {low} y {high}"
        raise ConfigurationError(f"{name} tiene que ser {bounds}, no {value}")


def _require_fraction(name: str, value: float, high: float) -> None:
    if not 0.0 < value <= high:
        raise ConfigurationError(f"{name} tiene que estar en (0, {high}], no {value}")


def _require_positive(name: str, value: float) -> None:
    """Un plazo infinito o NaN haría que una espera no terminara nunca."""
    if not 0 < value < math.inf:
        raise ConfigurationError(f"{name} tiene que ser positivo y finito, no {value}")
