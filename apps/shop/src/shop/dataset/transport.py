"""Las peticiones HTTP de las fotos: sin fijar de antemano, muchas y con reintentos.

Los archivos publicados del dataset, con su SHA-256 fijado, se bajan con
`appkit.download.fetch_verified`; aquí queda solo lo propio de las fotos, que son decenas
de miles de URL sin suma conocida.
"""

from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from http import HTTPStatus
from typing import Protocol

from shop.dataset.errors import HttpStatusError

USER_AGENT = "minigestor-bd2-shop (proyecto universitario)"
TIMEOUT_SECONDS = 60
TOO_MANY_REQUESTS = HTTPStatus.TOO_MANY_REQUESTS
TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    urllib.error.URLError,
    http.client.HTTPException,
    ConnectionError,
    TimeoutError,
)
RETRY_CANDIDATES: tuple[type[Exception], ...] = (HttpStatusError, *TRANSIENT_ERRORS)


class Body(Protocol):
    """El cuerpo de una respuesta."""

    def read(self, size: int = -1, /) -> bytes: ...


class Transport(Protocol):
    """Quien hace las peticiones: la red de verdad o, en los tests, bytes en memoria.

    `open` lanza `HttpStatusError` si el estado no es 200.
    """

    def open(self, url: str) -> AbstractContextManager[Body]: ...


class UrllibTransport:
    """Peticiones con `urllib`, que sigue las redirecciones."""

    def open(self, url: str) -> AbstractContextManager[Body]:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            response = urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as error:
            error.close()
            raise HttpStatusError(url, error.code) from error
        body: AbstractContextManager[Body] = response
        return body


def is_retryable(error: BaseException) -> bool:
    """Si vale la pena repetir la petición: cortes de red, 429 y errores 5xx."""
    if isinstance(error, HttpStatusError):
        return error.status == TOO_MANY_REQUESTS or error.status >= HTTPStatus.INTERNAL_SERVER_ERROR
    return isinstance(error, TRANSIENT_ERRORS)


def fetch_bytes(
    url: str,
    transport: Transport,
    attempts: int,
    backoff_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """El cuerpo entero de `url`. Lo que `is_retryable` acepta se reintenta hasta
    `attempts` veces en total, esperando `backoff_seconds` y el doble cada vez.

    Raises:
        HttpStatusError: el estado del último intento, si no fue 200.
        OSError: el último corte de red, si todos los intentos fallaron.
    """
    for attempt in range(1, attempts):
        try:
            return _read(url, transport)
        except RETRY_CANDIDATES as error:
            if not is_retryable(error):
                raise
            sleep(backoff_seconds * 2 ** (attempt - 1))
    return _read(url, transport)


def _read(url: str, transport: Transport) -> bytes:
    with transport.open(url) as body:
        return body.read()
