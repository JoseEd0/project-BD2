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
import re
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from api.schemas import (
    ColumnInfo,
    PlanInfo,
    QueryRequest,
    QueryResponse,
    StatementOutcome,
    TableInfo,
)
from config import DATA_DIRECTORY_VARIABLE, EngineConfig
from query.catalog import Organization
from query.engine import Engine, QueryResult
from query.plan import PlanNode
from sql import parse_script
from sql.errors import SqlPositionError
from sql.nodes import CreateTableFromFileStatement, DropTableStatement, IndexSpec, IndexType
from storage.types import Value
from txn import LockManager, Session, TransactionManager

TITLE = "Minigestor de Base de Datos Multimodal"
VERSION = "0.1.0"
CORS_ORIGINS_VARIABLE = "MINIGESTOR_CORS_ORIGINS"
DEFAULT_CORS_ORIGINS = "http://localhost:5173"
LOCK_TIMEOUT_VARIABLE = "MINIGESTOR_LOCK_TIMEOUT"
BAD_REQUEST = 400
UPLOAD_DIRECTORY = "uploads"
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ORGANIZATION_INDEX: dict[str, IndexType | None] = {
    Organization.HEAP.value: None,
    Organization.SEQUENTIAL.value: IndexType.SEQUENTIAL,
    Organization.CLUSTERED_BTREE.value: IndexType.BTREE,
}


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
    """Configuración tomada del entorno; nada de rutas ni tiempos incrustados.

    `MINIGESTOR_LOCK_TIMEOUT` sube la espera máxima por un bloqueo: en una demostración en
    vivo conviene ampliarla para que una transacción abierta no caduque mientras se explica.
    """
    defaults = EngineConfig()
    directory = os.environ.get(DATA_DIRECTORY_VARIABLE)
    timeout = os.environ.get(LOCK_TIMEOUT_VARIABLE)
    return EngineConfig(
        data_directory=defaults.data_directory if directory is None else Path(directory),
        lock_timeout_seconds=(
            defaults.lock_timeout_seconds if timeout is None else float(timeout)
        ),
    )


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
        """Ejecuta el script del editor. Devuelve las filas y el plan de la última consulta."""
        session = service.session(request.session_id)
        try:
            texts = _split(request.sql)
            results = session.execute_script(request.sql)
        except SqlPositionError as error:
            raise _as_http_error(error, error.line, error.column, None) from error
        except Exception as error:
            raise _as_http_error(error, None, None, None) from error
        return _as_response(results, texts, session.in_transaction)

    @app.post("/tables/upload", response_model=QueryResponse)
    async def upload(
        file: Annotated[UploadFile, File(description="CSV con cabecera")],
        name: Annotated[str, Form()],
        organization: Annotated[str, Form()] = Organization.HEAP.value,
        key_column: Annotated[str | None, Form()] = None,
        session_id: Annotated[str | None, Form()] = None,
    ) -> QueryResponse:
        """Crea una tabla a partir de un CSV subido, deduciendo el esquema del archivo."""
        statement = _upload_statement(name, organization, key_column)
        path = await _store_upload(service, file, name)
        session = service.session(session_id)
        try:
            result = session.run(_with_path(statement, path))
        except SqlPositionError as error:
            raise _as_http_error(error, error.line, error.column, None) from error
        except Exception as error:
            raise _as_http_error(error, None, None, None) from error
        return _as_response([result], ["CreateTableFromFile"], session.in_transaction)

    @app.delete("/tables", response_model=QueryResponse)
    def drop_all_tables(session_id: str | None = None) -> QueryResponse:
        """Borra todas las tablas y sus archivos. Deja la base de datos vacía.

        Existe para poder empezar una demostración desde cero sin tocar el disco a mano.
        """
        session = service.session(session_id)
        names = service.engine.table_names()
        try:
            results = [session.run(DropTableStatement(name=name)) for name in names]
        except Exception as error:
            raise _as_http_error(error, None, None, None) from error
        labels = ["DropTable"] * len(results)
        return _as_response(results, labels, session.in_transaction)

    @app.delete("/sessions/{identifier}")
    def close_session(identifier: str) -> dict[str, bool]:
        return {"closed": service.close_session(identifier)}

    return app


def _upload_statement(
    name: str, organization: str, key_column: str | None
) -> CreateTableFromFileStatement:
    """Valida los parámetros y arma la sentencia sin concatenar SQL.

    Construir el `CREATE TABLE` como texto abriría la puerta a que un nombre de tabla
    inyectara SQL; aquí el nombre solo puede ser un identificador y viaja como dato.

    Raises:
        HTTPException: si el nombre, la columna o la organización no son válidos.
    """
    if not IDENTIFIER_PATTERN.match(name):
        raise _invalid(f"'{name}' no es un nombre de tabla válido")
    if organization not in ORGANIZATION_INDEX:
        raise _invalid(f"organización desconocida: '{organization}'")
    method = ORGANIZATION_INDEX[organization]
    if method is None:
        return CreateTableFromFileStatement(name=name, path="", index=None)
    if key_column is None or not IDENTIFIER_PATTERN.match(key_column):
        raise _invalid(f"la organización '{organization}' necesita una columna clave válida")
    spec = IndexSpec(method=method, columns=(key_column,))
    return CreateTableFromFileStatement(name=name, path="", index=spec)


def _with_path(
    statement: CreateTableFromFileStatement, path: Path
) -> CreateTableFromFileStatement:
    return CreateTableFromFileStatement(
        name=statement.name,
        path=str(path),
        index=statement.index,
        if_not_exists=statement.if_not_exists,
    )


async def _store_upload(service: Service, file: UploadFile, name: str) -> Path:
    """Guarda el archivo subido junto a los datos, respetando el tamaño máximo.

    Raises:
        HTTPException: si el archivo está vacío o excede `max_upload_bytes`.
    """
    content = await file.read()
    if len(content) == 0:
        raise _invalid("el archivo está vacío")
    if len(content) > service.config.max_upload_bytes:
        raise _invalid(
            f"el archivo ocupa {len(content)} bytes y el máximo es "
            f"{service.config.max_upload_bytes}"
        )
    directory = service.config.data_directory / UPLOAD_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.csv"
    path.write_bytes(content)
    return path


def _invalid(message: str) -> HTTPException:
    return HTTPException(
        status_code=BAD_REQUEST,
        detail={"error": message, "kind": "UploadError", "line": None, "column": None,
                "statement_index": None},
    )


def _split(sql: str) -> list[str]:
    """Texto de cada sentencia, para poder etiquetar el resultado de cada una."""
    return [_describe_statement(statement) for statement in parse_script(sql)]


def _describe_statement(statement: object) -> str:
    return type(statement).__name__.replace("Statement", "")


def _as_response(
    results: list[QueryResult], labels: list[str], in_transaction: bool
) -> QueryResponse:
    last_query = next((item for item in reversed(results) if item.columns), None)
    outcomes = [
        StatementOutcome(
            sql=label,
            message=item.message,
            affected_rows=item.affected_rows,
            elapsed_ms=round(item.elapsed_ms, 3),
            returned_rows=len(item.rows),
        )
        for item, label in zip(results, labels, strict=False)
    ]
    total = round(sum(item.elapsed_ms for item in results), 3)
    if last_query is None:
        message = " · ".join(item.message for item in results if item.message)
        return QueryResponse(
            statements=outcomes,
            message=message,
            affected_rows=sum(item.affected_rows for item in results),
            elapsed_ms=total,
            in_transaction=in_transaction,
        )
    return QueryResponse(
        statements=outcomes,
        columns=list(last_query.columns),
        rows=[[_as_json(value) for value in row] for row in last_query.rows],
        plan=_as_plan(last_query.plan),
        message=last_query.message,
        affected_rows=last_query.affected_rows,
        elapsed_ms=total,
        in_transaction=in_transaction,
    )


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


def _as_http_error(
    error: Exception, line: int | None, column: int | None, statement_index: int | None
) -> HTTPException:
    return HTTPException(
        status_code=BAD_REQUEST,
        detail={
            "error": str(error),
            "kind": type(error).__name__,
            "line": line,
            "column": column,
            "statement_index": statement_index,
        },
    )


app = create_app()
