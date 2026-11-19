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
# Un WAV estéreo de 16 bits a 44,1 kHz de 4 min cabe; un MP3 de 10 min a 320 kbit/s, también.
# El gestor admite hasta 64 MiB por subida (`max_upload_bytes`).
DEFAULT_MAX_UPLOAD_BYTES = 48 * 1024 * 1024
# El máximo del gestor (`max_segment_audio_seconds`): 10 min dan 239 ventanas de 5 s cada
# 2,5 s, dentro de las 256 que busca por consulta.
DEFAULT_MAX_UPLOAD_SECONDS = 600.0
# Una copia alineada trae a sus vecinas solapadas (el tramo de antes y el de después) entre
# los primeros vecinos; diez dejan sitio a otras canciones parecidas sin pasar de las
# `max_result_rows` filas del gestor con la subida más larga (239 · 10).
DEFAULT_NEIGHBOURS_PER_WINDOW = 10
# Dos ventanas seguidas son 7,5 s de audio, la copia más corta que llena entera una ventana
# del catálogo (ver el README).
DEFAULT_MIN_RUN_WINDOWS = 2
# El de máxima J de Youden en la curva ROC de 400 remixes sin distorsión, con la verdad conocida
# y el puntaje por la evidencia de la racha (benchmarks/README.md, §17): encuentra el 71 % de
# los fragmentos copiados y da alguna coincidencia en el 23 % de los remixes sin copias (área
# bajo la curva 0,79). Es bajo porque el puntaje lo es: una racha de 3 ventanas (10 s) con
# similitud media 0,93 vale 3/7 · 0,53 = 0,23 y pasa; con 0,92, 0,20 y no. Bajo una distorsión
# de nivel, ruido, tempo o tono ningún umbral sirve: el descriptor deja de encontrar la copia.
DEFAULT_MATCH_THRESHOLD = 0.21
DEFAULT_MAX_MATCHES = 10
# Por debajo, silencio digital o el ruido de fondo de una grabación: -60 dBFS es mil veces
# menos potencia que una pista a -30 dBFS, que ya suena bajo.
DEFAULT_SILENCE_THRESHOLD_DBFS = -60.0


class CopyrightSettings(ServiceSettings):
    """Lo que necesita el API del servicio: lo común (ver `appkit.ServiceSettings`) y la
    detección.

    Attributes:
        max_upload_bytes, max_upload_seconds: el audio más pesado y el más largo que se
            admite en `POST /detections`.
        neighbours_per_window: tramos del catálogo que se piden por ventana de la subida.
        min_run_windows: ventanas seguidas con el mismo desfase que hacen una coincidencia.
        match_threshold: puntaje desde el que una coincidencia cuenta como copia.
        max_matches: coincidencias que se devuelven, las de mayor puntaje.
        silence_threshold_dbfs: nivel por debajo del cual una ventana no vota.
    """

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True, extra="ignore")

    max_upload_bytes: int = Field(default=DEFAULT_MAX_UPLOAD_BYTES, ge=1)
    max_upload_seconds: float = Field(default=DEFAULT_MAX_UPLOAD_SECONDS, gt=0)
    neighbours_per_window: int = Field(default=DEFAULT_NEIGHBOURS_PER_WINDOW, ge=1)
    min_run_windows: int = Field(default=DEFAULT_MIN_RUN_WINDOWS, ge=1)
    match_threshold: float = Field(default=DEFAULT_MATCH_THRESHOLD, ge=0, le=1)
    max_matches: int = Field(default=DEFAULT_MAX_MATCHES, ge=1)
    silence_threshold_dbfs: float = DEFAULT_SILENCE_THRESHOLD_DBFS


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
