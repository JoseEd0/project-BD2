"""Los derechos de cada persona sobre su registro (verlo y borrarlo) y el borrado de lo que
venció."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from faces.api.enrollments import person_view
from faces.api.errors import PERSON_NOT_FOUND, PersonNotFoundError
from faces.api.models import Person
from faces.api.store import FaceStore
from minigestor_client import MinigestorError, new_request_id, request_id_scope

Clock = Callable[[], float]
PURGE_REQUEST_PREFIX = "purga-"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PurgeReport:
    people: int
    enrollments: int


class PeopleService:
    def __init__(self, store: FaceStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def person(self, person_id: str) -> Person:
        """Lo que se guarda de la persona, salvo sus embeddings.

        Raises:
            PersonNotFoundError: si no existe o venció.
        """
        person = await self._store.person(person_id, int(self._clock()))
        if person is None:
            raise PersonNotFoundError(PERSON_NOT_FOUND)
        return person_view(person, await self._store.embedding_count(person_id))

    async def forget(self, person_id: str) -> None:
        """Borra la persona y todos sus embeddings; no pasa nada si ya no estaba."""
        await self._store.delete_person(person_id)

    async def purge_expired(self) -> PurgeReport:
        """Borra las personas y las sesiones de registro vencidas, con sus embeddings y tomas.

        Varias réplicas pueden correrlo a la vez: cada borrado es idempotente."""
        now = int(self._clock())
        people = await self._store.expired_people(now)
        for person_id in people:
            await self._store.delete_person(person_id)
        enrollments = await self._store.expired_enrollments(now)
        for enrollment_id in enrollments:
            await self._store.discard_enrollment(enrollment_id)
        return PurgeReport(people=len(people), enrollments=len(enrollments))


async def purge_periodically(people: PeopleService, interval_seconds: float) -> None:
    """Borra lo vencido cada `interval_seconds` hasta que se cancele. Un fallo del gestor
    queda en el log y se reintenta en la vuelta siguiente."""
    while True:
        with request_id_scope(PURGE_REQUEST_PREFIX + new_request_id()):
            try:
                report = await people.purge_expired()
            except MinigestorError as error:
                logger.warning("no se pudo borrar lo vencido: %s", error)
            else:
                if report.people or report.enrollments:
                    logger.info(
                        "borrados %d registros y %d sesiones vencidos",
                        report.people,
                        report.enrollments,
                    )
        await asyncio.sleep(interval_seconds)
