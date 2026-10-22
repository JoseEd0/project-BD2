"""Carga de archivos CSV: guardarlos para usarlos desde SQL, o crear la tabla de una vez."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from api.errors import DOMAIN_ERRORS, as_http_error, invalid_request
from api.responses import as_response
from api.schemas import FileUploadResponse, QueryResponse
from api.service import Service, ServiceDependency
from query.catalog import Organization
from query.loader import LoaderError, read_header
from sql.nodes import CreateTableFromFileStatement, IndexSpec, IndexType

UPLOAD_DIRECTORY = "uploads"
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Método que se declara sobre la clave para obtener cada organización. En el heap la clave
# es opcional: si se da, las filas siguen en el heap y la clave recibe un índice hash.
ORGANIZATION_INDEX: dict[str, IndexType] = {
    Organization.HEAP.value: IndexType.HASH,
    Organization.SEQUENTIAL.value: IndexType.SEQUENTIAL,
    Organization.CLUSTERED_BTREE.value: IndexType.BTREE,
}
KEYLESS_ORGANIZATIONS = frozenset({Organization.HEAP.value})

router = APIRouter()


@router.post("/files/upload", response_model=FileUploadResponse)
async def upload_file(
    service: ServiceDependency,
    file: Annotated[UploadFile, File(description="CSV con cabecera")],
    name: Annotated[str, Form()],
) -> FileUploadResponse:
    """Solo guarda el archivo, sin crear tabla.

    Devuelve la ruta y las columnas para que la tabla se cree a mano con
    `CREATE TABLE ... FROM FILE`, eligiendo la organización en el propio SQL.
    """
    if not IDENTIFIER_PATTERN.match(name):
        raise invalid_request(f"'{name}' no es un nombre de archivo válido")
    path = await _store_upload(service, file, name)
    try:
        columns = read_header(path, service.config)
    except LoaderError as error:
        path.unlink(missing_ok=True)
        raise as_http_error(error, None, None) from error
    return FileUploadResponse(path=_path_for_sql(path), columns=columns)


@router.post("/tables/upload", response_model=QueryResponse)
async def upload(
    service: ServiceDependency,
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
    except DOMAIN_ERRORS as error:
        raise as_http_error(error, None, None) from error
    return as_response([result], ["CreateTableFromFile"], session.in_transaction)


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
        raise invalid_request(f"'{name}' no es un nombre de tabla válido")
    if organization not in ORGANIZATION_INDEX:
        raise invalid_request(f"organización desconocida: '{organization}'")
    key = (key_column or "").strip()
    if not key:
        if organization in KEYLESS_ORGANIZATIONS:
            return CreateTableFromFileStatement(name=name, path="", index=None)
        raise invalid_request(f"la organización '{organization}' necesita una columna clave")
    if not IDENTIFIER_PATTERN.match(key):
        raise invalid_request(f"'{key}' no es un nombre de columna válido")
    spec = IndexSpec(method=ORGANIZATION_INDEX[organization], columns=(key,))
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
        raise invalid_request("el archivo está vacío")
    if len(content) > service.config.max_upload_bytes:
        raise invalid_request(
            f"el archivo ocupa {len(content)} bytes y el máximo es "
            f"{service.config.max_upload_bytes}"
        )
    directory = service.config.data_directory / UPLOAD_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.csv"
    path.write_bytes(content)
    return path


def _path_for_sql(path: Path) -> str:
    """Ruta tal como la resolverá el motor: relativa al directorio del proceso si cabe.

    El motor abre `FROM FILE` respecto al directorio desde el que corre el API, así que una
    ruta relativa a él es a la vez correcta y legible en el editor.
    """
    absolute = path.resolve()
    try:
        return str(absolute.relative_to(Path.cwd()))
    except ValueError:
        return str(absolute)
