"""El formato en disco del índice invertido: sus archivos, sus cabeceras y sus registros.

Ver `README.md` para el dibujo de cada archivo y las invariantes que lo sostienen.
"""

from __future__ import annotations

import struct
import sys
from array import array
from pathlib import Path
from typing import NamedTuple

from config import EngineConfig
from storage.layout import FileHeader
from storage.record_id import RECORD_ID_FIELDS, RecordId
from storage.types import StorageError

LEXICON_SUFFIX = ".lexicon"
TERMS_SUFFIX = ".terms"
POSTINGS_SUFFIX = ".postings"
DOCUMENTS_SUFFIX = ".documents"
MANIFEST_SUFFIX = ".manifest"
DOCUMENT_MAP_SUFFIX = ".docmap"
SEGMENT_INFIX = ".seg"
BUILD_DIRECTORY_SUFFIX = ".spimi"
# La 2 lleva en la tabla de documentos los borrados y la longitud de los vivos, y guarda
# las listas en segmentos que enumera el manifiesto.
INVERTED_VERSION = 2
LEXICON_MAGIC = b"IVLX"
TERMS_MAGIC = b"IVTM"
POSTINGS_MAGIC = b"IVPS"
DOCUMENTS_MAGIC = b"IVDC"
MANIFEST_MAGIC = b"IVMF"
FIRST_GENERATION = 0
LANGUAGE_CODE_BYTES = 8
# Idioma, términos, bytes de los términos, postings, bytes de las fronteras, bloques y
# pasadas de mezcla.
LEXICON_HEADER_FIELDS = f"{LANGUAGE_CODE_BYTES}sQQQQII"
TERMS_HEADER_FIELDS = "Q"
POSTINGS_HEADER_FIELDS = "Q"
# `doc_id` repartidos, cuántos de ellos están borrados y suma de las longitudes de los vivos.
DOCUMENTS_HEADER_FIELDS = "QQQ"
# Siguiente generación de segmento, borrados cuyos postings ya quitó una fusión, fusiones
# hechas y número de segmentos; detrás, la generación de cada segmento.
MANIFEST_HEADER_FIELDS = "QQQI"
GENERATION = struct.Struct("<Q")
FIRST_DATA_PAGE = 1

# Un posting son dos enteros sin signo de 32 bits, `doc_id` y `tf`, guardados en
# little-endian: una lista entera se decodifica con un solo `array.frombytes`, en C, y los
# `doc_id` y los `tf` salen de dos rebanadas con salto 2.
POSTING_TYPECODE = "I"
POSTING_FIELDS = 2
POSTING_BYTES = 8
FILE_BYTE_ORDER = "little"
NATIVE_IS_FILE_ORDER = sys.byteorder == FILE_BYTE_ORDER

# UTF-8 usa como mucho cuatro bytes por carácter, así que un término de
# `text_max_token_length` caracteres nunca pasa de cuatro veces esa cifra en bytes.
MAX_UTF8_BYTES_PER_CHARACTER = 4
# Dónde empieza el término en el archivo de términos y cuántos bytes mide, df, frecuencia
# total y dónde empieza su lista, contada en postings.
LEXICON_RECORD = struct.Struct("<QHIQQ")
DOCUMENT_RECORD = struct.Struct("<" + RECORD_ID_FIELDS + "Id")
LANGUAGE_PADDING = b"\x00"


class InvertedIndexError(StorageError):
    """El índice invertido no puede hacer lo que se le pidió."""


class InvertedFormatError(InvertedIndexError):
    """Los archivos no son un índice invertido de este formato, o no son del mismo índice."""


class InvertedIndexExistsError(InvertedIndexError):
    """Ya hay un índice invertido guardado en esa ruta."""


class InvertedIndexNotFoundError(InvertedIndexError):
    """No hay un índice invertido guardado en esa ruta."""


class TermTooLongError(InvertedIndexError):
    """Un término mide más bytes de los que admite el léxico."""


class DocumentNotFoundError(InvertedIndexError):
    """El `doc_id` no corresponde a ningún documento del índice."""


class DocumentAlreadyIndexedError(InvertedIndexError):
    """Ya hay un documento vivo con la dirección de esa fila."""


LEXICON_HEADER = FileHeader(
    LEXICON_MAGIC, INVERTED_VERSION, LEXICON_HEADER_FIELDS, InvertedFormatError, "un léxico"
)
TERMS_HEADER = FileHeader(
    TERMS_MAGIC,
    INVERTED_VERSION,
    TERMS_HEADER_FIELDS,
    InvertedFormatError,
    "un archivo de términos",
)
POSTINGS_HEADER = FileHeader(
    POSTINGS_MAGIC,
    INVERTED_VERSION,
    POSTINGS_HEADER_FIELDS,
    InvertedFormatError,
    "un archivo de postings",
)
DOCUMENTS_HEADER = FileHeader(
    DOCUMENTS_MAGIC,
    INVERTED_VERSION,
    DOCUMENTS_HEADER_FIELDS,
    InvertedFormatError,
    "una tabla de documentos",
)
MANIFEST_HEADER = FileHeader(
    MANIFEST_MAGIC,
    INVERTED_VERSION,
    MANIFEST_HEADER_FIELDS,
    InvertedFormatError,
    "un manifiesto de segmentos",
)


class LexiconEntry(NamedTuple):
    """Un término del léxico: en cuántos documentos aparece (`df`), cuántas veces en total
    y dónde empieza su lista, contada en postings desde el principio del archivo."""

    term: str
    document_frequency: int
    total_frequency: int
    postings_offset: int


class DocumentEntry(NamedTuple):
    """Un documento indexado: la fila de la que viene, cuántos términos tiene, su norma lnc
    y si está borrado."""

    record_id: RecordId
    length: int
    norm: float
    deleted: bool


def lexicon_path(path: Path) -> Path:
    return path.with_name(path.name + LEXICON_SUFFIX)


def terms_path(path: Path) -> Path:
    return path.with_name(path.name + TERMS_SUFFIX)


def postings_path(path: Path) -> Path:
    return path.with_name(path.name + POSTINGS_SUFFIX)


def documents_path(path: Path) -> Path:
    return path.with_name(path.name + DOCUMENTS_SUFFIX)


def manifest_path(path: Path) -> Path:
    return path.with_name(path.name + MANIFEST_SUFFIX)


def document_map_path(path: Path) -> Path:
    return path.with_name(path.name + DOCUMENT_MAP_SUFFIX)


def segment_base(path: Path, generation: int) -> Path:
    """La ruta base de un segmento: `productos.inv.seg3` da `productos.inv.seg3.lexicon`…"""
    return path.with_name(f"{path.name}{SEGMENT_INFIX}{generation}")


def segment_files(base: Path) -> list[Path]:
    """Los tres archivos de un segmento: léxico, términos y postings."""
    return [lexicon_path(base), terms_path(base), postings_path(base)]


def build_directory(path: Path) -> Path:
    """El directorio de los bloques temporales de SPIMI y de las fusiones, que no sobrevive
    a ninguna de las dos."""
    return path.with_name(path.name + BUILD_DIRECTORY_SUFFIX)


def built_files(path: Path) -> list[Path]:
    """Lo que escribe SPIMI: el primer segmento y la tabla de documentos."""
    return [*segment_files(segment_base(path, FIRST_GENERATION)), documents_path(path)]


def max_term_bytes(config: EngineConfig) -> int:
    """Lo más largo, en bytes de UTF-8, que puede medir un término del analizador."""
    return config.text_max_token_length * MAX_UTF8_BYTES_PER_CHARACTER


def encode_language(language: str) -> bytes:
    """El código de idioma tal como lo guarda la cabecera del léxico.

    Raises:
        InvertedIndexError: si no cabe en `LANGUAGE_CODE_BYTES` bytes.
    """
    encoded = language.encode()
    if len(encoded) > LANGUAGE_CODE_BYTES:
        raise InvertedIndexError(
            f"el código de idioma {language!r} no cabe en {LANGUAGE_CODE_BYTES} bytes"
        )
    return encoded


def encode_postings(values: array[int]) -> bytes:
    """Los postings intercalados `doc_id, tf, doc_id, tf, …` en el orden de bytes del archivo."""
    if NATIVE_IS_FILE_ORDER:
        return values.tobytes()
    swapped = array(POSTING_TYPECODE, values)
    swapped.byteswap()
    return swapped.tobytes()


def decode_postings(raw: bytes) -> array[int]:
    """La inversa de `encode_postings`."""
    values = array(POSTING_TYPECODE)
    values.frombytes(raw)
    if not NATIVE_IS_FILE_ORDER:
        values.byteswap()
    return values
