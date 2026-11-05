"""Archivos multimedia de prueba, generados en el directorio temporal de cada test."""

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from tests.multimedia.media_support import (
    FLAT_GRAY,
    TEXTURE_SIDE,
    textured_pixels,
    tone,
    write_audio,
    write_image,
)


@pytest.fixture
def textured_image(tmp_path: Path) -> Path:
    return write_image(tmp_path / "textura.png", textured_pixels(TEXTURE_SIDE, TEXTURE_SIDE))


@pytest.fixture
def flat_image(tmp_path: Path) -> Path:
    pixels = np.full((TEXTURE_SIDE, TEXTURE_SIDE), FLAT_GRAY, dtype=np.uint8)
    return write_image(tmp_path / "lisa.png", pixels)


@pytest.fixture
def one_second_tone(tmp_path: Path) -> Path:
    return write_audio(tmp_path / "tono.wav", tone(1.0))


@pytest.fixture
def corrupt_file(tmp_path: Path) -> Callable[[str], Path]:
    def make(extension: str) -> Path:
        path = tmp_path / f"dañado{extension}"
        path.write_bytes(b"esto no es un archivo multimedia" * 8)
        return path

    return make
