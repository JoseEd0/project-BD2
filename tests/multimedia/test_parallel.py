"""La extracción de muchos archivos: el mismo resultado en un proceso o en varios, en flujo
y sin cortar por un archivo dañado."""

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from tests.multimedia.media_support import (
    chirp,
    textured_pixels,
    tone,
    write_audio,
    write_image,
)

from config import ConfigurationError, EngineConfig
from multimedia import (
    IN_FLIGHT_CHUNKS_PER_WORKER,
    ExtractedFeatures,
    ExtractionOutcome,
    FailedExtraction,
    UnreadableMediaError,
    UnsupportedMediaTypeError,
    extract_each,
    extract_features,
    extract_in_parallel,
)

WORKERS = 2
CHUNK_FILES = 2
IMAGES = 5
IMAGE_SIDE = 96
STREAMED_FILES = 1_000


@pytest.fixture
def mixed_files(tmp_path: Path) -> list[Path]:
    images = [
        write_image(tmp_path / f"imagen{seed}.png", textured_pixels(IMAGE_SIDE, IMAGE_SIDE, seed))
        for seed in range(IMAGES)
    ]
    corrupt = tmp_path / "dañada.jpg"
    corrupt.write_bytes(b"no es una imagen")
    return [
        *images[:2],
        write_audio(tmp_path / "tono.wav", tone(1.0)),
        corrupt,
        tmp_path / "notas.txt",
        write_audio(tmp_path / "barrido.flac", chirp(1.0)),
        *images[2:],
    ]


def assert_same_outcomes(actual: list[ExtractionOutcome], expected: list[ExtractionOutcome]):
    assert [outcome.path for outcome in actual] == [outcome.path for outcome in expected]
    for got, wanted in zip(actual, expected, strict=True):
        assert type(got) is type(wanted)
        if isinstance(got, ExtractedFeatures) and isinstance(wanted, ExtractedFeatures):
            assert got.descriptors.dtype == wanted.descriptors.dtype
            assert np.array_equal(got.descriptors, wanted.descriptors)
        if isinstance(got, FailedExtraction) and isinstance(wanted, FailedExtraction):
            assert type(got.error) is type(wanted.error)
            assert str(got.error) == str(wanted.error)


def test_each_file_gives_its_descriptors_or_its_error(mixed_files: list[Path]):
    outcomes = list(extract_each(mixed_files, EngineConfig()))

    assert [outcome.path for outcome in outcomes] == mixed_files
    failures = {o.path.name: type(o.error) for o in outcomes if isinstance(o, FailedExtraction)}
    assert failures == {"dañada.jpg": UnreadableMediaError, "notas.txt": UnsupportedMediaTypeError}
    for outcome in outcomes:
        if isinstance(outcome, ExtractedFeatures):
            assert np.array_equal(outcome.descriptors, extract_features(outcome.path, EngineConfig()))


def test_parallel_extraction_matches_sequential_extraction(mixed_files: list[Path]):
    config = EngineConfig(feature_chunk_files=CHUNK_FILES)

    parallel = list(extract_in_parallel(mixed_files, config, WORKERS))

    assert_same_outcomes(parallel, list(extract_each(mixed_files, config)))


def test_parallel_extraction_reads_the_paths_as_it_goes(tmp_path: Path):
    """Los archivos no existen y su extensión no es multimedia, así que cada uno es un error
    inmediato: lo que se mide es cuántos se pidieron, no cuánto tarda describirlos."""
    taken: list[Path] = []

    def paths() -> Iterator[Path]:
        for number in range(STREAMED_FILES):
            path = tmp_path / f"archivo{number}.txt"
            taken.append(path)
            yield path

    config = EngineConfig(feature_chunk_files=CHUNK_FILES)
    outcomes = extract_in_parallel(paths(), config, WORKERS)
    first = next(outcomes)
    outcomes.close()

    assert first.path == taken[0]
    in_flight = WORKERS * IN_FLIGHT_CHUNKS_PER_WORKER + 1
    assert len(taken) <= in_flight * CHUNK_FILES


def test_an_empty_input_gives_no_outcomes():
    assert list(extract_in_parallel([], EngineConfig(), WORKERS)) == []
    assert list(extract_each([], EngineConfig())) == []


@pytest.mark.parametrize("workers", [0, -1])
def test_at_least_one_worker_is_required(workers: int):
    with pytest.raises(ConfigurationError):
        extract_in_parallel([], EngineConfig(), workers)
