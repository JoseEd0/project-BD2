"""Un audio cortado en ventanas: cuántas salen, los límites y una sola decodificación."""

from pathlib import Path

import numpy as np
import pytest
from tests.multimedia.media_support import chirp, textured_pixels, write_audio, write_image

import multimedia.segments
from config import EngineConfig
from multimedia import (
    SegmentLimitError,
    TemporalFragment,
    UnsupportedMediaTypeError,
    decode_audio,
    decode_segments,
    describe_segments,
    extract_features,
    segment_windows,
)

WINDOW = 10.0
HOP = 5.0
SONG_SECONDS = 180.0
CLIP_SECONDS = 3.0
SMALL_WINDOW = 1.0
SMALL_HOP = 0.5
LIMITED = EngineConfig(min_segment_seconds=0.5, max_segment_audio_seconds=4.0, max_segments=5)


def test_a_three_minute_song_gives_thirty_five_windows():
    windows = segment_windows(SONG_SECONDS, WINDOW, HOP, EngineConfig())

    assert len(windows) == 35
    assert windows[0] == TemporalFragment(0.0, WINDOW)
    assert windows[-1] == TemporalFragment(170.0, SONG_SECONDS)


def test_only_whole_windows_are_kept():
    windows = segment_windows(23.0, WINDOW, HOP, EngineConfig())

    assert windows == (
        TemporalFragment(0.0, 10.0),
        TemporalFragment(5.0, 15.0),
        TemporalFragment(10.0, 20.0),
    )


def test_an_audio_shorter_than_a_window_is_one_window():
    assert segment_windows(4.0, WINDOW, HOP, EngineConfig()) == (TemporalFragment(0.0, 4.0),)


@pytest.mark.parametrize(
    ("duration", "window", "hop", "message"),
    [
        (0.2, 1.0, 0.5, "menos que la ventana más corta"),
        (4.5, 1.0, 1.0, "admite hasta"),
        (4.0, 0.25, 0.25, "al menos"),
        (4.0, 1.0, 0.0, "salto ser positivo"),
        (4.0, 1.0, 0.5, "pasan del máximo"),
    ],
)
def test_the_limits_are_enforced(duration: float, window: float, hop: float, message: str):
    with pytest.raises(SegmentLimitError, match=message):
        segment_windows(duration, window, hop, LIMITED)


def test_a_song_is_decoded_once_and_every_window_matches_its_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    song = write_audio(tmp_path / "cancion.wav", chirp(CLIP_SECONDS))
    decoded: list[Path] = []

    def counting(path: Path):
        decoded.append(path)
        return decode_audio(path)

    monkeypatch.setattr(multimedia.segments, "decode_audio", counting)
    config = EngineConfig()

    segmented = decode_segments(song, SMALL_WINDOW, SMALL_HOP, config)
    described = describe_segments(segmented, config)

    assert decoded == [song]
    assert len(segmented.windows) == 5
    for window, descriptors in zip(segmented.windows, described, strict=True):
        assert np.array_equal(descriptors, extract_features(song, config, window))


def test_a_song_too_long_is_refused_before_decoding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    song = write_audio(tmp_path / "larga.wav", chirp(CLIP_SECONDS))

    def never(path: Path):
        raise AssertionError(f"no se debía decodificar {path}")

    monkeypatch.setattr(multimedia.segments, "decode_audio", never)
    config = EngineConfig(max_segment_audio_seconds=CLIP_SECONDS - 1.0)

    with pytest.raises(SegmentLimitError, match="admite hasta"):
        decode_segments(song, SMALL_WINDOW, SMALL_HOP, config)


def test_an_image_has_no_segments(tmp_path: Path):
    photo = write_image(tmp_path / "foto.png", textured_pixels(32, 32))

    with pytest.raises(UnsupportedMediaTypeError, match="no es un audio"):
        decode_segments(photo, SMALL_WINDOW, SMALL_HOP, EngineConfig())
