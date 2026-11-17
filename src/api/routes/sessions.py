"""Sesiones que el cliente mantiene entre peticiones para usar transacciones."""

from __future__ import annotations

from fastapi import APIRouter

from api.auth import CallerDependency
from api.service import ServiceDependency

router = APIRouter()


@router.post("/sessions/{identifier}/close")
def close_session(
    identifier: str, service: ServiceDependency, caller: CallerDependency
) -> dict[str, bool]:
    """Cierra la sesión de quien llama y aborta lo que tuviera abierto; la de otro servicio
    con el mismo identificador no se toca.

    Es `POST` y no `DELETE` porque la llama `navigator.sendBeacon` al cerrar la pestaña: es
    la única petición que el navegador garantiza enviar mientras descarga la página, y
    solo sabe hacer `POST`.
    """
    return {"closed": service.close_session(caller.service, identifier)}
