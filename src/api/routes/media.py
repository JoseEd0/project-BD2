"""Los archivos multimedia: subir el de una consulta `SIMILAR_TO` y servir los del
resultado, como miniatura o tal cual (el audio, con `Range` para que el reproductor salte).

Solo se sirven imágenes y audios que estén dentro de los directorios que el motor ya puede
leer —el de datos y los de `MINIGESTOR_IMPORT_DIRS`—, con el mismo control que
`CREATE TABLE ... FROM FILE` y que el archivo de un `SIMILAR_TO`: quien puede escribir
`SIMILAR_TO('ruta')` ya puede hacer que el motor lea esa ruta.
"""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import FileResponse, Response

from api.errors import as_http_error, invalid_request
from api.routes.uploads import MEDIA_UPLOAD_PATH, copy_limited, path_for_sql, uploads_of
from api.schemas import MediaUploadResponse
from api.service import Service, ServiceDependency
from errors import DomainError
from multimedia import MediaKind, media_kind_of
from multimedia.thumbnails import THUMBNAIL_EXTENSION, thumbnail
from query.files import resolve_user_file
from storage.atomic import write_atomically

QUERY_DIRECTORY = "consultas"
THUMBNAIL_DIRECTORY = ".miniaturas"
PARTIAL_SUFFIX = ".partial"
THUMBNAIL_MEDIA_TYPE = "image/jpeg"
FALLBACK_MEDIA_TYPE = "application/octet-stream"
# Un archivo de la colección no cambia mientras se expone; el navegador lo guarda una hora.
CACHE_CONTROL = "private, max-age=3600"

MEDIA_FILE_PATH = "/media/file"
MEDIA_THUMBNAIL_PATH = "/media/thumbnail"

router = APIRouter(tags=["multimedia"])

# Las rutas son `def`: leen y escriben archivos, y FastAPI las corre en su pool de hilos.


@router.post(MEDIA_UPLOAD_PATH, response_model=MediaUploadResponse)
def upload_query(
    service: ServiceDependency, file: Annotated[UploadFile, File(description="Imagen o audio")]
) -> MediaUploadResponse:
    """Guarda el archivo de una consulta y devuelve la ruta que va en `SIMILAR_TO`.

    Se guarda con un nombre nuevo, así que dos consultas con el mismo nombre de archivo no
    se pisan; las más viejas se borran al pasar de `max_query_uploads`.
    """
    extension = Path(file.filename or "").suffix.lower()
    try:
        kind = media_kind_of(Path(f"consulta{extension}"))
    except DomainError as error:
        raise as_http_error(error) from error
    directory = _queries_of(service)
    target = directory / f"{uuid.uuid4().hex}{extension}"
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        copy_limited(file.file, partial, service.config.max_upload_bytes)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    _prune(directory, service.config.max_query_uploads)
    return MediaUploadResponse(
        path=path_for_sql(target), media=kind.value, name=file.filename or target.name
    )


@router.get(MEDIA_FILE_PATH)
def media_file(
    service: ServiceDependency, path: Annotated[str, Query(description="Ruta del archivo")]
) -> FileResponse:
    """El archivo tal como está; con `Range`, solo el trozo pedido (`206`)."""
    located, _ = _media(path, service)
    media_type = mimetypes.guess_type(located.name)[0] or FALLBACK_MEDIA_TYPE
    return FileResponse(located, media_type=media_type, headers={"Cache-Control": CACHE_CONTROL})


@router.get(MEDIA_THUMBNAIL_PATH)
def media_thumbnail(
    service: ServiceDependency, path: Annotated[str, Query(description="Ruta de la imagen")]
) -> Response:
    """La imagen reducida a `thumbnail_side` píxeles de lado mayor, en JPEG.

    Se guarda en el directorio de datos con el tamaño y la fecha del original en el nombre:
    pedirla de nuevo no vuelve a decodificar la imagen, y si el original cambia se rehace.
    """
    located, kind = _media(path, service)
    if kind is not MediaKind.IMAGE:
        raise invalid_request(f"'{located.name}' es un audio: no tiene miniatura")
    cached = _thumbnail_path(located, service)
    if not cached.exists():
        try:
            reduced = thumbnail(located, service.config.thumbnail_side)
        except DomainError as error:
            raise as_http_error(error) from error
        cached.parent.mkdir(parents=True, exist_ok=True)
        write_atomically(cached, lambda handle: handle.write(reduced))
    return FileResponse(
        cached, media_type=THUMBNAIL_MEDIA_TYPE, headers={"Cache-Control": CACHE_CONTROL}
    )


def _media(text: str, service: Service) -> tuple[Path, MediaKind]:
    """La ruta resuelta y permitida de una imagen o un audio.

    Raises:
        HTTPException: `400` si sale de los directorios permitidos, no existe o no es una
            imagen ni un audio.
    """
    try:
        located = resolve_user_file(text, service.config)
        return located, media_kind_of(located)
    except DomainError as error:
        raise as_http_error(error) from error


def _thumbnail_path(located: Path, service: Service) -> Path:
    stat = located.stat()
    key = f"{located}:{stat.st_mtime_ns}:{stat.st_size}:{service.config.thumbnail_side}"
    digest = hashlib.sha256(key.encode()).hexdigest()
    return service.config.data_directory / THUMBNAIL_DIRECTORY / f"{digest}{THUMBNAIL_EXTENSION}"


def _queries_of(service: Service) -> Path:
    directory = uploads_of(service) / QUERY_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _prune(directory: Path, keep: int) -> None:
    """Borra los archivos de consulta más viejos hasta dejar `keep`."""
    files = sorted(
        (path for path in directory.iterdir() if path.suffix != PARTIAL_SUFFIX),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in files[keep:]:
        stale.unlink(missing_ok=True)
