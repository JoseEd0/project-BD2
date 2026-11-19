"""El generador de remixes y sus distorsiones: misma semilla, mismos remixes; la verdad dice
dónde suena cada fragmento; cada distorsión hace lo que dice."""

import io
import random

import numpy as np
import pytest
import soundfile
from benchmarks.remixes.distortions import (
    low_passed,
    mp3_bytes,
    pitch,
    power,
    reencoded,
    snr_db,
    speed,
    with_gain,
    with_noise,
)
from benchmarks.remixes.generator import (
    RemixError,
    RemixPlan,
    RemixShape,
    Span,
    frames,
    plan_remix,
    render,
)

SAMPLE_RATE = 8_000
SHAPE = RemixShape(
    min_fragments=1,
    max_fragments=3,
    min_fragment_seconds=5.0,
    max_fragment_seconds=20.0,
    crossfade_seconds=1.0,
    source_seconds=30.0,
)
SONGS = tuple(range(100, 110))
SEED = 7
SONG_STEP = 10.0
# Dos muestras de diferencia entre lo esperado y lo leído: el redondeo de cada tramo a muestras.
TIME_TOLERANCE = 2 / SAMPLE_RATE / SONG_STEP
PLANS = 50
TOLERANCE_DB = 0.2


def song_audio(song_id: int) -> np.ndarray:
    """Una pista sintética que dice de qué canción (las decenas) y de qué instante (décimas de
    segundo) es cada muestra."""
    seconds = np.arange(frames(SHAPE.source_seconds, SAMPLE_RATE)) / SAMPLE_RATE
    return (SONG_STEP * (song_id - SONGS[0]) + seconds / SONG_STEP).astype(np.float32)


def plans(seed: int) -> list[RemixPlan]:
    rng = random.Random(seed)
    return [plan_remix(rng, SONGS, SHAPE, catalogued=True) for _ in range(PLANS)]


def tone(frequency: float, seconds: float = 2.0, rate: int = SAMPLE_RATE) -> np.ndarray:
    time = np.arange(frames(seconds, rate)) / rate
    return (0.5 * np.sin(2 * np.pi * frequency * time)).astype(np.float32)


def dominant_frequency(samples: np.ndarray, rate: int = SAMPLE_RATE) -> float:
    spectrum = np.abs(np.fft.rfft(samples))
    return float(np.fft.rfftfreq(len(samples), 1 / rate)[int(np.argmax(spectrum))])


def test_the_same_seed_gives_the_same_remixes_and_another_seed_other_ones():
    assert plans(SEED) == plans(SEED)
    assert plans(SEED) != plans(SEED + 1)


def test_every_plan_respects_the_shape():
    for plan in plans(SEED):
        songs = [fragment.song_id for fragment in plan.fragments]
        assert SHAPE.min_fragments <= len(songs) <= SHAPE.max_fragments
        assert len(set(songs)) == len(songs)
        assert plan.fragments[0].upload.start == 0.0
        for fragment in plan.fragments:
            assert SHAPE.min_fragment_seconds <= fragment.original.seconds
            assert fragment.original.seconds <= SHAPE.max_fragment_seconds
            assert fragment.original.start >= 0.0
            assert fragment.original.end <= SHAPE.source_seconds
            assert fragment.upload.seconds == pytest.approx(fragment.original.seconds)
        for before, after in zip(plan.fragments, plan.fragments[1:], strict=False):
            overlap = before.upload.end - after.upload.start
            assert overlap == pytest.approx(SHAPE.crossfade_seconds)


def test_the_truth_says_which_song_sounds_where_and_from_where_in_the_original():
    for plan in plans(SEED):
        remix = render(plan, song_audio, SAMPLE_RATE, SHAPE.crossfade_seconds)
        assert len(remix) == frames(plan.seconds, SAMPLE_RATE)
        for fragment in plan.fragments:
            inside = np.linspace(
                fragment.upload.start + SHAPE.crossfade_seconds,
                fragment.upload.end - SHAPE.crossfade_seconds,
                7,
            )[1:-1]
            for moment in inside:
                expected = song_audio(fragment.song_id)[
                    frames(fragment.original.start, SAMPLE_RATE)
                    + frames(moment - fragment.upload.start, SAMPLE_RATE)
                ]
                got = remix[frames(moment, SAMPLE_RATE)]
                assert got == pytest.approx(expected, abs=TIME_TOLERANCE)


def test_a_crossfade_keeps_the_level_where_two_fragments_meet():
    plan = plan_remix(random.Random(SEED), SONGS, SHAPE, catalogued=True)
    constant = render(plan, lambda _song: np.ones(10**6), SAMPLE_RATE, SHAPE.crossfade_seconds)
    assert np.allclose(constant[frames(SHAPE.crossfade_seconds, SAMPLE_RATE) :], 1.0, atol=1e-3)


def test_a_single_fragment_remix_and_its_seconds():
    shape = RemixShape(1, 1, 6.0, 6.0, 1.0, 30.0)
    plan = plan_remix(random.Random(SEED), SONGS, shape, catalogued=False)
    assert len(plan.fragments) == 1
    assert plan.seconds == pytest.approx(6.0)
    assert not plan.catalogued


@pytest.mark.parametrize(
    "shape",
    [
        (0, 1, 5.0, 10.0, 1.0, 30.0),
        (3, 1, 5.0, 10.0, 1.0, 30.0),
        (1, 2, 5.0, 40.0, 1.0, 30.0),
        (1, 2, 5.0, 10.0, 3.0, 30.0),
    ],
)
def test_a_shape_that_cannot_make_remixes_is_rejected(shape):
    with pytest.raises(RemixError):
        RemixShape(*shape)


def test_a_plan_needs_distinct_songs_for_its_fragments():
    shape = RemixShape(3, 3, 5.0, 10.0, 1.0, 30.0)
    with pytest.raises(RemixError):
        plan_remix(random.Random(SEED), (1, 1, 2), shape, catalogued=True)


def test_a_source_shorter_than_the_fragment_is_an_error():
    plan = plan_remix(random.Random(SEED), SONGS, SHAPE, catalogued=True)
    with pytest.raises(RemixError):
        render(plan, lambda _song: np.ones(SAMPLE_RATE), SAMPLE_RATE, SHAPE.crossfade_seconds)


def test_a_span_scales_with_the_duration():
    assert Span(2.0, 4.0).scaled(0.5) == Span(1.0, 2.0)


@pytest.mark.parametrize("target", [20.0, 10.0])
def test_noise_lands_at_the_requested_snr(target):
    clean = tone(440.0, seconds=5.0)
    noisy = with_noise(clean, target, np.random.default_rng(SEED))
    assert snr_db(clean, noisy) == pytest.approx(target, abs=TOLERANCE_DB)


def test_noise_is_reproducible_with_its_seed():
    clean = tone(440.0)
    first = with_noise(clean, 10.0, np.random.default_rng(SEED))
    second = with_noise(clean, 10.0, np.random.default_rng(SEED))
    assert np.array_equal(first, second)


def test_volume_changes_the_power_by_its_decibels():
    clean = tone(440.0)
    quieter = with_gain(clean, -12.0)
    assert 10 * np.log10(power(quieter) / power(clean)) == pytest.approx(-12.0, abs=1e-6)


def test_low_pass_removes_what_is_above_the_cutoff_and_keeps_the_rest():
    low, high = tone(500.0), tone(3_000.0)
    filtered = low_passed(low + high, SAMPLE_RATE, 1_000.0)
    assert power(filtered - low) < 1e-6 * power(high)


@pytest.mark.parametrize(("rate", "scale"), [(1.05, 1 / 1.05), (0.95, 1 / 0.95)])
def test_speed_changes_the_tempo_but_not_the_pitch(rate, scale):
    distortion = speed("tempo", rate)
    clean = tone(440.0)
    stretched = distortion.apply(clean, SAMPLE_RATE, np.random.default_rng(SEED))
    assert distortion.time_scale == pytest.approx(scale)
    assert len(stretched) == pytest.approx(len(clean) * scale, rel=0.01)
    assert dominant_frequency(stretched) == pytest.approx(440.0, rel=0.01)


@pytest.mark.parametrize("semitones", [1.0, -1.0])
def test_pitch_moves_the_frequency_a_semitone_and_keeps_the_duration(semitones):
    distortion = pitch("tono", semitones)
    clean = tone(440.0)
    shifted = distortion.apply(clean, SAMPLE_RATE, np.random.default_rng(SEED))
    assert distortion.time_scale == 1.0
    assert len(shifted) == len(clean)
    assert dominant_frequency(shifted) == pytest.approx(440.0 * 2 ** (semitones / 12), rel=0.01)


@pytest.mark.parametrize("rate", [44_100, 22_050])
def test_mp3_is_encoded_at_the_requested_bitrate(rate):
    seconds = 10.0
    noise = np.random.default_rng(SEED).normal(0, 0.1, frames(seconds, rate)).astype(np.float32)
    encoded = mp3_bytes(noise, rate, 64.0)
    assert soundfile.info(io.BytesIO(encoded)).format == "MP3"
    assert len(encoded) * 8 / seconds / 1000 == pytest.approx(64.0, rel=0.03)


def test_mp3_reencoding_keeps_the_length_and_most_of_the_signal():
    clean = tone(440.0, rate=44_100)
    decoded = reencoded(clean, 44_100, 64.0)
    assert len(decoded) == len(clean)
    assert dominant_frequency(decoded, 44_100) == pytest.approx(440.0, rel=0.01)
    assert not np.array_equal(decoded, clean)
