"""Carga de archivos CSV: guardarlos para usarlos desde SQL, o crear la tabla de una vez."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import Annotated, BinaryIO

from fastapi import APIRouter, File, Form, Request, UploadFile

from api.errors import as_http_error, invalid_request, upload_too_large
from api.responses import as_response
from api.schemas import FileUploadResponse, QueryResponse
from api.service import Service, ServiceDependency
from errors import DomainError
from query.catalog import Organization
from query.loader import LoaderError, read_header
from sql.nodes import CreateTableFromFileStatement, IndexSpec, IndexType

FILE_UPLOAD_PATH = "/files/upload"
TABLE_UPLOAD_PATH = "/tables/upload"
UPLOAD_PATHS = frozenset({FILE_UPLOAD_PATH, TABLE_UPLOAD_PATH})
CONTENT_LENGTH_HEADER = "content-length"
# El límite es del archivo, pero Content-Length mide el cuerpo multipart entero: los
# separadores, las cabeceras de cada parte y los campos de texto del formulario.
MULTIPART_ALLOWANCE_BYTES = 64 * 1024
UPLOAD_DIRECTORY = "uploads"
CSV_SUFFIX = ".csv"
PARTIAL_SUFFIX = ".partial"
UPLOAD_CHUNK_BYTES = 1024 * 1024
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Método que se declara sobre la clave para obtener cada organización. En el heap la clave
# es opcional: si se da, las filas siguen en el heap y la clave recibe un índice hash.
ORGANIZATION_INDEX: dict[str, IndexType] = {
    Organization.HEAP.value: IndexType.HASH,
    Organization.SEQUENTIAL.value: IndexType.SEQUENTIAL,
    Organization.CLUSTERED_BTREE.value: IndexType.BTREE,
}
KEYLESS_ORGANIZATIONS = frozenset({Organization.HEAP.value})

router = APIRouter()


# Las dos rutas son `def` y no `async def`: FastAPI las corre en su pool de hilos. Copian
# el archivo y crean la tabla, que puede esperar un bloqueo; dentro del event loop, esa
# espera congelaría al servidor entero, incluido el COMMIT que liberaría el bloqueo.


def declares_oversized_upload(request: Request, limit: int) -> bool:
    """Si la petición anuncia un archivo mayor que `limit` antes de enviarlo.

    Sirve para rechazarla sin recibir el cuerpo: el servidor lo leería entero a un temporal
    antes de entrar en la ruta. Sin Content-Length válido no se decide aquí; el límite se
    vuelve a comprobar al copiar el archivo.
    """
    if request.url.path not in UPLOAD_PATHS:
        return False
    try:
        declared = int(request.headers.get(CONTENT_LENGTH_HEADER, ""))
    except ValueError:
        return False
    return declared > limit + MULTIPART_ALLOWANCE_BYTES


@router.post(FILE_UPLOAD_PATH, response_model=FileUploadResponse)
def upload_file(
    service: ServiceDependency,
    file: Annotated[UploadFile, File(description="CSV con cabecera")],
    name: Annotated[str, Form()],
) -> FileUploadResponse:
    """Solo guarda el archivo, sin crear tabla; si ya había uno con ese nombre, lo reemplaza.

    Devuelve la ruta y las columnas para que la tabla se cree a mano con
    `CREATE TABLE ... FROM FILE`, eligiendo la organización en el propio SQL.
    """
    _require_identifier(name, "archivo")
    path = _uploads_of(service) / f"{name}{CSV_SUFFIX}"
    partial = path.with_name(f"{path.name}.{uuid.uuid4().hex}{PARTIAL_SUFFIX}")
    try:
        _copy_limited(file.file, partial, service.config.max_upload_bytes)
        columns = read_header(partial, service.config)
        partial.replace(path)
    except LoaderError as error:
        raise as_http_error(error) from error
    finally:
        partial.unlink(missing_ok=True)
    return FileUploadResponse(path=_path_for_sql(path), columns=columns)


@router.post(TABLE_UPLOAD_PATH, response_model=QueryResponse)
def upload(
    service: ServiceDependency,
    file: Annotated[UploadFile, File(description="CSV con cabecera")],
    name: Annotated[str, Form()],
    organization: Annotated[str, Form()] = Organization.HEAP.value,
    key_column: Annotated[str | None, Form()] = None,
    session_id: Annotated[str | None, Form()] = None,
) -> QueryResponse:
    """Crea una tabla a partir de un CSV subido, deduciendo el esquema del archivo.

    El CSV solo hace falta mientras se carga: va a un directorio propio de la petición y se
    borra al terminar, haya salido bien o no.
    """
    statement = _upload_statement(name, organization, key_column)
    workspace = _uploads_of(service) / uuid.uuid4().hex
    workspace.mkdir()
    try:
        path = workspace / f"{name}{CSV_SUFFIX}"
        _copy_limited(file.file, path, service.config.max_upload_bytes)
        with service.session(session_id) as session:
            try:
                result = session.run(_with_path(statement, path))
            except DomainError as error:
                raise as_http_error(error, session.in_transaction) from error
            return as_response(
                [result],
                ["CreateTableFromFile"],
                session.in_transaction,
                service.config.max_result_rows,
            )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _upload_statement(
    name: str, organization: str, key_column: str | None
) -> CreateTableFromFileStatement:
    """Valida los parámetros y arma la sentencia sin concatenar SQL.

    Construir el `CREATE TABLE` como texto abriría la puerta a que un nombre de tabla
    inyectara SQL; aquí el nombre solo puede ser un identificador y viaja como dato.

    Raises:
        HTTPException: si el nombre, la columna o la organización no son válidos.
    """
    _require_identifier(name, "tabla")
    if organization not in ORGANIZATION_INDEX:
        raise invalid_request(f"organización desconocida: '{organization}'")
    key = (key_column or "").strip()
    if not key:
        if organization in KEYLESS_ORGANIZATIONS:
            return CreateTableFromFileStatement(name=name, path="", index=None)
        raise invalid_request(f"la organización '{organization}' necesita una columna clave")
    _require_identifier(key, "columna")
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


def _require_identifier(name: str, what: str) -> None:
    """`fullmatch`, no `match` con `$`: el `$` de `re` acepta un salto de línea final, y
    `"tabla\\n"` llegaría al disco como nombre de archivo."""
    if not IDENTIFIER_PATTERN.fullmatch(name):
        raise invalid_request(f"'{name}' no es un nombre de {what} válido")


def _uploads_of(service: Service) -> Path:
    directory = service.config.data_directory / UPLOAD_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _copy_limited(source: BinaryIO, target: Path, limit: int) -> None:
    """Copia el archivo subido por bloques, sin tenerlo entero en memoria.

    Raises:
        HTTPException: si está vacío (`400`) o pasa de `limit` bytes (`413`); en ese caso
            no queda nada escrito en `target`.
    """
    copied = 0
    with target.open("wb") as output:
        while chunk := source.read(UPLOAD_CHUNK_BYTES):
            copied += len(chunk)
            if copied > limit:
                break
            output.write(chunk)
    if copied == 0 or copied > limit:
        target.unlink(missing_ok=True)
        raise invalid_request("el archivo está vacío") if copied == 0 else upload_too_large(limit)


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
