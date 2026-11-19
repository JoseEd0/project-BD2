"""La configuración de la tienda, del entorno con el prefijo `SHOP_` (ver `appkit.settings`).

`ShopSettings` es la del API, con la del recomendador y su caché; `SeedSettings` le suma lo
que necesita la carga: dónde está el dataset, cómo lo ve el gestor, dónde guardar los
embeddings, cuánto esperar y cómo materializar las compras conjuntas.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePath

from pydantic import Field, RedisDsn
from pydantic_settings import SettingsConfigDict

from appkit import ServiceSettings
from shop.recommend.candidates import CLIP_METHOD, VisualMethod
from shop.recommend.fusion import FusionWeights

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
# Solo las compras de entrenamiento alimentan las recomendaciones: la validación y la prueba
# quedan para medirlas (#82) sin que el recomendador las haya visto.
DEFAULT_RECOMMENDATION_SPLIT = "train"
DEFAULT_CANDIDATES_PER_SIGNAL = 50
DEFAULT_NEARBY_RADIUS_METERS = 5000.0
# Lo que define a un usuario para las señales visual y de texto: sus últimas compras.
DEFAULT_USER_PROFILE_PRODUCTS = 10
# Pesos de partida de la fusión; #82 los ajusta en la validación.
DEFAULT_COPURCHASE_WEIGHT = 0.4
DEFAULT_VISUAL_WEIGHT = 0.25
DEFAULT_TEXT_WEIGHT = 0.25
DEFAULT_NEARBY_WEIGHT = 0.1
DEFAULT_PURCHASE_ATTEMPTS = 3
DEFAULT_CACHE_TTL_SECONDS = 600
# Una caché que tarda más que esto deja de ahorrar: se responde sin ella.
DEFAULT_REDIS_TIMEOUT_SECONDS = 0.25
DEFAULT_CACHE_RETRY_SECONDS = 5.0
# Un par comprado por un solo cliente es casi siempre casualidad; con 2, la tabla baja de
# 4,7 millones de pares a 406 668 (medido en el dataset).
DEFAULT_COPURCHASE_MIN_SUPPORT = 2
DEFAULT_COPURCHASE_CHUNK_PRODUCTS = 512


class ShopSettings(ServiceSettings):
    """Lo que necesita el API de la tienda.

    Attributes:
        models_dir: carpeta de los modelos (`python -m shop.clip models`).
        clip_threads: hilos de onnxruntime por sesión; 0 deja que elija.
        search_limit: resultados de una búsqueda o recomendación si no se pide otro número.
        max_search_limit: resultados que se pueden pedir como mucho.
        recommendation_split: la partición de las compras que alimenta las recomendaciones,
            y la de las compras nuevas.
        candidates_per_signal: candidatos que pide cada señal al gestor.
        nearby_radius_meters: radio por omisión de la señal de cercanía.
        user_profile_products: últimas compras de un usuario que lo describen.
        copurchase_weight, visual_weight, text_weight, nearby_weight: pesos de la fusión.
        visual_signal: el parecido visual por omisión: `clip` o `sift`.
        purchase_attempts: intentos de registrar una compra que choca con otra a la vez.
        redis_url: la caché de respuestas; sin ella, no hay caché.
        cache_ttl_seconds: cuánto vale una respuesta guardada.
        redis_timeout_seconds: plazo de conectar y de cada operación con Redis.
        cache_retry_seconds: tras un fallo de Redis, cuánto se responde sin probarlo.
    """

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True, extra="ignore")

    models_dir: Path
    clip_threads: int = Field(default=DEFAULT_CLIP_THREADS, ge=0)
    search_limit: int = Field(default=DEFAULT_SEARCH_LIMIT, ge=1)
    max_search_limit: int = Field(default=MAX_SEARCH_LIMIT, ge=1)
    recommendation_split: str = Field(default=DEFAULT_RECOMMENDATION_SPLIT, min_length=1)
    candidates_per_signal: int = Field(default=DEFAULT_CANDIDATES_PER_SIGNAL, ge=1)
    nearby_radius_meters: float = Field(default=DEFAULT_NEARBY_RADIUS_METERS, gt=0)
    user_profile_products: int = Field(default=DEFAULT_USER_PROFILE_PRODUCTS, ge=1)
    copurchase_weight: float = Field(default=DEFAULT_COPURCHASE_WEIGHT, ge=0)
    visual_weight: float = Field(default=DEFAULT_VISUAL_WEIGHT, ge=0)
    text_weight: float = Field(default=DEFAULT_TEXT_WEIGHT, ge=0)
    nearby_weight: float = Field(default=DEFAULT_NEARBY_WEIGHT, ge=0)
    visual_signal: VisualMethod = CLIP_METHOD
    purchase_attempts: int = Field(default=DEFAULT_PURCHASE_ATTEMPTS, ge=1)
    redis_url: RedisDsn | None = None
    cache_ttl_seconds: int = Field(default=DEFAULT_CACHE_TTL_SECONDS, ge=1)
    redis_timeout_seconds: float = Field(default=DEFAULT_REDIS_TIMEOUT_SECONDS, gt=0)
    cache_retry_seconds: float = Field(default=DEFAULT_CACHE_RETRY_SECONDS, ge=0)

    def fusion_weights(self) -> FusionWeights:
        return FusionWeights(
            copurchase=self.copurchase_weight,
            visual=self.visual_weight,
            text=self.text_weight,
            nearby=self.nearby_weight,
        )


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
        copurchase_min_support: clientes que tienen que haber comprado un par para que
            entre a `shop_compras_conjuntas`.
        copurchase_chunk_products: productos del primer lote del `GROUP BY` que calcula los
            pares; se ajusta solo a lo que cabe en una respuesta del gestor.
    """

    data_dir: Path
    engine_data_dir: PurePath | None = None
    embeddings_dir: Path | None = None
    clip_batch_size: int = Field(default=DEFAULT_CLIP_BATCH_SIZE, ge=1)
    preprocess_workers: int = Field(default=DEFAULT_PREPROCESS_WORKERS, ge=1)
    progress_rows: int = Field(default=DEFAULT_PROGRESS_ROWS, ge=1)
    job_timeout_seconds: float = Field(default=DEFAULT_JOB_TIMEOUT_SECONDS, gt=0)
    copurchase_min_support: int = Field(default=DEFAULT_COPURCHASE_MIN_SUPPORT, ge=1)
    copurchase_chunk_products: int = Field(default=DEFAULT_COPURCHASE_CHUNK_PRODUCTS, ge=1)

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
