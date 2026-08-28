"""API REST del minigestor.

Expone lo justo para que el frontend funcione:

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/health` | comprobar que el servidor responde |
| `GET` | `/tables` | panel de archivos: tablas, columnas e índices |
| `POST` | `/query` | panel de consultas: ejecuta SQL y devuelve filas y plan |
| `DELETE` | `/sessions/{id}` | cierra una sesión y aborta lo que tuviera abierto |

Las sentencias sin `session_id` van en autocommit. Enviando un `session_id` estable, el
cliente puede usar `BEGIN … COMMIT` a través de varias peticiones, que es lo que permite
demostrar transacciones desde la interfaz.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api.schemas import (
    ColumnInfo,
    PlanInfo,
    QueryRequest,
    QueryResponse,
    TableInfo,
)
from config import DATA_DIRECTORY_VARIABLE, EngineConfig
from query.engine import Engine
from query.plan import PlanNode
from sql.errors import SqlPositionError
from storage.types import Value
from txn import LockManager, Session, TransactionManager

TITLE = "Minigestor de Base de Datos Multimodal"
VERSION = "0.1.0"
CORS_ORIGINS_VARIABLE = "MINIGESTOR_CORS_ORIGINS"
DEFAULT_CORS_ORIGINS = "http://localhost:5173"
BAD_REQUEST = 400


class Service:
    """Estado compartido del servidor: un motor, un gestor de bloqueos y las sesiones."""

    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.engine = Engine(config)
        self.locks = LockManager(config)
        self.transactions = TransactionManager()
        self._sessions: dict[str, Session] = {}

    def session(self, identifier: str | None) -> Session:
        """Sesión del cliente; una efímera si no envió identificador."""
        if identifier is None:
            return Session(self.engine, self.locks, self.transactions)
        if identifier not in self._sessions:
            self._sessions[identifier] = Session(self.engine, self.locks, self.transactions)
        return self._sessions[identifier]

    def close_session(self, identifier: str) -> bool:
        session = self._sessions.pop(identifier, None)
        if session is None:
            return False
        session.close()
        return True

    def tables(self) -> Iterator[TableInfo]:
        for name in self.engine.table_names():
            yield self._describe(name)

    def close(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()
        self.engine.close()

    def _describe(self, name: str) -> TableInfo:
        table = self.engine.table(name)
        definition = table.definition
        return TableInfo(
            name=definition.name,
            organization=definition.organization.value,
            primary_key=definition.primary_key,
            row_count=table.row_count,
            columns=[
                ColumnInfo(
                    name=item.name,
                    type=item.type.value,
                    length=item.length,
                    nullable=item.nullable,
                    primary_key=item.name == definition.primary_key,
                    indexed_with=_index_method(definition, item.name),
                )
                for item in definition.schema
            ],
            indexes=[index.name for index in definition.indexes],
        )


def _index_method(definition: Any, column: str) -> str | None:
    index = definition.index_on(column)
    return None if index is None else index.method.value


def build_config() -> EngineConfig:
    """Configuración tomada del entorno; nada de rutas incrustadas."""
    directory = os.environ.get(DATA_DIRECTORY_VARIABLE)
    return EngineConfig() if directory is None else EngineConfig(data_directory=Path(directory))


def create_app(config: EngineConfig | None = None) -> FastAPI:
    """Construye la aplicación. Recibe la configuración para poder probarla aislada."""
    service = Service(config or build_config())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        yield
        service.close()

    app = FastAPI(title=TITLE, version=VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get(CORS_ORIGINS_VARIABLE, DEFAULT_CORS_ORIGINS).split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.service = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": VERSION}

    @app.get("/tables", response_model=list[TableInfo])
    def tables() -> list[TableInfo]:
        return list(service.tables())

    @app.post("/query", response_model=QueryResponse)
    def query(request: QueryRequest) -> QueryResponse:
        session = service.session(request.session_id)
        try:
            result = session.execute(request.sql)
        except SqlPositionError as error:
            raise _as_http_error(error, error.line, error.column) from error
        except Exception as error:
            raise _as_http_error(error, None, None) from error
        return QueryResponse(
            columns=list(result.columns),
            rows=[[_as_json(value) for value in row] for row in result.rows],
            plan=_as_plan(result.plan),
            message=result.message,
            affected_rows=result.affected_rows,
            elapsed_ms=round(result.elapsed_ms, 3),
            in_transaction=session.in_transaction,
        )

    @app.delete("/sessions/{identifier}")
    def close_session(identifier: str) -> dict[str, bool]:
        return {"closed": service.close_session(identifier)}

    return app


def _as_plan(node: PlanNode | None) -> PlanInfo | None:
    if node is None:
        return None
    return PlanInfo(
        operation=node.operation,
        detail=node.detail,
        children=[plan for plan in (_as_plan(child) for child in node.children) if plan],
    )


def _as_json(value: Value) -> Any:
    """Los tipos que JSON no conoce viajan como texto."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, int | float | bool | str) or value is None:
        return value
    if isinstance(value, tuple):
        return list(value)
    return str(value)


def _as_http_error(error: Exception, line: int | None, column: int | None) -> HTTPException:
    return HTTPException(
        status_code=BAD_REQUEST,
        detail={
            "error": str(error),
            "kind": type(error).__name__,
            "line": line,
            "column": column,
        },
    )


app = create_app()
