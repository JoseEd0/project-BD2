"""El tipo de un archivo sale de su extensión."""

from pathlib import Path

import pytest

from config import EngineConfig
from multimedia import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MediaKind,
    UnsupportedMediaTypeError,
    extract_features,
    media_kind_of,
)


@pytest.mark.parametrize("extension", sorted(IMAGE_EXTENSIONS))
def test_image_extensions_are_images(extension: str):
    assert media_kind_of(Path(f"foto{extension}")) is MediaKind.IMAGE


@pytest.mark.parametrize("extension", sorted(AUDIO_EXTENSIONS))
def test_audio_extensions_are_audio(extension: str):
    assert media_kind_of(Path(f"pista{extension}")) is MediaKind.AUDIO


def test_the_extension_ignores_case():
    assert media_kind_of(Path("FOTO.JPG")) is MediaKind.IMAGE
    assert media_kind_of(Path("Pista.Mp3")) is MediaKind.AUDIO


@pytest.mark.parametrize("name", ["notas.txt", "video.mp4", "sin_extension", "foto.jpg.bak"])
def test_an_unknown_extension_is_a_domain_error(name: str):
    with pytest.raises(UnsupportedMediaTypeError, match="no es una imagen ni un audio"):
        media_kind_of(Path(name))


def test_extraction_rejects_an_unknown_extension_without_reading_it(tmp_path: Path):
    with pytest.raises(UnsupportedMediaTypeError):
        extract_features(tmp_path / "no-existe.txt", EngineConfig())
