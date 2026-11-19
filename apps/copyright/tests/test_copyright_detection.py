"""La votación por desfase con listas de ventanas sintéticas, sin gestor."""

from __future__ import annotations

import random

import pytest

from songcheck.detection import (
    Neighbour,
    QueryWindow,
    Run,
    Span,
    VotingError,
    VotingParameters,
    aligned_runs,
    coverage,
    score_runs,
)

HOP = 2.5
WINDOW = 5.0
PARAMETERS = VotingParameters(hop_seconds=HOP, min_run_windows=2)
COPIED = 7
OTHER = 9
SIMILARITY = 0.9
SEED = 20261118
NOISE_SONGS = 100_000
NOISE_NEIGHBOURS = 5
NOISE_WINDOWS = 12


def window(position: int, *neighbours: Neighbour) -> QueryWindow:
    start = position * HOP
    return QueryWindow(start, start + WINDOW, neighbours)


def segment(song: int, original_window: int, similarity: float = SIMILARITY) -> Neighbour:
    start = original_window * HOP
    return Neighbour(song, start, start + WINDOW, similarity)


def unrelated(position: int) -> Neighbour:
    """Un vecino de una canción que no vuelve a aparecer en ninguna otra ventana."""
    return segment(1_000 + position, position % 3, 0.6)


def copy_of(song: int, positions: range, original_first: int) -> list[QueryWindow]:
    return [
        window(position, segment(song, original_first + index), unrelated(position))
        for index, position in enumerate(positions)
    ]


def summary(run: Run) -> tuple[int, float, int, int]:
    return (run.song_id, run.offset_seconds, run.first_window, run.last_window)


def test_an_upload_without_windows_has_no_runs():
    assert aligned_runs([], PARAMETERS) == []


def test_an_exact_copy_is_one_run_over_every_window():
    windows = copy_of(COPIED, range(10), original_first=0)
    (run,) = aligned_runs(windows, PARAMETERS)
    assert summary(run) == (COPIED, 0.0, 0, 9)
    assert (run.upload, run.original) == (Span(0.0, 27.5), Span(0.0, 27.5))
    assert run.mean_similarity == pytest.approx(SIMILARITY)


def test_a_shifted_copy_reports_its_offset_and_both_spans():
    windows = [window(position, unrelated(position)) for position in range(4)]
    windows += copy_of(COPIED, range(4, 10), original_first=0)
    (run,) = aligned_runs(windows, PARAMETERS)
    assert summary(run) == (COPIED, 10.0, 4, 9)
    assert (run.upload, run.original) == (Span(10.0, 27.5), Span(0.0, 17.5))


def test_a_copy_off_the_catalog_grid_votes_two_neighbouring_offsets_and_stays_one_run():
    windows = [
        window(
            position,
            segment(COPIED, position, 0.9 - 0.1 * (position % 2)),
            segment(COPIED, position + 1, 0.8 + 0.1 * (position % 2)),
        )
        for position in range(8)
    ]
    (run,) = aligned_runs(windows, PARAMETERS)
    assert (run.first_window, run.last_window, run.windows) == (0, 7, 8)
    assert run.offset_seconds in (0.0, -HOP)


def test_the_original_has_to_advance_with_the_upload():
    hub = [window(position, segment(COPIED, 4, 0.95)) for position in range(6)]
    assert {run.windows for run in aligned_runs(hub, PARAMETERS)} == {2}
    repeated = [
        window(position, segment(COPIED, position, 0.9), segment(COPIED, 0, 0.99))
        for position in range(4)
    ]
    (run,) = aligned_runs(repeated, PARAMETERS)
    assert summary(run) == (COPIED, 0.0, 0, 3)
    assert run.similarities == (0.99, 0.99, 0.9, 0.9)


def test_votes_two_hops_apart_do_not_agree():
    windows = [window(position, segment(COPIED, 3 * position)) for position in range(6)]
    assert aligned_runs(windows, PARAMETERS) == []


def test_two_spliced_songs_are_two_runs_with_their_own_offsets():
    windows = copy_of(COPIED, range(5), original_first=3)
    windows += copy_of(OTHER, range(5, 10), original_first=0)
    runs = aligned_runs(windows, PARAMETERS)
    assert [summary(run) for run in runs] == [
        (COPIED, -7.5, 0, 4),
        (OTHER, 12.5, 5, 9),
    ]


def test_the_same_song_copied_twice_is_a_run_per_offset():
    windows = copy_of(COPIED, range(4), original_first=0)
    windows += copy_of(COPIED, range(4, 8), original_first=6)
    runs = aligned_runs(windows, PARAMETERS)
    assert sorted(summary(run) for run in runs) == [(COPIED, -5.0, 4, 7), (COPIED, 0.0, 0, 3)]


def test_noise_from_a_large_catalog_has_no_runs():
    generator = random.Random(SEED)
    windows = [
        window(
            position,
            *(
                segment(
                    generator.randrange(NOISE_SONGS), generator.randrange(11), generator.random()
                )
                for _ in range(NOISE_NEIGHBOURS)
            ),
        )
        for position in range(NOISE_WINDOWS)
    ]
    assert aligned_runs(windows, PARAMETERS) == []


def test_a_run_shorter_than_the_minimum_is_dropped():
    windows = [window(0), window(1, segment(COPIED, 0)), window(2)]
    assert aligned_runs(windows, VotingParameters(HOP, min_run_windows=2)) == []
    (run,) = aligned_runs(windows, VotingParameters(HOP, min_run_windows=1))
    assert summary(run) == (COPIED, 2.5, 1, 1)


def test_an_upload_shorter_than_the_minimum_run_asks_for_all_its_windows():
    windows = [QueryWindow(0.0, 3.0, (segment(COPIED, 4),))]
    (run,) = aligned_runs(windows, VotingParameters(HOP, min_run_windows=3))
    assert summary(run) == (COPIED, -10.0, 0, 0)
    assert run.upload == Span(0.0, 3.0)


def test_each_window_keeps_its_most_similar_aligned_neighbour():
    windows = [
        window(position, segment(COPIED, position, 0.7), segment(COPIED, position + 1, 0.95))
        for position in range(3)
    ]
    (run,) = aligned_runs(windows, PARAMETERS)
    assert run.similarities == (0.95, 0.95, 0.95)
    assert run.original == Span(2.5, 12.5)


def test_tied_offsets_pick_the_smaller_and_tied_songs_both_count():
    windows = [
        window(position, segment(COPIED, position), segment(COPIED, position + 4))
        for position in range(3)
    ]
    (run,) = aligned_runs(windows, PARAMETERS)
    assert run.offset_seconds == -10.0
    twins = [
        window(position, segment(OTHER, position), segment(COPIED, position))
        for position in range(3)
    ]
    assert [run.song_id for run in aligned_runs(twins, PARAMETERS)] == [COPIED, OTHER]


def test_the_order_of_the_neighbours_does_not_change_the_runs():
    windows = copy_of(COPIED, range(5), original_first=3)
    windows += copy_of(OTHER, range(5, 10), original_first=0)
    generator = random.Random(SEED)
    shuffled = []
    for position, item in enumerate(windows):
        neighbours = [*item.neighbours, unrelated(len(windows) + position)]
        generator.shuffle(neighbours)
        shuffled.append(QueryWindow(item.start, item.end, tuple(neighbours)))
    expected = aligned_runs(windows, PARAMETERS)
    assert [summary(run) for run in aligned_runs(shuffled, PARAMETERS)] == [
        summary(run) for run in expected
    ]


def test_coverage_is_over_what_both_recordings_could_share():
    assert coverage(3, upload_windows=71, original_windows=11) == pytest.approx(3 / 11)
    assert coverage(3, upload_windows=4, original_windows=11) == pytest.approx(3 / 4)
    assert coverage(12, upload_windows=20, original_windows=11) == 1.0
    assert coverage(0, upload_windows=0, original_windows=11) == 0.0


def test_matches_are_scored_flagged_by_the_threshold_and_sorted():
    windows = copy_of(COPIED, range(10), original_first=0)
    windows += copy_of(OTHER, range(10, 12), original_first=0)
    runs = aligned_runs(windows, PARAMETERS)
    matches = score_runs(runs, len(windows), {COPIED: 10, OTHER: 11}, threshold=0.5)
    assert [match.run.song_id for match in matches] == [COPIED, OTHER]
    copied, other = matches
    assert (copied.coverage, copied.score, copied.above_threshold) == (
        1.0,
        pytest.approx(SIMILARITY),
        True,
    )
    assert other.coverage == pytest.approx(2 / 11)
    assert other.score == pytest.approx(SIMILARITY * 2 / 11)
    assert not other.above_threshold


def test_a_run_of_a_song_without_its_window_count_is_a_key_error():
    runs = aligned_runs(copy_of(COPIED, range(3), 0), PARAMETERS)
    with pytest.raises(KeyError):
        score_runs(runs, 3, {}, threshold=0.5)


@pytest.mark.parametrize(("hop", "min_run"), [(0.0, 2), (-1.0, 2), (2.5, 0)])
def test_parameters_that_cannot_vote_are_rejected(hop: float, min_run: int):
    with pytest.raises(VotingError):
        VotingParameters(hop, min_run)
