"""La extracción de muchos archivos: el mismo resultado en un proceso o en varios, en flujo,
sin cortar por un archivo dañado y con una sola decodificación por audio con tramos."""

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

import multimedia.parallel
from config import ConfigurationError, EngineConfig
from multimedia import (
    IN_FLIGHT_CHUNKS_PER_WORKER,
    ExtractedFeatures,
    ExtractionOutcome,
    FailedExtraction,
    FragmentOutOfRangeError,
    MediaReference,
    TemporalFragment,
    UnreadableMediaError,
    UnsupportedMediaTypeError,
    decode_audio,
    extract_each,
    extract_features,
    extract_in_parallel,
)

WORKERS = 2
CHUNK_FILES = 2
IMAGES = 5
IMAGE_SIDE = 96
STREAMED_FILES = 1_000
# Para saber que un archivo terminó, el lote lee la referencia siguiente.
FILE_END_LOOKAHEAD = 1
SWEEP_SECONDS = 4.0
WINDOWS = (TemporalFragment(0.0, 1.0), TemporalFragment(0.5, 1.5), TemporalFragment(2.0, None))
PAST_THE_END = TemporalFragment(SWEEP_SECONDS + 1.0, SWEEP_SECONDS + 2.0)


@pytest.fixture
def mixed_files(tmp_path: Path) -> list[MediaReference]:
    images = [
        write_image(tmp_path / f"imagen{seed}.png", textured_pixels(IMAGE_SIDE, IMAGE_SIDE, seed))
        for seed in range(IMAGES)
    ]
    corrupt = tmp_path / "dañada.jpg"
    corrupt.write_bytes(b"no es una imagen")
    paths = [
        *images[:2],
        write_audio(tmp_path / "tono.wav", tone(1.0)),
        corrupt,
        tmp_path / "notas.txt",
        write_audio(tmp_path / "barrido.flac", chirp(1.0)),
        *images[2:],
    ]
    return [MediaReference(path) for path in paths]


@pytest.fixture
def sweep(tmp_path: Path) -> Path:
    return write_audio(tmp_path / "barrido.wav", chirp(SWEEP_SECONDS))


@pytest.fixture
def decodes(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Los audios que se decodifican enteros mientras dura el test."""
    decoded: list[Path] = []

    def counting(path: Path):
        decoded.append(path)
        return decode_audio(path)

    monkeypatch.setattr(multimedia.parallel, "decode_audio", counting)
    return decoded


def assert_same_outcomes(actual: list[ExtractionOutcome], expected: list[ExtractionOutcome]):
    assert [outcome.reference for outcome in actual] == [outcome.reference for outcome in expected]
    for got, wanted in zip(actual, expected, strict=True):
        assert type(got) is type(wanted)
        if isinstance(got, ExtractedFeatures) and isinstance(wanted, ExtractedFeatures):
            assert got.descriptors.dtype == wanted.descriptors.dtype
            assert np.array_equal(got.descriptors, wanted.descriptors)
        if isinstance(got, FailedExtraction) and isinstance(wanted, FailedExtraction):
            assert type(got.error) is type(wanted.error)
            assert str(got.error) == str(wanted.error)


def test_each_file_gives_its_descriptors_or_its_error(mixed_files: list[MediaReference]):
    outcomes = list(extract_each(mixed_files, EngineConfig()))

    assert [outcome.reference for outcome in outcomes] == mixed_files
    failures = {
        o.reference.path.name: type(o.error) for o in outcomes if isinstance(o, FailedExtraction)
    }
    assert failures == {"dañada.jpg": UnreadableMediaError, "notas.txt": UnsupportedMediaTypeError}
    for outcome in outcomes:
        if isinstance(outcome, ExtractedFeatures):
            expected = extract_features(outcome.reference.path, EngineConfig())
            assert np.array_equal(outcome.descriptors, expected)


def test_fragments_of_one_audio_are_cut_from_a_single_decode(sweep: Path, decodes: list[Path]):
    references = [MediaReference(sweep, window) for window in WINDOWS]

    outcomes = list(extract_each(references, EngineConfig()))

    assert decodes == [sweep]
    for outcome, window in zip(outcomes, WINDOWS, strict=True):
        assert isinstance(outcome, ExtractedFeatures)
        assert np.array_equal(outcome.descriptors, extract_features(sweep, EngineConfig(), window))


def test_a_single_fragment_reads_only_its_range(sweep: Path, decodes: list[Path]):
    outcomes = list(extract_each([MediaReference(sweep, WINDOWS[0])], EngineConfig()))

    assert decodes == []
    assert isinstance(outcomes[0], ExtractedFeatures)


def test_a_fragment_past_the_end_fails_alone(sweep: Path):
    references = [MediaReference(sweep, WINDOWS[0]), MediaReference(sweep, PAST_THE_END)]

    first, second = extract_each(references, EngineConfig())

    assert isinstance(first, ExtractedFeatures)
    assert isinstance(second, FailedExtraction)
    assert isinstance(second.error, FragmentOutOfRangeError)


def test_an_unreadable_audio_fails_every_fragment(tmp_path: Path):
    missing = tmp_path / "no-existe.wav"
    references = [MediaReference(missing, window) for window in WINDOWS]

    outcomes = list(extract_each(references, EngineConfig()))

    assert all(isinstance(outcome, FailedExtraction) for outcome in outcomes)


def test_a_batch_never_splits_the_fragments_of_a_file(sweep: Path, tmp_path: Path):
    tone_file = write_audio(tmp_path / "tono.wav", tone(1.0))
    references = [
        *(MediaReference(sweep, window) for window in WINDOWS),
        MediaReference(tone_file),
    ]

    batches = list(multimedia.parallel._chunks(references, 1))

    assert batches == [tuple(references[: len(WINDOWS)]), (references[-1],)]


def test_parallel_extraction_of_fragments_matches_sequential(sweep: Path, tmp_path: Path):
    tone_file = write_audio(tmp_path / "tono.wav", tone(1.0))
    references = [
        MediaReference(tone_file),
        *(MediaReference(sweep, window) for window in WINDOWS),
        MediaReference(sweep, PAST_THE_END),
    ]
    config = EngineConfig(feature_chunk_files=1)

    parallel = list(extract_in_parallel(references, config, WORKERS))

    assert_same_outcomes(parallel, list(extract_each(references, config)))


def test_parallel_extraction_matches_sequential_extraction(mixed_files: list[MediaReference]):
    config = EngineConfig(feature_chunk_files=CHUNK_FILES)

    parallel = list(extract_in_parallel(mixed_files, config, WORKERS))

    assert_same_outcomes(parallel, list(extract_each(mixed_files, config)))


def test_parallel_extraction_reads_the_paths_as_it_goes(tmp_path: Path):
    """Los archivos no existen y su extensión no es multimedia, así que cada uno es un error
    inmediato: lo que se mide es cuántos se pidieron, no cuánto tarda describirlos."""
    taken: list[MediaReference] = []

    def references() -> Iterator[MediaReference]:
        for number in range(STREAMED_FILES):
            reference = MediaReference(tmp_path / f"archivo{number}.txt")
            taken.append(reference)
            yield reference

    config = EngineConfig(feature_chunk_files=CHUNK_FILES)
    outcomes = extract_in_parallel(references(), config, WORKERS)
    first = next(outcomes)
    outcomes.close()

    assert first.reference == taken[0]
    in_flight = WORKERS * IN_FLIGHT_CHUNKS_PER_WORKER + 1
    assert len(taken) <= in_flight * CHUNK_FILES + FILE_END_LOOKAHEAD


def test_an_empty_input_gives_no_outcomes():
    assert list(extract_in_parallel([], EngineConfig(), WORKERS)) == []
    assert list(extract_each([], EngineConfig())) == []


@pytest.mark.parametrize("workers", [0, -1])
def test_at_least_one_worker_is_required(workers: int):
    with pytest.raises(ConfigurationError):
        extract_in_parallel([], EngineConfig(), workers)
