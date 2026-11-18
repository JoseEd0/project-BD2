"""Peticiones HTTP en serie y con pausa entre una y otra, como piden los términos de arXiv.

Un `PoliteClient` hace una petición a la vez y deja pasar al menos `delay_seconds` desde
que terminó la anterior. Un 429 o un 503 es el control de flujo del servidor: se espera lo
que diga `Retry-After` y se reintenta. Un corte de la red también se reintenta; un 404 no.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http import HTTPStatus
from typing import Protocol

from appkit.download import TRANSIENT_ERRORS, DownloadError

from .settings import HttpSettings

logger = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.SERVICE_UNAVAILABLE})


class Response(Protocol):
    """Lo que se usa de la respuesta de `urlopen`."""

    def read(self) -> bytes: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *details: object) -> None: ...


Opener = Callable[[urllib.request.Request, float], Response]


def open_url(request: urllib.request.Request, timeout: float) -> Response:
    """`urllib.request.urlopen` con la firma de `Opener`."""
    response: Response = urllib.request.urlopen(request, timeout=timeout)
    return response


class PoliteClient:
    """Cliente HTTP de una sola conexión con pausa mínima entre peticiones.

    `clock`, `sleep` y `opener` se inyectan para probar las pausas y los reintentos sin red.
    """

    def __init__(
        self,
        user_agent: str,
        delay_seconds: float,
        settings: HttpSettings,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        opener: Opener = open_url,
    ) -> None:
        self._user_agent = user_agent
        self._delay_seconds = delay_seconds
        self._settings = settings
        self._clock = clock
        self._sleep = sleep
        self._opener = opener
        self._last_request_end: float | None = None

    def get(self, url: str) -> bytes:
        """El cuerpo de la respuesta a `GET url`.

        Raises:
            DownloadError: si el servidor responde un error que no es de control de flujo, o
                si después de `attempts` intentos la petición sigue fallando.
        """
        failure = "sin intentos"
        for attempt in range(1, self._settings.attempts + 1):
            self._wait_turn()
            try:
                return self._fetch(url)
            except urllib.error.HTTPError as error:
                if error.code not in RETRY_STATUSES:
                    raise DownloadError(f"{url}: HTTP {error.code}") from error
                failure = f"HTTP {error.code}"
                self._sleep(self._retry_after(error))
            except TRANSIENT_ERRORS as error:
                failure = f"{type(error).__name__}: {error}"
                self._sleep(self._delay_seconds * 2**attempt)
            logger.info("reintento %d de %s (%s)", attempt, url, failure)
        raise DownloadError(f"{url}: {failure} después de {self._settings.attempts} intentos")

    def _fetch(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": self._user_agent})
        try:
            with self._opener(request, self._settings.timeout_seconds) as response:
                return response.read()
        finally:
            self._last_request_end = self._clock()

    def _wait_turn(self) -> None:
        if self._last_request_end is None:
            return
        elapsed = self._clock() - self._last_request_end
        if elapsed < self._delay_seconds:
            self._sleep(self._delay_seconds - elapsed)

    def _retry_after(self, error: urllib.error.HTTPError) -> float:
        header = error.headers.get("Retry-After") if error.headers else None
        try:
            seconds = float(header) if header else self._settings.default_retry_after_seconds
        except ValueError:
            seconds = self._settings.default_retry_after_seconds
        return min(max(seconds, self._delay_seconds), self._settings.max_retry_after_seconds)
