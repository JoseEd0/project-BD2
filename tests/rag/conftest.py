"""El gestor real servido por HTTP en un puerto libre, el corpus diminuto y la configuración
del servicio que apunta a ese gestor, con una clave `write` para atender y otra `admin` para
migrar, como en producción."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from apps.rag.tests.rag_models_support import loaded_embedder
from fastapi import FastAPI
from tests.client.engine_server import serving
from tests.rag.rag_corpus import HashEmbedder, write_corpus

from api.auth import Role, ServiceKey, digest_of
from api.main import create_app
from config import EngineConfig
from rag.config import RagSettings
from rag.embeddings import E5Embedder
from rag.pipeline import CorpusPaths

WRITE_KEY = "clave-de-escritura-del-rag-0123456789"
ADMIN_KEY = "clave-de-administracion-del-rag-0123456789"
# Lotes de dos filas: el corpus de siete fragmentos ya se carga en varios lotes.
BATCH_ROWS = 2


@pytest.fixture
def engine_app(tmp_path: Path) -> Iterator[FastAPI]:
    keys = (
        ServiceKey("rag", Role.WRITE, digest_of(WRITE_KEY)),
        ServiceKey("rag-migraciones", Role.ADMIN, digest_of(ADMIN_KEY)),
    )
    config = EngineConfig(data_directory=tmp_path / "gestor", ingest_batch_rows=BATCH_ROWS)
    app = create_app(config, service_keys=keys)
    yield app
    app.state.service.close()


@pytest.fixture
def engine_url(engine_app: FastAPI) -> Iterator[str]:
    with serving(engine_app) as url:
        yield url


@pytest.fixture
def settings(engine_url: str, tmp_path: Path) -> RagSettings:
    return RagSettings(
        engine_url=engine_url,
        engine_key=WRITE_KEY,
        migration_key=ADMIN_KEY,
        models_dir=tmp_path / "modelos",
        ready_cache_seconds=0,
    )


@pytest.fixture
def corpus(tmp_path: Path) -> CorpusPaths:
    return write_corpus(tmp_path / "rag_papers")


@pytest.fixture
def hash_embedder() -> HashEmbedder:
    return HashEmbedder()


@pytest.fixture(scope="session")
def e5() -> E5Embedder:
    """El modelo real, en su variante por omisión; sin `RAG_MODELS_DIR`, el test se salta."""
    return loaded_embedder()
