"""Un servicio mínimo hecho con el kit. Existe para los tests (los de `apps/kit/tests` y el de
punta a punta contra el gestor real en `tests/kit/`) y para probar la topología de Docker
mientras ninguna aplicación tiene su API: el perfil `example` lo corre con la imagen y el
compose de las apps (`deploy/example/compose.yaml`), con `create_app` como fábrica y
`python -m kit_example` como carga inicial."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict

from appkit import (
    Engine,
    ResourceNotFoundError,
    ServiceSettings,
    configure_logging,
    create_service_app,
    engine_client,
    load_settings,
    timed,
)
from minigestor_client import Record

NAME = "ejemplo"
VERSION = "0.0.1"
ENV_PREFIX = "EJEMPLO_"
NOTE_LENGTH = 80
SCHEMA = (
    f"CREATE TABLE IF NOT EXISTS ej_notas (id INT PRIMARY KEY, texto VARCHAR({NOTE_LENGTH}))",
    "CREATE INDEX IF NOT EXISTS ej_notas_texto ON ej_notas USING HASH (texto)",
)
READ_NOTE = "SELECT id, texto FROM ej_notas WHERE id = $1"
WRITE_NOTE = "INSERT INTO ej_notas VALUES ($1, $2)"
BROKEN_SQL = "SELEC id FROM ej_notas"
NOTES_STAGE = "notas"
SEED_NOTE_ID = 1
SEED_NOTE_TEXT = "la nota de la carga inicial"


class ExampleSettings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True, extra="ignore")


class Note(BaseModel):
    id: int
    texto: str = Field(max_length=NOTE_LENGTH)


router = APIRouter()


@router.get("/notas/{note_id}")
async def read_note(note_id: int, engine: Engine) -> Record:
    with timed(NOTES_STAGE):
        note = await engine.fetch_one(READ_NOTE, [note_id])
    if note is None:
        raise ResourceNotFoundError(f"no hay una nota {note_id}")
    return note


@router.post("/notas", status_code=201)
async def write_note(note: Note, engine: Engine) -> Note:
    await engine.execute(WRITE_NOTE, [note.id, note.texto])
    return note


@router.get("/sql-roto")
async def broken_query(engine: Engine) -> Record | None:
    return await engine.fetch_one(BROKEN_SQL)


@router.get("/fallo")
async def unexpected_failure() -> None:
    raise RuntimeError("un bug del servicio")


def create_example_app(
    settings: ExampleSettings, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    app = create_service_app(
        settings, name=NAME, version=VERSION, migrations=SCHEMA, engine_transport=transport
    )
    app.include_router(router)
    return app


def create_app() -> FastAPI:
    """La aplicación configurada con el entorno, como la arranca la imagen de los servicios."""
    settings = load_settings(ExampleSettings)
    configure_logging(settings.log_level)
    return create_example_app(settings)


async def seed(
    settings: ExampleSettings, transport: httpx.AsyncBaseTransport | None = None
) -> bool:
    """Deja la nota de la carga inicial si falta; devuelve si la escribió. Repetirla no
    duplica nada, como todo trabajo del perfil `seed`."""
    async with engine_client(settings, settings.engine_key, transport) as engine:
        if await engine.fetch_one(READ_NOTE, [SEED_NOTE_ID]) is not None:
            return False
        await engine.execute(WRITE_NOTE, [SEED_NOTE_ID, SEED_NOTE_TEXT])
        return True


if __name__ == "__main__":
    asyncio.run(seed(load_settings(ExampleSettings)))
