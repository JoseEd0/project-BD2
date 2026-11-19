"""La configuración de la tienda, del entorno con el prefijo `SHOP_` (ver `appkit.settings`).

`ShopSettings` es la del API; `SeedSettings` le suma lo que necesita la carga: dónde está el
dataset, cómo lo ve el gestor, dónde guardar los embeddings y cuánto esperar.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePath

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from appkit import ServiceSettings

ENV_PREFIX = "SHOP_"
DATASET_DIRECTORY = "amazon_musical_instruments"
EMBEDDINGS_DIRECTORY = "embeddings"
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 100
# Unas 190 fotos por segundo en un Apple M4 Pro con 32, 16 o 64 por lote dentro de un 8 %
# (ver README): 32 deja el lote en unos 20 MB de píxeles.
DEFAULT_CLIP_BATCH_SIZE = 32
DEFAULT_PREPROCESS_WORKERS = os.cpu_count() or 1
DEFAULT_CLIP_THREADS = 0
DEFAULT_PROGRESS_ROWS = 50_000
# El CREATE INDEX de SIFT extrae, entrena el vocabulario y construye el HNSW de 24 580 fotos
# en una sola petición, y la carga de una tabla responde al terminar sus índices: una hora
# de plazo cubre los dos con margen.
DEFAULT_JOB_TIMEOUT_SECONDS = 3600.0


class ShopSettings(ServiceSettings):
    """Lo que necesita el API de la tienda.

    Attributes:
        models_dir: carpeta de los modelos (`python -m shop.clip models`).
        clip_threads: hilos de onnxruntime por sesión; 0 deja que elija.
        search_limit: resultados de una búsqueda si no se pide otro número.
        max_search_limit: resultados que se pueden pedir como mucho.
    """

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True, extra="ignore")

    models_dir: Path
    clip_threads: int = Field(default=DEFAULT_CLIP_THREADS, ge=0)
    search_limit: int = Field(default=DEFAULT_SEARCH_LIMIT, ge=1)
    max_search_limit: int = Field(default=MAX_SEARCH_LIMIT, ge=1)


class SeedSettings(ShopSettings):
    """Lo que necesita `python -m shop.seed`.

    Attributes:
        data_dir: carpeta de datasets; el de la tienda está en
            `<data_dir>/amazon_musical_instruments/` (la de `python -m shop.dataset`).
        engine_data_dir: la misma carpeta vista desde el gestor, si corre en otro
            contenedor; la columna `imagen` guarda esa ruta. Por omisión, `data_dir`
            absoluta. El gestor tiene que poder leerla: va en su `MINIGESTOR_IMPORT_DIRS`.
        embeddings_dir: caché de los embeddings de CLIP; por omisión,
            `<dataset>/embeddings/`.
        clip_batch_size: fotos por pasada de la torre de imagen.
        preprocess_workers: hilos que decodifican y preparan las fotos.
        progress_rows: cada cuántas filas o fotos se informa el avance.
        job_timeout_seconds: plazo de cada petición de la carga, que incluye construir
            índices.
    """

    data_dir: Path
    engine_data_dir: PurePath | None = None
    embeddings_dir: Path | None = None
    clip_batch_size: int = Field(default=DEFAULT_CLIP_BATCH_SIZE, ge=1)
    preprocess_workers: int = Field(default=DEFAULT_PREPROCESS_WORKERS, ge=1)
    progress_rows: int = Field(default=DEFAULT_PROGRESS_ROWS, ge=1)
    job_timeout_seconds: float = Field(default=DEFAULT_JOB_TIMEOUT_SECONDS, gt=0)

    @property
    def dataset(self) -> Path:
        return self.data_dir / DATASET_DIRECTORY

    @property
    def engine_dataset(self) -> PurePath:
        root = self.engine_data_dir or self.data_dir.resolve()
        return root / DATASET_DIRECTORY

    @property
    def embeddings(self) -> Path:
        return self.embeddings_dir or self.dataset / EMBEDDINGS_DIRECTORY
