"""El `X-Request-ID` de la petición en curso, para que cada llamada al gestor lleve el mismo.

El servicio que atiende una petición deja su id en una variable de contexto (lo hace el
middleware de `appkit`); el cliente lo lee al llamar al gestor, así una misma petición se
sigue en los logs de la app y en los del gestor. Fuera de una petición, cada llamada genera
el suyo.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str | None] = ContextVar("minigestor_request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


def current_request_id() -> str | None:
    """El id que fijó quien atiende la petición en curso; `None` fuera de una."""
    return _request_id.get()


@contextmanager
def request_id_scope(request_id: str) -> Iterator[str]:
    """Las llamadas al gestor dentro del bloque llevan `request_id`."""
    token = _request_id.set(request_id)
    try:
        yield request_id
    finally:
        _request_id.reset(token)
