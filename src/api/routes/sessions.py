"""Sesiones que el cliente mantiene entre peticiones para usar transacciones."""

from __future__ import annotations

from fastapi import APIRouter

from api.service import ServiceDependency

router = APIRouter()


@router.delete("/sessions/{identifier}")
def close_session(identifier: str, service: ServiceDependency) -> dict[str, bool]:
    return {"closed": service.close_session(identifier)}
