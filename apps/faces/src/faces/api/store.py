"""Las tablas del servicio en el gestor y las consultas que las leen y escriben.

Todo el estado vive en el gestor —también las sesiones de registro a medias—, así que
cualquier réplica del API atiende cualquier petición. Cada consulta va con parámetros `$n`.

| Tabla | Una fila por | Índices |
|---|---|---|
| `face_personas` | persona registrada | clave primaria |
| `face_embeddings` | toma aceptada de una persona | `persona` (hash) y `embedding` (HNSW coseno) |
| `face_sesiones` | registro a medias | clave primaria |
| `face_tomas` | toma aceptada de un registro a medias | `sesion` (hash) |
| `face_famosos` | persona pública de LFW (`faces.famous`) | `embedding` (HNSW coseno) |
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import numpy as np

from faces.enrollment import DISPLAY_NAME_MAX_LENGTH, Neighbour
from faces.famous import FAMOUS_SCHEMA
from faces.pose import HeadPose, Pose
from faces.quality import ShotMeasurements
from faces.recognition import EMBEDDING_DIMENSION, Embedding
from faces.shots import AcceptedShot
from minigestor_client import AsyncClient, AsyncTransaction, Record

# Un uuid4 en hexadecimal; el de una toma le suma `-<número>`.
ID_LENGTH = 32
SHOT_ID_LENGTH = 40
POSE_LENGTH = 8
CONSENT_VERSION_LENGTH = 24
MEASUREMENT_COLUMNS = (
    "ancho_rostro DOUBLE NOT NULL, fuera DOUBLE NOT NULL, brillo DOUBLE NOT NULL, "
    "sobreexpuesto DOUBLE NOT NULL, nitidez DOUBLE NOT NULL, yaw DOUBLE NOT NULL, "
    "pitch DOUBLE NOT NULL, roll DOUBLE NOT NULL"
)
SHOT_COLUMNS = (
    f"toma INT NOT NULL, pose VARCHAR({POSE_LENGTH}) NOT NULL, "
    f"embedding VECTOR({EMBEDDING_DIMENSION}) NOT NULL, {MEASUREMENT_COLUMNS}"
)
REGISTRATION_COLUMNS = (
    f"nombre VARCHAR({DISPLAY_NAME_MAX_LENGTH}) NOT NULL, "
    f"version_consentimiento VARCHAR({CONSENT_VERSION_LENGTH}) NOT NULL, "
    "consentido_en BIGINT NOT NULL, creado_en BIGINT NOT NULL, expira_en BIGINT NOT NULL"
)
SCHEMA = (
    f"CREATE TABLE IF NOT EXISTS face_personas (id VARCHAR({ID_LENGTH}) PRIMARY KEY, "
    f"{REGISTRATION_COLUMNS})",
    f"CREATE TABLE IF NOT EXISTS face_embeddings (id VARCHAR({SHOT_ID_LENGTH}) PRIMARY KEY, "
    f"persona VARCHAR({ID_LENGTH}) NOT NULL, {SHOT_COLUMNS})",
    "CREATE INDEX IF NOT EXISTS face_embeddings_persona ON face_embeddings USING HASH (persona)",
    "CREATE INDEX IF NOT EXISTS face_embeddings_vector ON face_embeddings "
    "USING HNSW (embedding) WITH (metric = 'cosine')",
    f"CREATE TABLE IF NOT EXISTS face_sesiones (id VARCHAR({ID_LENGTH}) PRIMARY KEY, "
    f"{REGISTRATION_COLUMNS}, intentos INT NOT NULL)",
    f"CREATE TABLE IF NOT EXISTS face_tomas (id VARCHAR({SHOT_ID_LENGTH}) PRIMARY KEY, "
    f"sesion VARCHAR({ID_LENGTH}) NOT NULL, {SHOT_COLUMNS})",
    "CREATE INDEX IF NOT EXISTS face_tomas_sesion ON face_tomas USING HASH (sesion)",
    *FAMOUS_SCHEMA,
)
SHOT_VALUES = 13
INSERT_ENROLLMENT = "INSERT INTO face_sesiones VALUES ($1, $2, $3, $4, $5, $6, $7)"
SELECT_ENROLLMENT = "SELECT * FROM face_sesiones WHERE id = $1 AND expira_en > $2"
COUNT_ATTEMPT = "UPDATE face_sesiones SET intentos = intentos + 1 WHERE id = $1"
SELECT_PENDING_SHOTS = "SELECT * FROM face_tomas WHERE sesion = $1"
DELETE_ENROLLMENT = "DELETE FROM face_sesiones WHERE id = $1"
DELETE_PENDING_SHOTS = "DELETE FROM face_tomas WHERE sesion = $1"
INSERT_PERSON = "INSERT INTO face_personas VALUES ($1, $2, $3, $4, $5, $6)"
SELECT_PERSON = "SELECT * FROM face_personas WHERE id = $1 AND expira_en > $2"
SELECT_PERSON_NAME = "SELECT nombre FROM face_personas WHERE id = $1 AND expira_en > $2"
COUNT_EMBEDDINGS = "SELECT COUNT(*) FROM face_embeddings WHERE persona = $1"
DELETE_EMBEDDINGS = "DELETE FROM face_embeddings WHERE persona = $1"
DELETE_PERSON = "DELETE FROM face_personas WHERE id = $1"
NEAREST_EMBEDDINGS = (
    "SELECT persona, SIMILARITY_SCORE() AS score FROM face_embeddings "
    "WHERE SIMILAR_TO(embedding, $1, k = $2)"
)
EXPIRED_PEOPLE = "SELECT id FROM face_personas WHERE expira_en <= $1"
EXPIRED_ENROLLMENTS = "SELECT id FROM face_sesiones WHERE expira_en <= $1"


class EnrollmentGoneError(Exception):
    """La sesión de registro dejó de existir mientras se terminaba: otra petición la terminó
    o la canceló, o venció."""


@dataclass(frozen=True, slots=True)
class Registration:
    """Lo común de una sesión de registro y de una persona registrada (tiempos en segundos
    desde la época, UTC)."""

    id: str
    display_name: str
    consent_version: str
    consented_at: int
    created_at: int
    expires_at: int

    def values(self) -> list[Any]:
        return [
            self.id,
            self.display_name,
            self.consent_version,
            self.consented_at,
            self.created_at,
            self.expires_at,
        ]


@dataclass(frozen=True, slots=True)
class PendingEnrollment:
    registration: Registration
    attempts: int


@dataclass(frozen=True, slots=True)
class StoredShot:
    """Una toma guardada: su número dentro del registro, su pose, su embedding y medidas."""

    index: int
    pose: Pose
    embedding: Embedding
    measurements: ShotMeasurements


class GalleryQueries:
    """Las lecturas de la galería: con el cliente, o dentro de la transacción que termina un
    registro."""

    def __init__(self, engine: AsyncClient | AsyncTransaction) -> None:
        self._engine = engine

    async def nearest(self, embedding: Embedding, neighbours: int) -> list[Neighbour]:
        """Los `neighbours` embeddings de la galería más parecidos, por el HNSW."""
        rows = await self._engine.fetch_all(NEAREST_EMBEDDINGS, [embedding, neighbours])
        return [Neighbour(str(row["persona"]), float(row["score"])) for row in rows]

    async def person_name(self, person_id: str, now: int) -> str | None:
        name = await self._engine.scalar(SELECT_PERSON_NAME, [person_id, now])
        return None if name is None else str(name)


class EnrollmentClaim:
    """Una sesión de registro ya consumida, con su transacción abierta: lo que lee `gallery`
    y lo que escribe `create_person` se confirman juntos al salir del bloque."""

    def __init__(self, transaction: AsyncTransaction) -> None:
        self._transaction = transaction
        self.gallery = GalleryQueries(transaction)

    async def create_person(self, person: Registration, shots: Sequence[StoredShot]) -> None:
        """Crea la persona con un embedding por toma."""
        await self._transaction.execute(INSERT_PERSON, person.values())
        await _insert_embeddings(self._transaction, person.id, shots)


class FaceStore:
    """Las consultas del servicio sobre el gestor."""

    def __init__(self, engine: AsyncClient) -> None:
        self._engine = engine
        self.gallery = GalleryQueries(engine)

    async def create_enrollment(self, registration: Registration) -> None:
        await self._engine.execute(INSERT_ENROLLMENT, [*registration.values(), 0])

    async def enrollment(self, enrollment_id: str, now: int) -> PendingEnrollment | None:
        """La sesión `enrollment_id` si existe y no venció."""
        row = await self._engine.fetch_one(SELECT_ENROLLMENT, [enrollment_id, now])
        if row is None:
            return None
        return PendingEnrollment(_registration(row), int(row["intentos"]))

    async def count_attempt(self, enrollment_id: str) -> None:
        await self._engine.execute(COUNT_ATTEMPT, [enrollment_id])

    async def pending_shots(self, enrollment_id: str) -> list[StoredShot]:
        """Las tomas aceptadas de la sesión, en orden."""
        rows = await self._engine.fetch_all(SELECT_PENDING_SHOTS, [enrollment_id])
        return sorted((_stored_shot(row) for row in rows), key=lambda shot: shot.index)

    async def add_pending_shot(
        self, enrollment_id: str, index: int, pose: Pose, shot: AcceptedShot
    ) -> None:
        """Guarda la toma número `index` de la sesión.

        Raises:
            minigestor_client.ConflictError: si ya hay una toma con ese número, porque otra
                petición de la misma sesión la guardó antes.
        """
        values = _shot_values(f"{enrollment_id}-{index}", enrollment_id, index, pose, shot)
        await self._engine.execute(f"INSERT INTO face_tomas VALUES {_placeholders(1)}", values)

    async def discard_enrollment(self, enrollment_id: str) -> None:
        """Borra la sesión y sus tomas; no hace nada si ya no estaba."""
        async with self._engine.transaction() as transaction:
            await _consume_enrollment(transaction, enrollment_id)

    @asynccontextmanager
    async def claim_enrollment(self, enrollment_id: str) -> AsyncIterator[EnrollmentClaim]:
        """Abre una transacción, consume en ella la sesión y sus tomas, y la confirma al salir
        del bloque; si el bloque falla, la deshace y la sesión sigue ahí.

        El `DELETE` de la sesión bloquea `face_sesiones` en exclusiva hasta el final: dos
        registros que terminan a la vez se turnan aquí, y el segundo busca duplicados con el
        primero ya confirmado.

        Raises:
            EnrollmentGoneError: si la sesión ya no estaba; no se crea nadie.
        """
        async with self._engine.transaction() as transaction:
            if not await _consume_enrollment(transaction, enrollment_id):
                raise EnrollmentGoneError(enrollment_id)
            yield EnrollmentClaim(transaction)

    async def person(self, person_id: str, now: int) -> Registration | None:
        row = await self._engine.fetch_one(SELECT_PERSON, [person_id, now])
        return None if row is None else _registration(row)

    async def embedding_count(self, person_id: str) -> int:
        return int(await self._engine.scalar(COUNT_EMBEDDINGS, [person_id]) or 0)

    async def delete_person(self, person_id: str) -> None:
        """Borra la persona y sus embeddings en una transacción; no hace nada si no estaba."""
        async with self._engine.transaction() as transaction:
            await transaction.execute(DELETE_EMBEDDINGS, [person_id])
            await transaction.execute(DELETE_PERSON, [person_id])

    async def expired_people(self, now: int) -> list[str]:
        rows = await self._engine.fetch_all(EXPIRED_PEOPLE, [now])
        return [str(row["id"]) for row in rows]

    async def expired_enrollments(self, now: int) -> list[str]:
        rows = await self._engine.fetch_all(EXPIRED_ENROLLMENTS, [now])
        return [str(row["id"]) for row in rows]


async def _consume_enrollment(transaction: AsyncTransaction, enrollment_id: str) -> bool:
    """Borra la sesión y sus tomas, en ese orden en toda transacción para no cruzar los
    bloqueos de las dos tablas. Dice si la sesión estaba."""
    existed = await transaction.execute(DELETE_ENROLLMENT, [enrollment_id]) > 0
    await transaction.execute(DELETE_PENDING_SHOTS, [enrollment_id])
    return existed


async def _insert_embeddings(
    transaction: AsyncTransaction, person_id: str, shots: Sequence[StoredShot]
) -> None:
    rows = [
        _shot_values(f"{person_id}-{shot.index}", person_id, shot.index, shot.pose, shot)
        for shot in shots
    ]
    values = [value for row in rows for value in row]
    await transaction.execute(
        f"INSERT INTO face_embeddings VALUES {_placeholders(len(rows))}", values
    )


def _placeholders(rows: int) -> str:
    """`($1, …, $13), ($14, …)`: los parámetros de `rows` filas de una toma."""
    return ", ".join(
        "("
        + ", ".join(f"${row * SHOT_VALUES + column}" for column in range(1, SHOT_VALUES + 1))
        + ")"
        for row in range(rows)
    )


def _shot_values(
    shot_id: str, owner: str, index: int, pose: Pose, shot: AcceptedShot | StoredShot
) -> list[Any]:
    measured = shot.measurements
    return [
        shot_id,
        owner,
        index,
        pose.value,
        np.asarray(shot.embedding, dtype=np.float32),
        measured.face_width,
        measured.outside_fraction,
        measured.brightness,
        measured.overexposed_fraction,
        measured.sharpness,
        measured.head.yaw,
        measured.head.pitch,
        measured.head.roll,
    ]


def _registration(row: Record) -> Registration:
    return Registration(
        id=str(row["id"]),
        display_name=str(row["nombre"]),
        consent_version=str(row["version_consentimiento"]),
        consented_at=int(row["consentido_en"]),
        created_at=int(row["creado_en"]),
        expires_at=int(row["expira_en"]),
    )


def _stored_shot(row: Record) -> StoredShot:
    return StoredShot(
        index=int(row["toma"]),
        pose=Pose(row["pose"]),
        embedding=np.asarray(row["embedding"], dtype=np.float32),
        measurements=ShotMeasurements(
            face_width=float(row["ancho_rostro"]),
            outside_fraction=float(row["fuera"]),
            brightness=float(row["brillo"]),
            overexposed_fraction=float(row["sobreexpuesto"]),
            sharpness=float(row["nitidez"]),
            head=HeadPose(
                yaw=float(row["yaw"]), pitch=float(row["pitch"]), roll=float(row["roll"])
            ),
        ),
    )
