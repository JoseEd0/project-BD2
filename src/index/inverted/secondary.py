"""El índice invertido como índice secundario sobre una columna de texto de una tabla.

La tabla le avisa de cada fila que entra, sale o cambia con su dirección, igual que a un
B+ o a un hash, y deshacer una transacción vuelve a pasar por ahí: deshacer una
inserción borra el documento y deshacer un borrado lo vuelve a insertar, con un `doc_id`
nuevo. Lo que este índice responde no es una igualdad sino una búsqueda rankeada: se
consulta con un `Ranker` sobre `inverted_index`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from config import EngineConfig
from index.secondary import ColumnIndex
from storage.record import RecordSerializer
from storage.record_id import RecordId
from storage.types import Key
from text import DEFAULT_LANGUAGE, Analyzer, language_named

from .format import InvertedIndexError
from .index import InvertedIndex


class TextSearchEqualityError(InvertedIndexError):
    """Se pidió una igualdad a un índice invertido, que solo responde búsquedas rankeadas."""


class NotTextValueError(InvertedIndexError):
    """La columna indexada trae un valor que no es texto."""


class IndexLanguageMismatchError(InvertedIndexError):
    """El índice guardado se construyó con otro idioma que el pedido."""


class TextSearchIndex(ColumnIndex):
    """Índice invertido sobre una columna de texto: cada fila con texto es un documento,
    y su dirección en el heap es la que se devuelve al buscar. Las filas con NULL no
    entran.

    `language` es el del analizador con que se crea el índice; al abrir uno guardado
    tiene que coincidir con el suyo.

    Raises:
        UnknownLanguageError: si el idioma no está registrado.
        IndexLanguageMismatchError: si el índice guardado es de otro idioma.
        InvertedFormatError: si los archivos guardados no son de este formato.
    """

    def __init__(
        self,
        path: Path,
        serializer: RecordSerializer,
        column: str,
        config: EngineConfig,
        language: str = DEFAULT_LANGUAGE,
    ) -> None:
        super().__init__(serializer, column)
        self._path = path
        self._config = config
        self._language = language_named(language).code
        self._index = self._open_or_create()

    files_of = staticmethod(InvertedIndex.files_of)

    @property
    def inverted_index(self) -> InvertedIndex:
        """El índice que consultan el ranking y el planificador."""
        return self._index

    def build(self, rows: Iterable[tuple[bytes, RecordId]]) -> None:
        """Indexa de una vez las filas de la tabla con SPIMI, en vez de una a una.

        Raises:
            InvertedIndexError: si el índice ya tiene documentos.
        """
        if self._index.document_count or self._index.deleted_count:
            raise InvertedIndexError(
                f"{self._path.name} ya tiene documentos: solo se construye un índice vacío"
            )
        self._index.close()
        for file in InvertedIndex.files_of(self._path):
            file.unlink(missing_ok=True)
        self._index = InvertedIndex.build(
            self._path, self._documents(rows), self._analyzer(), self._config
        )

    def describe(self) -> dict[str, Any]:
        return self._index.describe()

    def flush(self) -> None:
        self._index.flush()

    def close(self) -> None:
        self._index.close()

    def _add(self, value: Key, record_id: RecordId) -> None:
        self._index.insert(record_id, self._text(value))

    def _remove(self, value: Key, record_id: RecordId) -> bool:
        return self._index.delete(record_id)

    def _find(self, value: Key) -> list[RecordId]:
        raise TextSearchEqualityError(
            "un índice invertido no responde igualdades: se consulta con MATCH"
        )

    def _documents(self, rows: Iterable[tuple[bytes, RecordId]]) -> Iterator[tuple[RecordId, str]]:
        for record, record_id in rows:
            value = self._value_of(record)
            if value is not None:
                yield record_id, self._text(value)

    def _text(self, value: Key) -> str:
        if not isinstance(value, str):
            raise NotTextValueError(
                f"un índice invertido indexa texto y recibió {type(value).__name__}"
            )
        return value

    def _analyzer(self) -> Analyzer:
        return Analyzer(self._config, self._language)

    def _open_or_create(self) -> InvertedIndex:
        if not any(file.exists() for file in InvertedIndex.files_of(self._path)):
            return InvertedIndex.build(self._path, (), self._analyzer(), self._config)
        index = InvertedIndex(self._path, self._config)
        if index.language != self._language:
            index.close()
            raise IndexLanguageMismatchError(
                f"{self._path.name} se construyó en '{index.language}' y se pidió "
                f"'{self._language}'"
            )
        return index
