"""Las fotos principales de los productos: se bajan, se reducen, se recodifican y se
guardan una sola vez por contenido.

Cada foto queda en `images/<xx>/<sha256>.jpg`, con el SHA-256 de sus propios bytes como
nombre: dos productos con la misma foto comparten archivo, y comprobar que un archivo está
sano es volver a hashearlo. Lo que pasó con cada URL se anota en `images.jsonl`, una línea
por intento, así que una ejecución cortada sigue donde quedó: no repite las fotos que ya
verifican ni las que fallaron sin remedio (un 404, un archivo que no es imagen), y vuelve a
intentar las que fallaron por la red o por un 429/5xx.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from appkit.download import sha256_of
from shop.dataset.errors import DatasetError
from shop.dataset.files import write_bytes_atomically
from shop.dataset.transport import RETRY_CANDIDATES, Transport, fetch_bytes, is_retryable

IMAGES_DIRECTORY = "images"
LEDGER_NAME = "images.jsonl"
JPEG_SUFFIX = ".jpg"
SHARD_CHARACTERS = 2
JPEG_FORMAT = "JPEG"
RGB_MODE = "RGB"
# Fondo sobre el que se aplanan las fotos con transparencia: el blanco de las fichas.
BACKGROUND_COLOR = (255, 255, 255)
PROGRESS_EVERY = 1000

logger = logging.getLogger(__name__)


class ImageDecodeError(DatasetError):
    """Los bytes bajados no son una imagen que se pueda abrir."""


@dataclass(frozen=True, slots=True)
class ImageSettings:
    """Cómo se bajan y se guardan las fotos.

    `workers` acota las descargas simultáneas al CDN de Amazon; `attempts` y
    `backoff_seconds`, los reintentos de una foto ante un corte, un 429 o un 5xx.
    """

    max_side: int
    jpeg_quality: int
    workers: int
    attempts: int
    backoff_seconds: float


@dataclass(frozen=True, slots=True)
class ImageOutcome:
    """Lo que quedó de una URL: la ruta de su archivo o el error y si vale reintentarlo."""

    url: str
    image: str | None
    error: str | None
    retryable: bool


def to_jpeg(data: bytes, max_side: int, quality: int) -> bytes:
    """La imagen de `data` como JPEG RGB sin metadatos, girada según su EXIF y reducida,
    sin agrandarla nunca, a que su lado mayor mida a lo sumo `max_side`.

    Raises:
        ImageDecodeError: si `data` no es una imagen que Pillow abra.
    """
    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = _flattened(ImageOps.exif_transpose(opened))
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, JPEG_FORMAT, quality=quality, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise ImageDecodeError(f"no es una imagen válida: {error}") from error


class ImageStore:
    """Las fotos guardadas por su contenido bajo `<raíz>/images/`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def save(self, jpeg: bytes) -> str:
        """Guarda la foto, salvo que ya esté sana. Devuelve su ruta relativa a la raíz."""
        relative = relative_path_of(hashlib.sha256(jpeg).hexdigest())
        if not self.verify(relative):
            write_bytes_atomically(self._root / relative, jpeg)
        return relative

    def verify(self, relative: str) -> bool:
        """Si el archivo existe y sus bytes tienen el SHA-256 de su nombre."""
        path = self._root / relative
        if not path.is_file():
            return False
        return sha256_of(path) == path.stem

    def size_of(self, relative: str) -> int:
        return (self._root / relative).stat().st_size


def relative_path_of(digest: str) -> str:
    return f"{IMAGES_DIRECTORY}/{digest[:SHARD_CHARACTERS]}/{digest}{JPEG_SUFFIX}"


class ImageLedger:
    """El registro `images.jsonl`: una línea por intento; de cada URL vale la última."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, ImageOutcome]:
        """El último resultado de cada URL. Una línea cortada a medias (la ejecución se
        interrumpió al escribirla) se ignora: esa URL se vuelve a intentar."""
        outcomes: dict[str, ImageOutcome] = {}
        if not self._path.is_file():
            return outcomes
        with self._path.open(encoding="utf-8") as lines:
            for line in lines:
                try:
                    outcome = ImageOutcome(**json.loads(line))
                except (json.JSONDecodeError, TypeError):
                    continue
                outcomes[outcome.url] = outcome
        return outcomes

    def append(self, outcome: ImageOutcome) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(outcome), ensure_ascii=False) + "\n")


class ImageFetcher:
    """Baja y guarda las fotos que faltan, con `settings.workers` descargas a la vez."""

    def __init__(
        self,
        root: Path,
        transport: Transport,
        settings: ImageSettings,
        sleep: Callable[[float], None],
    ) -> None:
        self._store = ImageStore(root)
        self._ledger = ImageLedger(root / LEDGER_NAME)
        self._transport = transport
        self._settings = settings
        self._sleep = sleep

    @property
    def store(self) -> ImageStore:
        return self._store

    def fetch(self, urls: Iterable[str]) -> dict[str, ImageOutcome]:
        """El resultado de cada URL distinta de `urls`. Solo va a la red por las que no
        están hechas: ni guardadas y verificadas, ni fallidas sin remedio. Un fallo nunca
        detiene la descarga: queda anotado y se sigue con las demás."""
        wanted = sorted(set(urls))
        known = self._ledger.load()
        outcomes = {url: known[url] for url in wanted if url in known and self._is_done(known[url])}
        pending = [url for url in wanted if url not in outcomes]
        logger.info("fotos: %d hechas, %d por bajar", len(outcomes), len(pending))
        with ThreadPoolExecutor(max_workers=self._settings.workers) as pool:
            futures = [pool.submit(self._process, url) for url in pending]
            for done, future in enumerate(as_completed(futures), start=1):
                outcome = future.result()
                self._ledger.append(outcome)
                outcomes[outcome.url] = outcome
                if outcome.error:
                    logger.warning("foto omitida %s: %s", outcome.url, outcome.error)
                if done % PROGRESS_EVERY == 0:
                    logger.info("fotos: %d de %d", done, len(pending))
        return outcomes

    def _is_done(self, outcome: ImageOutcome) -> bool:
        if outcome.image is not None:
            return self._store.verify(outcome.image)
        return not outcome.retryable

    def _process(self, url: str) -> ImageOutcome:
        settings = self._settings
        try:
            data = fetch_bytes(
                url, self._transport, settings.attempts, settings.backoff_seconds, self._sleep
            )
            jpeg = to_jpeg(data, settings.max_side, settings.jpeg_quality)
        except ImageDecodeError as error:
            return ImageOutcome(url, None, str(error), retryable=False)
        except RETRY_CANDIDATES as error:
            return ImageOutcome(url, None, str(error), retryable=is_retryable(error))
        return ImageOutcome(url, self._store.save(jpeg), None, retryable=False)


@dataclass(frozen=True, slots=True)
class ImageReport:
    """Cuántos productos quedaron con foto, cuántos no y cuánto ocupan los archivos.

    `deduplicated` cuenta los productos con foto que comparten archivo con otro anterior:
    `with_image - deduplicated` es el número de archivos.
    """

    without_url: int
    with_image: int
    failed: int
    deduplicated: int
    files: int
    bytes: int


def image_report(
    image_urls: Mapping[str, str | None], outcomes: Mapping[str, ImageOutcome], store: ImageStore
) -> ImageReport:
    """El resumen de las fotos de cada producto (`image_urls`: su URL, o `None`)."""
    paths = [
        outcomes[url].image for url in image_urls.values() if url is not None and url in outcomes
    ]
    saved = [path for path in paths if path is not None]
    files = set(saved)
    without_url = sum(1 for url in image_urls.values() if url is None)
    return ImageReport(
        without_url=without_url,
        with_image=len(saved),
        failed=len(image_urls) - without_url - len(saved),
        deduplicated=len(saved) - len(files),
        files=len(files),
        bytes=sum(store.size_of(path) for path in files),
    )


def _flattened(image: Image.Image) -> Image.Image:
    """La imagen en RGB; la transparencia se aplana sobre `BACKGROUND_COLOR`."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new(RGB_MODE, rgba.size, BACKGROUND_COLOR)
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert(RGB_MODE)
