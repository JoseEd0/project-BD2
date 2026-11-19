"""La ruta del reconocimiento en vivo."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from faces.api.realtime.connection import serve_recognition
from faces.api.routes import services_of

RECOGNIZE_PATH = "/ws/recognize"

router = APIRouter()


@router.websocket(RECOGNIZE_PATH)
async def recognize(websocket: WebSocket) -> None:
    """Cuadros JPEG con su número en mensajes binarios; rostros, rastros y tiempos en JSON.
    El protocolo está en `faces.api.realtime.protocol`."""
    await serve_recognition(websocket, services_of(websocket).stream)
