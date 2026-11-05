"""SIFT: descriptores de 128 dimensiones, tope por respuesta y lado mayor acotado."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from tests.multimedia.media_support import textured_pixels, write_image

from config import EngineConfig
from multimedia import (
    SIFT_DESCRIPTOR_DIMENSION,
    MediaKind,
    UnreadableMediaError,
    descriptor_dimension,
    extract_features,
)

UNCAPPED = 1_000_000
SMALL_CAP = 5
LARGE_SIDE = 400
REDUCED_SIDE = 100
TINY_SIDE = 48
HUGE_MAX_SIDE = 4096


@pytest.fixture
def uncapped() -> EngineConfig:
    return EngineConfig(image_max_descriptors=UNCAPPED)


def test_a_textured_image_gives_128_dimensional_float32_descriptors(textured_image: Path):
    descriptors = extract_features(textured_image, EngineConfig())

    assert descriptors.ndim == 2
    assert descriptors.shape[0] > 0
    assert descriptors.shape[1] == SIFT_DESCRIPTOR_DIMENSION
    assert descriptors.dtype == np.float32
    assert descriptors.flags.c_contiguous


def test_the_dimension_is_announced_before_extracting():
    assert descriptor_dimension(MediaKind.IMAGE, EngineConfig()) == SIFT_DESCRIPTOR_DIMENSION


def test_a_flat_image_gives_no_descriptors_without_failing(flat_image: Path):
    descriptors = extract_features(flat_image, EngineConfig())

    assert descriptors.shape == (0, SIFT_DESCRIPTOR_DIMENSION)
    assert descriptors.dtype == np.float32


def test_the_cap_keeps_the_strongest_keypoints(textured_image: Path, uncapped: EngineConfig):
    every = extract_features(textured_image, uncapped)
    capped = extract_features(textured_image, replace(uncapped, image_max_descriptors=SMALL_CAP))

    assert len(every) > SMALL_CAP
    assert np.array_equal(capped, every[:SMALL_CAP])


def test_a_large_image_is_reduced_to_the_maximum_side(tmp_path: Path, uncapped: EngineConfig):
    pixels = textured_pixels(LARGE_SIDE, LARGE_SIDE // 2)
    large = write_image(tmp_path / "grande.png", pixels)
    reduced_pixels = cv2.resize(pixels, (REDUCED_SIDE // 2, REDUCED_SIDE), interpolation=cv2.INTER_AREA)
    reduced = write_image(tmp_path / "reducida.png", reduced_pixels)

    from_large = extract_features(large, replace(uncapped, image_max_side=REDUCED_SIDE))

    assert np.array_equal(from_large, extract_features(reduced, uncapped))
    assert not np.array_equal(from_large, extract_features(large, uncapped))


def test_a_small_image_is_never_enlarged(tmp_path: Path, uncapped: EngineConfig):
    tiny = write_image(tmp_path / "chica.png", textured_pixels(TINY_SIDE, TINY_SIDE))

    at_its_size = extract_features(tiny, replace(uncapped, image_max_side=TINY_SIDE))

    assert np.array_equal(at_its_size, extract_features(tiny, replace(uncapped, image_max_side=HUGE_MAX_SIDE)))


def test_a_color_image_is_described_in_grayscale(tmp_path: Path):
    gray = textured_pixels(TINY_SIDE * 2, TINY_SIDE * 2)
    color = write_image(tmp_path / "color.png", cv2.merge([gray, gray, gray]))
    single = write_image(tmp_path / "gris.png", gray)

    assert np.array_equal(extract_features(color, EngineConfig()), extract_features(single, EngineConfig()))


def test_extraction_is_deterministic(textured_image: Path):
    assert np.array_equal(
        extract_features(textured_image, EngineConfig()),
        extract_features(textured_image, EngineConfig()),
    )


@pytest.mark.parametrize("extension", [".jpg", ".png"])
def test_a_corrupt_image_is_a_domain_error(corrupt_file: Callable[[str], Path], extension: str):
    with pytest.raises(UnreadableMediaError, match="no es una imagen"):
        extract_features(corrupt_file(extension), EngineConfig())


def test_an_empty_image_file_is_a_domain_error(tmp_path: Path):
    empty = tmp_path / "vacia.jpg"
    empty.write_bytes(b"")

    with pytest.raises(UnreadableMediaError):
        extract_features(empty, EngineConfig())


def test_a_missing_image_is_a_domain_error(tmp_path: Path):
    with pytest.raises(UnreadableMediaError, match="no se puede leer"):
        extract_features(tmp_path / "no-existe.png", EngineConfig())
