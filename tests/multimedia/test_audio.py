"""MFCC: una fila por ventana con los coeficientes y sus dos derivadas, sobre el segmento
pedido."""

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from tests.multimedia.media_support import SAMPLE_RATE, chirp, tone, write_audio

from config import EngineConfig
from multimedia import (
    MFCC_DELTA_ORDERS,
    MediaKind,
    UnreadableMediaError,
    descriptor_dimension,
    extract_features,
    mfcc_dimension,
)

CD_SAMPLE_RATE = 44_100
CUSTOM_COEFFICIENTS = 20
SEGMENT_OFFSET_SECONDS = 0.5
SEGMENT_SECONDS = 1.0
CHIRP_SECONDS = 2.0


def frames_of(seconds: float, config: EngineConfig) -> int:
    """librosa centra cada ventana en su salto, así que hay una ventana más que saltos
    completos."""
    return 1 + round(seconds * config.audio_sample_rate) // config.mfcc_hop_length


def test_a_tone_gives_one_descriptor_per_window(one_second_tone: Path):
    config = EngineConfig()

    descriptors = extract_features(one_second_tone, config)

    dimension = config.mfcc_coefficients * (1 + len(MFCC_DELTA_ORDERS))
    assert descriptors.shape == (frames_of(1.0, config), dimension)
    assert descriptors.dtype == np.float32
    assert descriptors.flags.c_contiguous
    assert np.isfinite(descriptors).all()


def test_the_dimension_follows_the_number_of_coefficients(one_second_tone: Path):
    config = EngineConfig(mfcc_coefficients=CUSTOM_COEFFICIENTS)

    descriptors = extract_features(one_second_tone, config)

    assert descriptors.shape[1] == mfcc_dimension(config) == CUSTOM_COEFFICIENTS * 3
    assert descriptor_dimension(MediaKind.AUDIO, config) == mfcc_dimension(config)


def test_stereo_audio_at_another_rate_is_mixed_and_resampled(tmp_path: Path):
    samples = tone(1.0, CD_SAMPLE_RATE)
    stereo = write_audio(tmp_path / "estereo.flac", np.stack([samples, samples], axis=1), CD_SAMPLE_RATE)
    config = EngineConfig()

    assert extract_features(stereo, config).shape == (frames_of(1.0, config), mfcc_dimension(config))


def test_an_mp3_is_decoded(tmp_path: Path):
    """El formato de FMA: libsndfile decodifica MP3 sin un códec aparte."""
    mp3 = write_audio(tmp_path / "pista.mp3", chirp(1.0))

    descriptors = extract_features(mp3, EngineConfig())

    assert descriptors.shape[0] > 0
    assert descriptors.shape[1] == mfcc_dimension(EngineConfig())


def test_only_the_requested_segment_is_described(tmp_path: Path):
    samples = chirp(CHIRP_SECONDS)
    whole = write_audio(tmp_path / "barrido.wav", samples)
    start = round(SEGMENT_OFFSET_SECONDS * SAMPLE_RATE)
    end = start + round(SEGMENT_SECONDS * SAMPLE_RATE)
    cut = write_audio(tmp_path / "recorte.wav", samples[start:end])
    segment = EngineConfig(
        audio_offset_seconds=SEGMENT_OFFSET_SECONDS, audio_duration_seconds=SEGMENT_SECONDS
    )

    from_whole = extract_features(whole, segment)

    assert np.array_equal(from_whole, extract_features(cut, EngineConfig()))
    assert from_whole.shape[0] == frames_of(SEGMENT_SECONDS, segment)


def test_a_duration_past_the_end_stops_at_the_end(one_second_tone: Path):
    config = EngineConfig(audio_duration_seconds=CHIRP_SECONDS)

    assert np.array_equal(
        extract_features(one_second_tone, config), extract_features(one_second_tone, EngineConfig())
    )


def test_an_offset_past_the_end_gives_no_descriptors(one_second_tone: Path):
    config = EngineConfig(audio_offset_seconds=CHIRP_SECONDS)

    assert extract_features(one_second_tone, config).shape == (0, mfcc_dimension(config))


def test_an_audio_without_samples_gives_no_descriptors(tmp_path: Path):
    silent = write_audio(tmp_path / "vacio.wav", np.zeros(0, dtype=np.float32))

    assert extract_features(silent, EngineConfig()).shape == (0, mfcc_dimension(EngineConfig()))


def test_audio_shorter_than_the_delta_window_is_described(tmp_path: Path):
    config = EngineConfig()
    hops = config.mfcc_delta_width // 2
    short = write_audio(tmp_path / "corto.wav", tone(hops * config.mfcc_hop_length / SAMPLE_RATE))

    assert extract_features(short, config).shape == (hops + 1, mfcc_dimension(config))


def test_silence_gives_finite_descriptors(tmp_path: Path):
    silence = write_audio(tmp_path / "silencio.wav", np.zeros(SAMPLE_RATE, dtype=np.float32))

    assert np.isfinite(extract_features(silence, EngineConfig())).all()


def test_extraction_is_deterministic(one_second_tone: Path):
    assert np.array_equal(
        extract_features(one_second_tone, EngineConfig()),
        extract_features(one_second_tone, EngineConfig()),
    )


def test_different_sounds_give_different_descriptors(tmp_path: Path):
    a_tone = write_audio(tmp_path / "tono.wav", tone(1.0))
    a_sweep = write_audio(tmp_path / "barrido.wav", chirp(1.0))

    assert not np.array_equal(
        extract_features(a_tone, EngineConfig()), extract_features(a_sweep, EngineConfig())
    )


@pytest.mark.parametrize("extension", [".wav", ".flac"])
def test_a_corrupt_audio_is_a_domain_error(corrupt_file: Callable[[str], Path], extension: str):
    with pytest.raises(UnreadableMediaError, match="no se puede leer el audio"):
        extract_features(corrupt_file(extension), EngineConfig())


def test_a_missing_audio_is_a_domain_error(tmp_path: Path):
    with pytest.raises(UnreadableMediaError):
        extract_features(tmp_path / "no-existe.mp3", EngineConfig())
