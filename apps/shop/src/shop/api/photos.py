"""Las fotos: la de cada producto, servida desde el dataset, y buscar con una foto subida.

La columna `imagen` guarda la ruta de la foto **vista desde el gestor**. El API no la abre
tal cual: toma solo su nombre, que tiene que ser el SHA-256 de la foto (así las guarda
`shop.dataset.images`), y la busca bajo su propia carpeta del dataset (`SHOP_DATA_DIR`). Una
ruta manipulada no puede salir de esa carpeta.

Buscar con una foto pasa la foto por la torre de imagen de CLIP y busca sus vecinos en el
HNSW de `shop_clip`, como la búsqueda de fotos con palabras.
"""

from __future__ import annotations

import io
import re
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from typing import Annotated, Protocol

import numpy as np
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

from appkit import Engine, ResourceNotFoundError, ServiceError, timed
from shop.clip.encoder import Embeddings
from shop.clip.preprocessing import RGB, PixelValues, pixel_values
from shop.dataset.images import relative_path_of

from . import queries
from .filters import Filter
from .models import ScoredProduct
from .routes import CLIP_STAGE, ENGINE_STAGE, Limit, Settings, clip_search, search_limit

PHOTO_NAME = re.compile(r"[0-9a-f]{64}")
PHOTO_MEDIA_TYPE = "image/jpeg"
MEBIBYTE = 1024 * 1024
UNREADABLE_PHOTO = "La foto no se pudo leer: envía un JPEG, PNG o WebP."


class PixelEmbedder(Protocol):
    """La torre de imagen de CLIP (`ClipImageEncoder`), o una de mentira en los tests."""

    def embed_pixels(self, batch: PixelValues) -> Embeddings: ...


class ImageEncoderNotLoadedError(ServiceError):
    """El servicio arrancó sin la torre de imagen de CLIP."""

    status = HTTPStatus.SERVICE_UNAVAILABLE


class ProductPhotoNotFoundError(ResourceNotFoundError):
    """El producto no existe, no tiene foto o el API no ve la carpeta de fotos."""


class UploadTooLargeError(ServiceError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE


class TooManyPixelsError(ServiceError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE


class UnreadablePhotoError(ServiceError):
    """Lo subido está vacío o no es una imagen que Pillow pueda decodificar."""


router = APIRouter()


def image_encoder_of(request: Request) -> PixelEmbedder:
    encoder: PixelEmbedder | None = getattr(request.app.state, "image_encoder", None)
    if encoder is None:
        raise ImageEncoderNotLoadedError("el servicio no cargó la torre de imagen de CLIP")
    return encoder


ImageEncoder = Annotated[PixelEmbedder, Depends(image_encoder_of)]
Photo = Annotated[UploadFile, File(description="La foto: JPEG, PNG o WebP.")]


@router.get("/productos/{product_id}/foto", response_class=FileResponse)
async def product_photo(product_id: str, engine: Engine, settings: Settings) -> FileResponse:
    """La foto del producto (JPEG), con caché del navegador.

    Raises:
        ProductPhotoNotFoundError: si el producto no existe o no tiene foto, o si el API no
            tiene `SHOP_DATA_DIR`.
    """
    root = settings.photos_root
    if root is None:
        raise ProductPhotoNotFoundError("el servicio no tiene la carpeta de fotos")
    with timed(ENGINE_STAGE):
        found = await engine.fetch_one(queries.PRODUCT_PHOTO, [product_id])
    path = _photo_path(root, None if found is None else found["imagen"])
    if path is None:
        raise ProductPhotoNotFoundError(f"el producto {product_id} no tiene foto")
    return FileResponse(
        path,
        media_type=PHOTO_MEDIA_TYPE,
        headers={"Cache-Control": f"public, max-age={settings.photo_cache_seconds}"},
    )


@router.post("/busqueda/foto")
async def photo_search(
    foto: Photo,
    engine: Engine,
    settings: Settings,
    encoder: ImageEncoder,
    filters: Filter,
    limite: Limit = None,
) -> list[ScoredProduct]:
    """Productos cuya foto se parece a la subida, con la torre de imagen de CLIP y el HNSW
    de `shop_clip`.

    Raises:
        UploadTooLargeError: si pesa más de `SHOP_PHOTO_MAX_BYTES`.
        TooManyPixelsError: si mide más de `SHOP_PHOTO_MAX_PIXELS`.
        UnreadablePhotoError: si está vacía o no es una imagen.
    """
    limit = search_limit(limite, settings)
    data = await _read_upload(foto, settings.photo_max_bytes)
    with timed(CLIP_STAGE):
        vector = await run_in_threadpool(_embed, encoder, data, settings.photo_max_pixels)
    return await clip_search(engine, settings, vector, limit, filters)


def _photo_path(root: Path, stored: object) -> Path | None:
    if not isinstance(stored, str):
        return None
    digest = PurePosixPath(stored).stem
    if not PHOTO_NAME.fullmatch(digest):
        return None
    path = root / relative_path_of(digest)
    return path if path.is_file() else None


async def _read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UploadTooLargeError(
            f"La foto pesa más de {max_bytes / MEBIBYTE:g} MiB: envía una más liviana."
        )
    if not data:
        raise UnreadablePhotoError(UNREADABLE_PHOTO)
    return data


def _embed(encoder: PixelEmbedder, data: bytes, max_pixels: int) -> Embeddings:
    image = _decode(data, max_pixels)
    (vector,) = encoder.embed_pixels(pixel_values(image)[np.newaxis])
    return np.asarray(vector, dtype=np.float32)


def _decode(data: bytes, max_pixels: int) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width * height > max_pixels:
                raise TooManyPixelsError(
                    f"La foto mide {width} x {height} px; el máximo es "
                    f"{max_pixels / 1_000_000:g} megapíxeles."
                )
            upright = ImageOps.exif_transpose(image)
            return upright.convert(RGB)
    except Image.DecompressionBombError as error:
        raise TooManyPixelsError(str(error)) from error
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError) as error:
        raise UnreadablePhotoError(UNREADABLE_PHOTO) from error
