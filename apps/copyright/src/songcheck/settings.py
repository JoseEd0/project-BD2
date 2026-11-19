"""La configuración del servicio de copyright, del entorno con el prefijo `COPYRIGHT_`.

Las variables comunes a todo servicio (`COPYRIGHT_ENGINE_URL`, `COPYRIGHT_ENGINE_KEY`, …)
son las de `appkit.ServiceSettings`; el trabajo de carga añade las suyas en `SeedSettings`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from appkit import ServiceSettings
from songcheck.catalog import DATASET_DIRECTORY
from songcheck.fma.prepare import DEFAULT_AUDIO_URL, DEFAULT_METADATA_URL, FmaSources

ENV_PREFIX = "COPYRIGHT_"
DATA_DIRECTORY_VARIABLE = f"{ENV_PREFIX}DATA_DIR"
DEFAULT_DOWNLOAD_THREADS = 4
# El `CREATE INDEX` del catálogo completo extrae los MFCC de unos 80 000 tramos: minutos, no
# los segundos de una consulta. Su respuesta llega al terminar, así que se espera hasta esto.
DEFAULT_INDEX_TIMEOUT_SECONDS = 3600.0


class CopyrightSettings(ServiceSettings):
    """Lo que necesita el API del servicio: solo lo común (ver `appkit.ServiceSettings`)."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True, extra="ignore")


class SeedSettings(CopyrightSettings):
    """Lo que necesita además el trabajo de carga.

    Attributes:
        data_dir: carpeta de datasets tal como la ve este proceso; el catálogo queda en
            `<data_dir>/fma_small/`.
        engine_data_dir: la misma carpeta tal como la ve el gestor, que lee el audio por su
            ruta (en Docker, otro punto de montaje). Por omisión, `data_dir` en absoluto: el
            gestor en la misma máquina.
        download_threads: pistas que se bajan a la vez, cada una con su conexión.
        audio_url, metadata_url: de dónde se leen los `.zip` del FMA.
        index_timeout_seconds: cuánto se espera la respuesta del `CREATE INDEX`.
    """

    data_dir: Path
    engine_data_dir: Path | None = None
    download_threads: int = Field(default=DEFAULT_DOWNLOAD_THREADS, ge=1)
    audio_url: str = DEFAULT_AUDIO_URL
    metadata_url: str = DEFAULT_METADATA_URL
    index_timeout_seconds: float = Field(default=DEFAULT_INDEX_TIMEOUT_SECONDS, gt=0)

    def sources(self) -> FmaSources:
        return FmaSources(audio_url=self.audio_url, metadata_url=self.metadata_url)

    def engine_media_root(self) -> str:
        """La carpeta del dataset como la ve el gestor: el prefijo de cada ruta guardada."""
        root = self.engine_data_dir or self.data_dir.resolve()
        return str(root / DATASET_DIRECTORY)
