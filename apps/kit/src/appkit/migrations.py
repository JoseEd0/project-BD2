"""Migraciones de esquema repetibles: la lista ordenada de sentencias con que un servicio
crea sus tablas e índices, que corre entera en cada arranque.

Cada sentencia es un `CREATE TABLE IF NOT EXISTS` o un `CREATE INDEX IF NOT EXISTS`: el gestor
la omite si lo que existe es lo pedido y lanza `DefinitionMismatchError` si es otra cosa
(ver `src/sql/README.md`, *IF NOT EXISTS*). Así un cambio de esquema sin migrar —una columna
que pasó de `VECTOR(384)` a `VECTOR(768)`— para el arranque con un mensaje claro, en vez de
fallar después en la primera carga.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from minigestor_client import (
    AsyncClient,
    DefinitionMismatchError,
    MinigestorError,
    PermissionDeniedError,
)

REPEATABLE_DDL = re.compile(r"\s*CREATE\s+(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\b", re.IGNORECASE)

logger = logging.getLogger(__name__)


class MigrationError(Exception):
    """Una migración no se pudo aplicar: el servicio no debe arrancar."""


class NonRepeatableMigrationError(MigrationError):
    """Una sentencia de la lista no se puede correr en cada arranque."""


def check_repeatable(statements: Sequence[str]) -> None:
    """Raises: NonRepeatableMigrationError si una sentencia no empieza por
    `CREATE TABLE IF NOT EXISTS` o `CREATE INDEX IF NOT EXISTS`."""
    for position, sql in enumerate(statements, start=1):
        if not REPEATABLE_DDL.match(sql):
            raise NonRepeatableMigrationError(
                f"la migración {position} no es CREATE TABLE/INDEX IF NOT EXISTS: {sql}"
            )


async def migrate(engine: AsyncClient, statements: Sequence[str]) -> None:
    """Aplica `statements` en orden. Cada una es idempotente, así que se reintenta si el gestor
    no respondió.

    Raises:
        NonRepeatableMigrationError: si alguna no es repetible; no se aplica ninguna.
        MigrationError: si el gestor rechaza una, con su posición y la causa.
    """
    check_repeatable(statements)
    for position, sql in enumerate(statements, start=1):
        try:
            await engine.execute(sql, idempotent=True)
        except MinigestorError as error:
            raise MigrationError(_explained(position, sql, error)) from error
    logger.info("esquema al día: %d migraciones aplicadas o ya presentes", len(statements))


def _explained(position: int, sql: str, error: MinigestorError) -> str:
    if isinstance(error, DefinitionMismatchError):
        cause = f"lo que ya existe tiene otra definición ({error.message})"
    elif isinstance(error, PermissionDeniedError):
        cause = f"la clave no tiene rol admin ({error.message}); define <PREFIJO>MIGRATION_KEY"
    else:
        cause = f"{type(error).__name__}: {error.message}"
    return f"la migración {position} falló: {cause}\n{sql}"
