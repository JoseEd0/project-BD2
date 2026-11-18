"""Los archivos multimedia: subir el de una consulta `SIMILAR_TO`, consultar por segmentos
un audio subido y servir los archivos del resultado, como miniatura o tal cual (el audio,
con `Range` para que el reproductor salte).

Solo se sirven imágenes y audios que estén dentro de los directorios que el motor ya puede
leer —el de datos y los de `MINIGESTOR_IMPORT_DIRS`—, con el mismo control que
`CREATE TABLE ... FROM FILE` y que el archivo de un `SIMILAR_TO`: quien puede escribir
`SIMILAR_TO('ruta')` ya puede hacer que el motor lea esa ruta.
"""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, Response

from api.auth import CallerDependency
from api.errors import as_http_error, invalid_request
from api.responses import as_json_value
from api.routes.uploads import (
    MEDIA_SEGMENTS_PATH,
    MEDIA_UPLOAD_PATH,
    copy_limited,
    path_for_sql,
    uploads_of,
)
from api.schemas import (
    MediaUploadResponse,
    SegmentInfo,
    SegmentNeighbourInfo,
    SegmentSearchResponse,
    SegmentTimings,
    TimeRangeInfo,
)
from api.service import Service, ServiceDependency
from errors import DomainError
from index.similarity import SimilarRow
from multimedia import MediaKind, TemporalFragment, media_kind_of, split_fragment
from multimedia.thumbnails import THUMBNAIL_EXTENSION, thumbnail
from query.files import resolve_user_file
from query.segment_search import SegmentRequest, SegmentSearch, search_segments
from storage.atomic import write_atomically

QUERY_DIRECTORY = "consultas"
SEGMENTS_DIRECTORY = "segmentos"
# Los vecinos de cada ventana si la petición no pide otro número: para alinear tramos
# copiados basta con los pocos más parecidos.
DEFAULT_SEGMENT_NEIGHBOURS = 5
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


@router.post(MEDIA_SEGMENTS_PATH, response_model=SegmentSearchResponse)
def segments(
    service: ServiceDependency,
    caller: CallerDependency,
    file: Annotated[UploadFile, File(description="Audio que se consulta por segmentos")],
    table: Annotated[str, Form(description="Tabla con la columna de rutas indexada")],
    column: Annotated[
        str | None, Form(description="Columna de las rutas; sin ella, la única indexada")
    ] = None,
    k: Annotated[int, Form(ge=1, description="Filas por ventana")] = DEFAULT_SEGMENT_NEIGHBOURS,
    window: Annotated[
        float | None, Form(gt=0, description="Segundos de cada ventana; por omisión, 10")
    ] = None,
    hop: Annotated[
        float | None, Form(gt=0, description="Segundos entre ventanas; por omisión, 5")
    ] = None,
    session_id: Annotated[str | None, Form()] = None,
) -> SegmentSearchResponse:
    """Corta el audio subido en ventanas de `window` segundos cada `hop` y devuelve las `k`
    filas más parecidas a cada una, con el índice HNSW o IVF de la columna de rutas.

    El audio se decodifica una vez y las ventanas se buscan en un solo lote; cada una
    recibe lo mismo que un `SIMILAR_TO('audio#t=inicio,fin', k)`. Un audio más corto que una
    ventana es una sola ventana. El archivo solo vive mientras dura la petición. Lee con
    bloqueo compartido sobre la tabla.
    """
    _require_audio(file.filename)
    request = SegmentRequest(
        column=column,
        k=k,
        window=window or service.config.segment_window_seconds,
        hop=hop or service.config.segment_hop_seconds,
    )
    workspace = uploads_of(service) / SEGMENTS_DIRECTORY / uuid.uuid4().hex
    workspace.mkdir(parents=True)
    try:
        audio = workspace / f"audio{Path(file.filename or '').suffix.lower()}"
        copy_limited(file.file, audio, service.config.max_upload_bytes)
        with service.session(caller.service, session_id) as session:
            try:
                found = session.inspect(
                    [table],
                    lambda: search_segments(
                        service.engine.table(table), audio, request, service.config.max_result_rows
                    ),
                )
            except DomainError as error:
                raise as_http_error(error, session.in_transaction) from error
        return _segments_response(found)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@router.get(MEDIA_FILE_PATH)
def media_file(
    service: ServiceDependency, path: Annotated[str, Query(description="Ruta del archivo")]
) -> FileResponse:
    """El archivo tal como está; con `Range`, solo el trozo pedido (`206`). Un fragmento
    `#t=` al final de la ruta se ignora: el tramo lo elige el reproductor."""
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
    """La ruta resuelta y permitida de una imagen o un audio, sin su fragmento.

    Raises:
        HTTPException: `400` si sale de los directorios permitidos, no existe, no es una
            imagen ni un audio o su fragmento no se puede leer.
    """
    try:
        written, _ = split_fragment(text)
        located = resolve_user_file(written, service.config)
        return located, media_kind_of(located)
    except DomainError as error:
        raise as_http_error(error) from error


def _require_audio(filename: str | None) -> None:
    try:
        kind = media_kind_of(Path(f"consulta{Path(filename or '').suffix.lower()}"))
    except DomainError as error:
        raise as_http_error(error) from error
    if kind is not MediaKind.AUDIO:
        raise invalid_request(
            f"'{filename}' es una imagen: la consulta por segmentos es de un audio"
        )


def _segments_response(found: SegmentSearch) -> SegmentSearchResponse:
    result = found.result
    columns = [field.name for field in found.table.schema.fields]
    segments = [
        SegmentInfo(
            start=segment.window.start,
            end=segment.window.end or result.duration,
            neighbours=[_neighbour(found, columns, similar) for similar in segment.rows],
        )
        for segment in result.segments
    ]
    return SegmentSearchResponse(
        table=found.table.name,
        column=found.column,
        index=found.index_name,
        method=found.method.value,
        metric=result.metric.value,
        k=found.request.k,
        window=found.request.window,
        hop=found.request.hop,
        duration=result.duration,
        columns=columns,
        segments=segments,
        distance_computations=result.stats.distance_computations,
        timings=SegmentTimings(
            decode_ms=result.decode_ms,
            extraction_ms=result.extraction_ms,
            search_ms=result.search_ms,
        ),
    )


def _neighbour(
    found: SegmentSearch, columns: list[str], similar: SimilarRow
) -> SegmentNeighbourInfo:
    """La fila de un vecino con su archivo y su tramo, leídos de la columna de rutas, que
    ya se validó al indexarla."""
    values = found.rows[similar.record_id]
    path, fragment = split_fragment(str(values[columns.index(found.column)]))
    return SegmentNeighbourInfo(
        row={name: as_json_value(value) for name, value in zip(columns, values, strict=True)},
        path=path,
        fragment=_time_range(fragment),
        score=similar.score,
    )


def _time_range(fragment: TemporalFragment | None) -> TimeRangeInfo | None:
    if fragment is None:
        return None
    return TimeRangeInfo(start=fragment.start, end=fragment.end)


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
