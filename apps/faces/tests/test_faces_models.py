"""Dónde se bajan y dónde se buscan los modelos."""

from __future__ import annotations

from pathlib import Path

import pytest

from appkit.download import RemoteFile
from faces import ModelNotFoundError, ModelPaths, locate_models, models_directory_from_environment
from faces import models as models_module
from faces.models import DETECTOR, MODELS_DIRECTORY_VARIABLE, RECOGNIZER, download_models

SHA256_HEX_DIGITS = 64


def test_both_models_are_pinned_to_a_commit_with_their_checksum():
    for remote in (DETECTOR, RECOGNIZER):
        assert models_module.OPENCV_ZOO_COMMIT in remote.url
        assert len(remote.sha256) == SHA256_HEX_DIGITS
        assert remote.size > 0


def test_downloading_fetches_both_into_the_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    fetched: list[tuple[RemoteFile, Path]] = []

    def record(remote: RemoteFile, destination: Path) -> Path:
        fetched.append((remote, destination))
        return destination

    monkeypatch.setattr(models_module, "fetch_verified", record)

    paths = download_models(tmp_path)

    assert fetched == [(DETECTOR, paths.detector), (RECOGNIZER, paths.recognizer)]
    assert paths == ModelPaths.inside(tmp_path)


def test_locating_present_models_needs_no_download(tmp_path: Path):
    expected = ModelPaths.inside(tmp_path)
    for path in (expected.detector, expected.recognizer):
        path.write_bytes(b"onnx")

    assert locate_models(tmp_path) == expected


def test_a_missing_model_says_how_to_get_it(tmp_path: Path):
    ModelPaths.inside(tmp_path).detector.write_bytes(b"onnx")

    with pytest.raises(
        ModelNotFoundError, match=r"face_recognition_sface.*python -m faces models"
    ):
        locate_models(tmp_path)


def test_the_directory_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv(MODELS_DIRECTORY_VARIABLE, str(tmp_path))

    assert models_directory_from_environment() == tmp_path


def test_without_the_variable_there_is_no_directory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(MODELS_DIRECTORY_VARIABLE, raising=False)

    with pytest.raises(ModelNotFoundError, match=MODELS_DIRECTORY_VARIABLE):
        models_directory_from_environment()
