"""Los rastros de cada sesión de reconocimiento en vivo, en Redis.

Una clave por sesión (`faces:rastros:<sesión>`) con los rastros en JSON y un TTL que se
renueva en cada cuadro: al cortarse la conexión, los rastros caducan solos. Como viven en
Redis y no en la memoria de la réplica, una sesión que se reconecta por el nginx a otra
réplica sigue con sus rastros, sin conexiones pegajosas.

Se leen y se guardan en cada cuadro, no solo al conectarse: Redis es la única copia, así
que todas las réplicas son iguales y sin Redis no hay caché ninguna (una copia en memoria
sería una caché que no se ve ni se vacía). Cuesta dos viajes a Redis por cuadro, que se
miden en la etapa `track`.

Lo guardado no es biométrico: cajas, el nombre que se muestra, un coseno y tiempos; nunca un
embedding ni el código de la persona.
"""

from __future__ import annotations

import json
from typing import Any

from redis.exceptions import RedisError

from appkit import OptionalRedis
from faces.detection import BoundingBox
from faces.tracking import Identity, Track, TrackBook

KEY_PREFIX = "faces:rastros:"


class TrackStore:
    """Lee y guarda el `TrackBook` de una sesión; sin Redis, no hay rastros."""

    def __init__(self, cache: OptionalRedis, ttl_seconds: int) -> None:
        self._cache = cache
        self._ttl_seconds = ttl_seconds

    async def load(self, session: str) -> TrackBook | None:
        """Los rastros de `session` (vacíos si es nueva o caducó), o `None` si no hay
        caché ahora."""
        client = self._cache.client()
        if client is None:
            return None
        try:
            stored = await client.get(_key(session))
        except RedisError as error:
            self._cache.failed(error)
            return None
        self._cache.succeeded()
        return TrackBook() if stored is None else book_from_json(stored)

    async def save(self, session: str, book: TrackBook) -> None:
        """Guarda los rastros de `session` y renueva su TTL; sin caché, no hace nada."""
        client = self._cache.client()
        if client is None:
            return
        try:
            await client.set(_key(session), book_to_json(book), ex=self._ttl_seconds)
        except RedisError as error:
            self._cache.failed(error)


def book_to_json(book: TrackBook) -> str:
    return json.dumps(
        {"next_id": book.next_id, "tracks": [_track_fields(track) for track in book.tracks]},
        ensure_ascii=False,
    )


def book_from_json(stored: bytes | str) -> TrackBook:
    """Los rastros guardados; si no se entienden (los dejó otra versión), ninguno: perder
    los rastros solo cuesta volver a buscar los rostros."""
    try:
        fields = json.loads(stored)
        tracks = tuple(_track(track) for track in fields["tracks"])
        return TrackBook(tracks, int(fields["next_id"]))
    except (ValueError, KeyError, TypeError):
        return TrackBook()


def _key(session: str) -> str:
    return KEY_PREFIX + session


def _track_fields(track: Track) -> dict[str, Any]:
    box = track.box
    identity = track.identity
    return {
        "id": track.track_id,
        "box": [box.x, box.y, box.width, box.height],
        "last_seen": track.last_seen,
        "identity": None
        if identity is None
        else {
            "name": identity.display_name,
            "score": identity.score,
            "verified_at": identity.verified_at,
        },
    }


def _track(fields: dict[str, Any]) -> Track:
    x, y, width, height = (float(value) for value in fields["box"])
    identity = fields["identity"]
    return Track(
        track_id=int(fields["id"]),
        box=BoundingBox(x, y, width, height),
        last_seen=float(fields["last_seen"]),
        identity=None
        if identity is None
        else Identity(
            display_name=identity["name"],
            score=identity["score"],
            verified_at=float(identity["verified_at"]),
        ),
    )
