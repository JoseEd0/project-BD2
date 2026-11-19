"""Las ventanas del catálogo, con la misma regla que la consulta por segmentos del gestor."""

from __future__ import annotations

import pytest

from songcheck.windows import (
    MIN_SEGMENT_SECONDS,
    WINDOWING,
    SegmentWindow,
    Windowing,
    WindowingError,
    segment_windows,
)

FMA_CLIP_SECONDS = 29.976575963718822


def bounds(duration: float, windowing: Windowing = WINDOWING) -> list[tuple[float, float]]:
    return [(window.start, window.end) for window in segment_windows(duration, windowing)]


def test_the_catalog_windows_are_five_seconds_every_two_and_a_half():
    assert (WINDOWING.window_seconds, WINDOWING.hop_seconds) == (5.0, 2.5)


def test_an_exact_multiple_ends_with_a_window_on_the_last_second():
    assert bounds(10.0) == [(0.0, 5.0), (2.5, 7.5), (5.0, 10.0)]
    assert len(segment_windows(30.0)) == 11


def test_only_whole_windows_enter():
    assert bounds(9.9) == [(0.0, 5.0), (2.5, 7.5)]
    assert len(segment_windows(FMA_CLIP_SECONDS)) == 10


def test_a_track_shorter_than_a_window_is_one_window_to_its_end():
    assert bounds(3.25) == [(0.0, 3.25)]
    assert bounds(5.0) == [(0.0, 5.0)]


def test_audio_shorter_than_the_engine_minimum_has_no_windows():
    assert segment_windows(MIN_SEGMENT_SECONDS - 0.01) == ()
    assert bounds(MIN_SEGMENT_SECONDS) == [(0.0, MIN_SEGMENT_SECONDS)]


def test_the_fragment_is_written_like_the_engine_writes_it():
    window = SegmentWindow(2.5, 7.5)
    assert window.fragment() == "t=2.5,7.5"
    assert SegmentWindow(0.0, 10.0).of("/datos/pistas/000/000002.mp3") == (
        "/datos/pistas/000/000002.mp3#t=0,10"
    )


@pytest.mark.parametrize(("window", "hop"), [(0.5, 0.25), (5.0, 0.0), (5.0, -1.0)])
def test_unusable_windows_are_rejected(window: float, hop: float):
    with pytest.raises(WindowingError):
        Windowing(window, hop)
