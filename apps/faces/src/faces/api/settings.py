"""La configuración del servicio de rostros, leída de variables `FACES_*`."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict

from appkit import ServiceSettings
from faces.config import PipelineConfig
from faces.enrollment import IdentityThresholds, PosePlan
from faces.pose import Pose
from faces.tracking import TrackingConfig

ENV_PREFIX = "FACES_"
SECONDS_PER_DAY = 86_400
MEBIBYTE = 1 << 20
# Un cuadro Full HD: más de lo que manda la cámara de un celular reducida para el socket.
FULL_HD_PIXELS = 1920 * 1080
DEFAULT_TRACKING = TrackingConfig()
DEFAULT_THRESHOLDS = IdentityThresholds()
DEFAULT_PIPELINE = PipelineConfig()


class FacesSettings(ServiceSettings):
    """Lo propio del servicio de rostros, además de lo común de `ServiceSettings`.

    Attributes:
        models_dir: la carpeta de YuNet y SFace (`FACES_MODELS_DIR`, la misma que usa
            `python -m faces models`).
        retention_days: días que dura un registro; después se borra solo.
        enrollment_ttl_seconds: cuánto dura una sesión de registro sin terminar.
        max_shot_attempts: tomas que se pueden enviar en una sesión, aceptadas o no.
        enrollment_poses: el plan de poses del registro; su largo es cuántas tomas pide.
        max_upload_bytes: tamaño máximo de una foto subida.
        max_image_pixels: píxeles que puede declarar una foto (ver `PipelineConfig`).
        max_query_faces: rostros de una foto que busca `POST /identify`.
        search_neighbours: embeddings vecinos que se piden al HNSW por consulta; con tres
            tomas por persona, unas tres personas candidatas.
        session_threshold, match_threshold, duplicate_threshold, lookalike_threshold: los
            umbrales de coseno (ver `IdentityThresholds`).
        analysis_workers: fotos que la réplica analiza a la vez, cada una en su hilo con su
            pipeline; las demás esperan turno.
        purge_interval_seconds: cada cuánto se borran los registros y sesiones vencidos.
        redis_url: el Redis de los rastros del reconocimiento en vivo; sin él, no hay caché
            de rastros y cada rostro de cada cuadro se busca.
        redis_timeout_seconds: plazo de Redis para conectar y para cada operación; corto,
            porque si Redis no responde cada cuadro lo espera hasta que se deja de probar.
        redis_retry_seconds: cuánto se deja de probar Redis después de un fallo.
        stream_max_frame_bytes, stream_max_frame_pixels: el tamaño máximo de un cuadro, en
            bytes y en píxeles declarados.
        stream_max_fps: cuadros por segundo que procesa una conexión; los que llegan antes
            esperan y, si llega otro, se descartan.
        stream_idle_timeout_seconds: una conexión sin mensajes por este tiempo se cierra.
        stream_max_connections: conexiones de reconocimiento a la vez en una réplica.
        track_min_iou, track_verify_seconds, track_lost_seconds: el seguimiento (ver
            `TrackingConfig`).
        track_session_ttl_seconds: cuánto viven en Redis los rastros de una sesión sin
            cuadros: lo que tiene el celular para reconectarse y retomarlos.
    """

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True, extra="ignore")

    models_dir: Path
    retention_days: int = Field(default=30, ge=1)
    enrollment_ttl_seconds: int = Field(default=900, ge=1)
    max_shot_attempts: int = Field(default=20, ge=1)
    enrollment_poses: tuple[Pose, ...] = Field(
        default=(Pose.FRONT, Pose.LEFT, Pose.RIGHT), min_length=1
    )
    max_upload_bytes: int = Field(default=10 * MEBIBYTE, ge=1)
    max_image_pixels: int = Field(default=DEFAULT_PIPELINE.max_image_pixels, ge=1)
    max_query_faces: int = Field(default=10, ge=1)
    search_neighbours: int = Field(default=10, ge=1)
    session_threshold: float = Field(default=DEFAULT_THRESHOLDS.session, ge=-1, le=1)
    match_threshold: float = Field(default=DEFAULT_THRESHOLDS.match, ge=-1, le=1)
    duplicate_threshold: float = Field(default=DEFAULT_THRESHOLDS.duplicate, ge=-1, le=1)
    lookalike_threshold: float = Field(default=DEFAULT_THRESHOLDS.lookalike, ge=-1, le=1)
    analysis_workers: int = Field(default=2, ge=1)
    purge_interval_seconds: float = Field(default=300.0, gt=0)
    redis_url: str | None = None
    redis_timeout_seconds: float = Field(default=0.1, gt=0)
    redis_retry_seconds: float = Field(default=5.0, ge=0)
    stream_max_frame_bytes: int = Field(default=MEBIBYTE, ge=1)
    stream_max_frame_pixels: int = Field(default=FULL_HD_PIXELS, ge=1)
    stream_max_fps: float = Field(default=15.0, gt=0)
    stream_idle_timeout_seconds: float = Field(default=30.0, gt=0)
    stream_max_connections: int = Field(default=16, ge=1)
    track_min_iou: float = Field(default=DEFAULT_TRACKING.min_iou, gt=0, le=1)
    track_verify_seconds: float = Field(default=DEFAULT_TRACKING.verify_seconds, ge=0)
    track_lost_seconds: float = Field(default=DEFAULT_TRACKING.lost_seconds, ge=0)
    track_session_ttl_seconds: int = Field(default=60, ge=1)

    @model_validator(mode="after")
    def lookalike_below_duplicate(self) -> FacesSettings:
        if self.lookalike_threshold > self.duplicate_threshold:
            raise ValueError("lookalike_threshold no puede pasar de duplicate_threshold")
        return self

    @property
    def retention_seconds(self) -> int:
        return self.retention_days * SECONDS_PER_DAY

    def pose_plan(self) -> PosePlan:
        return PosePlan(self.enrollment_poses)

    def thresholds(self) -> IdentityThresholds:
        return IdentityThresholds(
            session=self.session_threshold,
            match=self.match_threshold,
            duplicate=self.duplicate_threshold,
            lookalike=self.lookalike_threshold,
        )

    def tracking(self) -> TrackingConfig:
        return TrackingConfig(
            min_iou=self.track_min_iou,
            verify_seconds=self.track_verify_seconds,
            lost_seconds=self.track_lost_seconds,
        )

    def pipeline(self) -> PipelineConfig:
        return PipelineConfig(max_image_pixels=self.max_image_pixels)
