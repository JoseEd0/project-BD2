"""La configuración del servicio leída de `FACES_*`."""

from __future__ import annotations

import pytest

from appkit import SettingsError, load_settings
from faces.api.settings import FacesSettings
from faces.pose import Pose

REQUIRED = {
    "FACES_ENGINE_URL": "http://gestor:8000",
    "FACES_ENGINE_KEY": "clave",
    "FACES_MODELS_DIR": "/modelos",
}


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for variable, value in REQUIRED.items():
        monkeypatch.setenv(variable, value)
    return monkeypatch


def test_defaults(environment: pytest.MonkeyPatch):
    settings = load_settings(FacesSettings)
    assert settings.pose_plan().poses == (Pose.FRONT, Pose.LEFT, Pose.RIGHT)
    assert settings.retention_seconds == 30 * 86_400
    thresholds = settings.thresholds()
    assert thresholds.session < thresholds.match <= thresholds.duplicate


def test_the_plan_and_thresholds_come_from_the_environment(environment: pytest.MonkeyPatch):
    environment.setenv("FACES_ENROLLMENT_POSES", '["front", "front"]')
    environment.setenv("FACES_MATCH_THRESHOLD", "0.5")
    settings = load_settings(FacesSettings)
    assert settings.pose_plan().poses == (Pose.FRONT, Pose.FRONT)
    assert settings.thresholds().match == 0.5


def test_real_time_recognition_comes_from_the_environment(environment: pytest.MonkeyPatch):
    assert load_settings(FacesSettings).redis_url is None
    environment.setenv("FACES_REDIS_URL", "redis://redis:6379/1")
    environment.setenv("FACES_TRACK_VERIFY_SECONDS", "3")
    environment.setenv("FACES_TRACK_MIN_IOU", "0.4")
    environment.setenv("FACES_STREAM_MAX_FPS", "12")
    settings = load_settings(FacesSettings)
    assert settings.redis_url == "redis://redis:6379/1"
    assert (settings.tracking().verify_seconds, settings.tracking().min_iou) == (3.0, 0.4)
    assert settings.stream_max_fps == 12.0


@pytest.mark.parametrize(
    ("variable", "value", "problem"),
    [
        ("FACES_ENROLLMENT_POSES", '["front", "up"]', "FACES_ENROLLMENT_POSES"),
        ("FACES_ENROLLMENT_POSES", "[]", "FACES_ENROLLMENT_POSES"),
        ("FACES_MATCH_THRESHOLD", "1.5", "FACES_MATCH_THRESHOLD"),
        ("FACES_LOOKALIKE_THRESHOLD", "0.9", "lookalike_threshold"),
        ("FACES_STREAM_MAX_FPS", "0", "FACES_STREAM_MAX_FPS"),
        ("FACES_TRACK_MIN_IOU", "1.5", "FACES_TRACK_MIN_IOU"),
    ],
)
def test_bad_values_stop_the_start(
    environment: pytest.MonkeyPatch, variable: str, value: str, problem: str
):
    environment.setenv(variable, value)
    with pytest.raises(SettingsError, match=problem):
        load_settings(FacesSettings)


def test_the_models_directory_is_required(environment: pytest.MonkeyPatch):
    environment.delenv("FACES_MODELS_DIR")
    with pytest.raises(SettingsError, match="falta FACES_MODELS_DIR"):
        load_settings(FacesSettings)
