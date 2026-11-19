"""La tienda contra el gestor real en el mismo proceso, con un dataset diminuto.

El gestor lee las fotos de una carpeta de importación aparte de su directorio de datos,
como en Docker; la torre de imagen de CLIP es una de mentira (los tests de `apps/shop`
prueban la de verdad), para que estos tests no necesiten los 577 MiB del modelo.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path

import httpx
import numpy as np
import pytest
from apps.shop.tests.tiny_dataset import write_tiny_dataset
from fastapi import FastAPI

from api.main import create_app
from config import EngineConfig
from minigestor_client import AsyncClient
from shop.clip import EMBEDDING_DIMENSION, PixelEncoder
from shop.clip.preprocessing import PixelValues
from shop.seed.job import EncoderLoader
from shop.settings import DATASET_DIRECTORY, SeedSettings

ENGINE_URL = "http://gestor"
ENGINE_KEY = "clave-de-la-tienda"
PAGE_SIZE = 4096
# Dos filas por lote: el dataset diminuto ya cruza lotes, y una carga a medias se puede
# dejar con una cantidad exacta de filas confirmadas.
BATCH_ROWS = 2
# Las cuatro fotos distintas del dataset diminuto dan unos 700 descriptores SIFT. Con 32
# palabras cada foto usa solo algunas, así que el idf no las anula y k-means entrena rápido.
VOCABULARY_SIZE = 32


class FakeImageEncoder:
    """El vector de una foto son sus primeros píxeles, con norma 1: la misma foto da el mismo
    vector y dos fotos de ruido distintas, vectores distintos. Cuenta cuántas fotos vio."""

    def __init__(self) -> None:
        self.embedded = 0

    def embed_pixels(self, batch: PixelValues) -> np.ndarray:
        self.embedded += len(batch)
        vectors = batch.reshape(len(batch), -1)[:, :EMBEDDING_DIMENSION]
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


@pytest.fixture
def datasets(tmp_path: Path) -> Path:
    write_tiny_dataset(tmp_path / "datasets" / DATASET_DIRECTORY)
    return tmp_path / "datasets"


@pytest.fixture
def engine_config(config: EngineConfig, tmp_path: Path, datasets: Path) -> EngineConfig:
    data = tmp_path / "gestor"
    data.mkdir()
    return replace(
        config,
        page_size=PAGE_SIZE,
        data_directory=data,
        import_directories=(datasets,),
        ingest_batch_rows=BATCH_ROWS,
        vocabulary_size=VOCABULARY_SIZE,
        feature_workers=1,
    )


@pytest.fixture
def engine_app(engine_config: EngineConfig) -> Iterator[FastAPI]:
    app = create_app(engine_config)
    yield app
    app.state.service.close()


@pytest.fixture
def seed_settings(tmp_path: Path, datasets: Path) -> SeedSettings:
    return SeedSettings(
        engine_url=ENGINE_URL,  # type: ignore[arg-type]
        engine_key=ENGINE_KEY,  # type: ignore[arg-type]
        models_dir=tmp_path / "modelos",
        data_dir=datasets,
        embeddings_dir=tmp_path / "embeddings",
        clip_batch_size=2,
        preprocess_workers=1,
        progress_rows=2,
    )


@pytest.fixture
def image_encoder() -> FakeImageEncoder:
    return FakeImageEncoder()


@pytest.fixture
def load_encoder(image_encoder: FakeImageEncoder) -> EncoderLoader:
    def load(_: SeedSettings) -> PixelEncoder:
        return image_encoder

    return load


@pytest.fixture
async def engine(engine_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = httpx.ASGITransport(app=engine_app)
    async with AsyncClient(ENGINE_URL, ENGINE_KEY, transport=transport) as client:
        yield client
