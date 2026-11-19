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
# Un vecino de CLIP no sabe de departamentos ni de precios: con un filtro se piden diez veces
# más y se filtran las fichas, así que un filtro que deja uno de cada diez aún llena la lista.
DEFAULT_FILTERED_SEARCH_FACTOR = 10
# Una foto de un celular pesa de 2 a 6 MB; la interfaz la reduce antes de subirla.
DEFAULT_PHOTO_MAX_BYTES = 8 * 1024 * 1024
# 40 megapíxeles: más que cualquier cámara de celular, y una «bomba» de descompresión no
# llega a decodificarse.
DEFAULT_PHOTO_MAX_PIXELS = 40_000_000
# Las fotos se llaman como el SHA-256 de sus bytes: no cambian, solo una nueva carga podría
# darle otra foto a un producto.
DEFAULT_PHOTO_CACHE_SECONDS = 86_400
DEFAULT_FEATURED_USERS = 12
# Por debajo quedan las promociones colgadas del árbol de categorías («Prime Card Bonus»).
DEFAULT_DEPARTMENT_MIN_PRODUCTS = 20
# Un par comprado por un solo cliente es casi siempre casualidad; con 2, la tabla baja de
# 4,7 millones de pares a 406 668 (medido en el dataset).
DEFAULT_COPURCHASE_MIN_SUPPORT = 2
# El recomendador pide como mucho unos cientos de candidatos por señal.
DEFAULT_COPURCHASE_MAX_PARTNERS = 500
# Compras de entrenamiento por lote del GROUP BY: 396 958 en unos 40 lotes, cada uno con un
# resultado por debajo de las 10 000 filas de una respuesta del gestor en el dataset.
DEFAULT_COPURCHASE_CHUNK_BUYERS = 10_000


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
        data_dir: carpeta de datasets vista desde el API, para servir las fotos de
            `<data_dir>/amazon_musical_instruments/images/`; sin ella, no hay fotos.
        filtered_search_factor: cuántas veces más vecinos de CLIP se piden con un filtro.
        photo_max_bytes: lo más que pesa una foto subida para buscar.
        photo_max_pixels: lo más que mide una foto subida, en píxeles.
        photo_cache_seconds: cuánto guarda el navegador la foto de un producto.
        featured_users: usuarios que se ofrecen para «recomendado para ti».
        department_min_products: productos que necesita un departamento para listarse.
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
    data_dir: Path | None = None
    filtered_search_factor: int = Field(default=DEFAULT_FILTERED_SEARCH_FACTOR, ge=1)
    photo_max_bytes: int = Field(default=DEFAULT_PHOTO_MAX_BYTES, ge=1)
    photo_max_pixels: int = Field(default=DEFAULT_PHOTO_MAX_PIXELS, ge=1)
    photo_cache_seconds: int = Field(default=DEFAULT_PHOTO_CACHE_SECONDS, ge=0)
    featured_users: int = Field(default=DEFAULT_FEATURED_USERS, ge=1)
    department_min_products: int = Field(default=DEFAULT_DEPARTMENT_MIN_PRODUCTS, ge=1)

    @property
    def photos_root(self) -> Path | None:
        """La carpeta del dataset, de la que cuelga `images/`; `None` sin `data_dir`."""
        if self.data_dir is None:
            return None
        return self.data_dir / DATASET_DIRECTORY

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
        copurchase_max_partners: pares que se guardan por producto, los de más clientes.
        copurchase_chunk_buyers: compras que suma, como mucho, un lote del `GROUP BY` que
            calcula los pares; un lote que no cabe en una respuesta se parte solo.
    """

    data_dir: Path
    engine_data_dir: PurePath | None = None
    embeddings_dir: Path | None = None
    clip_batch_size: int = Field(default=DEFAULT_CLIP_BATCH_SIZE, ge=1)
    preprocess_workers: int = Field(default=DEFAULT_PREPROCESS_WORKERS, ge=1)
    progress_rows: int = Field(default=DEFAULT_PROGRESS_ROWS, ge=1)
    job_timeout_seconds: float = Field(default=DEFAULT_JOB_TIMEOUT_SECONDS, gt=0)
    copurchase_min_support: int = Field(default=DEFAULT_COPURCHASE_MIN_SUPPORT, ge=1)
    copurchase_max_partners: int = Field(default=DEFAULT_COPURCHASE_MAX_PARTNERS, ge=1)
    copurchase_chunk_buyers: int = Field(default=DEFAULT_COPURCHASE_CHUNK_BUYERS, ge=1)

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
