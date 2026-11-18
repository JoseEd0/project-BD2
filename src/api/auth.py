"""Quién llama y qué puede hacer: una clave `Bearer` por servicio, cada una con un rol.

Los roles se incluyen unos a otros, `read` < `write` < `admin`:

| Rol | Qué permite |
|---|---|
| `read` | `SELECT`, `EXPLAIN`, transacciones, inspeccionar tablas, buscar por archivo |
| `write` | además `INSERT`, `UPDATE`, `DELETE` y la carga masiva de filas |
| `admin` | además `CREATE`, `DROP`, cargar CSV y vaciar la base |

El servidor solo guarda el SHA-256 de cada clave. Basta con un hash rápido porque las
claves son aleatorias y largas: no hay diccionario con el que adivinarlas, a diferencia de
una contraseña. Sin ninguna clave configurada el API queda abierto, para desarrollo.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any

from fastapi import Depends, Request

from api.errors import forbidden, unauthenticated
from api.tracing import current_trace
from sql.nodes import (
    BeginTransactionStatement,
    CommitTransactionStatement,
    DeleteStatement,
    ExplainStatement,
    InsertStatement,
    RollbackTransactionStatement,
    SelectStatement,
    Statement,
    UpdateStatement,
)

AUTHORIZATION_HEADER = "authorization"
BEARER_SCHEME = "bearer"
OPEN_ACCESS_SERVICE = "anonymous"


class Role(Enum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"

    def includes(self, needed: Role) -> bool:
        return ROLE_ORDER.index(self) >= ROLE_ORDER.index(needed)


ROLE_ORDER = (Role.READ, Role.WRITE, Role.ADMIN)
READING_STATEMENTS = (
    SelectStatement,
    ExplainStatement,
    BeginTransactionStatement,
    CommitTransactionStatement,
    RollbackTransactionStatement,
)
WRITING_STATEMENTS = (InsertStatement, UpdateStatement, DeleteStatement)


@dataclass(frozen=True, slots=True)
class Caller:
    """El servicio autenticado de una petición y su rol."""

    service: str
    role: Role


@dataclass(frozen=True, slots=True)
class ServiceKey:
    """Una clave configurada: a qué servicio pertenece, con qué rol y su SHA-256 en hex."""

    service: str
    role: Role
    digest: str


OPEN_ACCESS = Caller(service=OPEN_ACCESS_SERVICE, role=Role.ADMIN)


def digest_of(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class Authenticator:
    """Reconoce al servicio por su clave."""

    def __init__(self, keys: Sequence[ServiceKey]) -> None:
        self._callers = {key.digest: Caller(key.service, key.role) for key in keys}

    @property
    def is_open(self) -> bool:
        return not self._callers

    def caller_for(self, authorization: str | None) -> Caller:
        """El servicio dueño de la clave de `Authorization: Bearer <clave>`.

        Buscar el hash en un diccionario no filtra la clave por tiempos: quien prueba
        claves no controla los bytes de su SHA-256.

        Raises:
            HTTPException: `401` si falta la clave o no es de ningún servicio.
        """
        if self.is_open:
            return OPEN_ACCESS
        key = _bearer_key(authorization)
        if key is None:
            raise unauthenticated("falta la cabecera 'Authorization: Bearer <clave>'")
        caller = self._callers.get(digest_of(key))
        if caller is None:
            raise unauthenticated("la clave no corresponde a ningún servicio")
        return caller


def required_role(statements: Sequence[Statement]) -> Role:
    """El rol que pide el script: el mayor de los que pide cada sentencia."""
    return max(
        (_role_of(statement) for statement in statements),
        key=ROLE_ORDER.index,
        default=Role.READ,
    )


def authorize(caller: Caller, needed: Role) -> None:
    """Raises: HTTPException `403` si el rol del servicio no alcanza."""
    if not caller.role.includes(needed):
        raise forbidden(
            f"el servicio '{caller.service}' tiene el rol '{caller.role.value}' y esto "
            f"pide '{needed.value}'"
        )


def current_caller(request: Request) -> Caller:
    """Dependencia de FastAPI: el servicio que llama, anotado en la traza para el log."""
    authenticator: Authenticator = request.app.state.authenticator
    caller = authenticator.caller_for(request.headers.get(AUTHORIZATION_HEADER))
    trace = current_trace()
    if trace is not None:
        trace.caller = caller.service
    return caller


CallerDependency = Annotated[Caller, Depends(current_caller)]


def requires(role: Role) -> Any:
    """Dependencia que exige `role` a quien llama, para declararla en una ruta o un router."""

    def check(caller: CallerDependency) -> None:
        authorize(caller, role)

    return Depends(check)


def _role_of(statement: Statement) -> Role:
    """El DDL, y cualquier sentencia que no se reconozca, pide `admin`: una sentencia nueva
    no puede quedar abierta a cualquiera por olvidar clasificarla."""
    if isinstance(statement, READING_STATEMENTS):
        return Role.READ
    if isinstance(statement, WRITING_STATEMENTS):
        return Role.WRITE
    return Role.ADMIN


def _bearer_key(authorization: str | None) -> str | None:
    scheme, _, key = (authorization or "").partition(" ")
    if scheme.lower() != BEARER_SCHEME or not key.strip():
        return None
    return key.strip()
