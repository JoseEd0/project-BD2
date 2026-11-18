"""Los atajos de consulta que comparten el cliente y sus transacciones: `fetch_all`,
`fetch_one`, `scalar` y `execute`, todos sobre `query`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

from .errors import ResultTruncatedError
from .models import QueryResult, Record

Params = Sequence[Any]
BEGIN_SQL = "BEGIN TRANSACTION"
COMMIT_SQL = "COMMIT"
ROLLBACK_SQL = "ROLLBACK"


def complete_records(result: QueryResult) -> list[Record]:
    """Raises: ResultTruncatedError si el gestor devolvió menos filas de las que había."""
    if result.truncated:
        raise ResultTruncatedError(
            f"el resultado tiene {result.total_rows} filas y llegaron {len(result.rows)}: "
            "pagina con LIMIT y OFFSET o filtra más",
            request_id=result.request_id,
        )
    return result.records()


def first_record(result: QueryResult) -> Record | None:
    records = result.records()
    return records[0] if records else None


def first_value(result: QueryResult) -> Any:
    return result.rows[0][0] if result.rows else None


class SyncQueries(ABC):
    """Los atajos sobre `query`. Cada uno acepta `params` para `$1`, `$2`, … e `idempotent`
    para decidir los reintentos (por omisión, solo se reintenta un script que solo lee)."""

    @abstractmethod
    def query(
        self, sql: str, params: Params = (), *, idempotent: bool | None = None
    ) -> QueryResult:
        """Ejecuta `sql` y devuelve su resultado."""

    def fetch_all(
        self, sql: str, params: Params = (), *, idempotent: bool | None = None
    ) -> list[Record]:
        """Todas las filas como `{columna: valor}`.

        Raises:
            ResultTruncatedError: si el resultado pasa de las filas que el gestor devuelve.
        """
        return complete_records(self.query(sql, params, idempotent=idempotent))

    def fetch_one(
        self, sql: str, params: Params = (), *, idempotent: bool | None = None
    ) -> Record | None:
        """La primera fila, o `None` si no hay ninguna."""
        return first_record(self.query(sql, params, idempotent=idempotent))

    def scalar(self, sql: str, params: Params = (), *, idempotent: bool | None = None) -> Any:
        """La primera columna de la primera fila, o `None` si no hay filas."""
        return first_value(self.query(sql, params, idempotent=idempotent))

    def execute(self, sql: str, params: Params = (), *, idempotent: bool | None = None) -> int:
        """Las filas afectadas por la última sentencia."""
        return self.query(sql, params, idempotent=idempotent).affected_rows


class AsyncQueries(ABC):
    """Los mismos atajos que `SyncQueries`, para el cliente asíncrono."""

    @abstractmethod
    async def query(
        self, sql: str, params: Params = (), *, idempotent: bool | None = None
    ) -> QueryResult:
        """Ejecuta `sql` y devuelve su resultado."""

    async def fetch_all(
        self, sql: str, params: Params = (), *, idempotent: bool | None = None
    ) -> list[Record]:
        """Raises: ResultTruncatedError si el resultado pasa de las filas que devuelve."""
        return complete_records(await self.query(sql, params, idempotent=idempotent))

    async def fetch_one(
        self, sql: str, params: Params = (), *, idempotent: bool | None = None
    ) -> Record | None:
        return first_record(await self.query(sql, params, idempotent=idempotent))

    async def scalar(self, sql: str, params: Params = (), *, idempotent: bool | None = None) -> Any:
        return first_value(await self.query(sql, params, idempotent=idempotent))

    async def execute(
        self, sql: str, params: Params = (), *, idempotent: bool | None = None
    ) -> int:
        return (await self.query(sql, params, idempotent=idempotent)).affected_rows
