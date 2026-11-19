"""Dónde se buscan los archivos del modelo y cómo se identifican sus vectores."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag.errors import ModelNotFoundError
from rag.models import MODEL_REVISION, ModelFiles, ModelVariant, locate_model


def test_each_variant_has_its_own_model_id():
    ids = {ModelFiles.inside(Path("m"), variant).model_id for variant in ModelVariant}
    assert len(ids) == len(ModelVariant)
    assert all(MODEL_REVISION[:12] in model_id for model_id in ids)


def test_missing_files_say_how_to_download_them(tmp_path: Path):
    with pytest.raises(ModelNotFoundError, match=r"python -m rag\.models --variante int8"):
        locate_model(tmp_path, ModelVariant.INT8)
