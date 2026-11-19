"""La configuración del servicio de rostros, leída de variables `FACES_*`."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict

from appkit import ServiceSettings
from faces.config import PipelineConfig
from faces.enrollment import IdentityThresholds, PosePlan
from faces.pose import Pose

ENV_PREFIX = "FACES_"
SECONDS_PER_DAY = 86_400
MEBIBYTE = 1 << 20
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

    def pipeline(self) -> PipelineConfig:
        return PipelineConfig(max_image_pixels=self.max_image_pixels)
