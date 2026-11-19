"""El seguimiento de rostros entre cuadros: IoU, emparejamiento, identidades que caducan y
rastros que se pierden."""

from __future__ import annotations

import pytest

from faces.detection import BoundingBox
from faces.tracking import (
    FIRST_TRACK_ID,
    Identity,
    Track,
    TrackBook,
    TrackingConfig,
    identified,
    iou,
    match_boxes,
)

CONFIG = TrackingConfig(min_iou=0.3, verify_seconds=2.0, lost_seconds=1.0)
LEFT = BoundingBox(0, 0, 100, 100)
RIGHT = BoundingBox(300, 0, 100, 100)
NOW = 1000.0
COLLINS = Identity("Eileen Collins", 0.8, NOW)


def shifted(box: BoundingBox, dx: float) -> BoundingBox:
    return BoundingBox(box.x + dx, box.y, box.width, box.height)


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (LEFT, LEFT, 1.0),
        (LEFT, RIGHT, 0.0),
        (LEFT, shifted(LEFT, 50), 50 * 100 / (2 * 100 * 100 - 50 * 100)),
        (LEFT, BoundingBox(100, 0, 100, 100), 0.0),
        (LEFT, BoundingBox(0, 0, 0, 0), 0.0),
    ],
)
def test_iou(first: BoundingBox, second: BoundingBox, expected: float):
    assert iou(first, second) == pytest.approx(expected)
    assert iou(second, first) == pytest.approx(expected)


def test_boxes_match_one_to_one_by_the_largest_overlap():
    previous = [LEFT, shifted(LEFT, 40)]
    current = [shifted(LEFT, 35), shifted(LEFT, 5), RIGHT]
    assert match_boxes(previous, current, CONFIG.min_iou) == [1, 0, None]


def test_without_previous_boxes_nothing_matches():
    assert match_boxes([], [LEFT, RIGHT], CONFIG.min_iou) == [None, None]
    assert match_boxes([LEFT], [], CONFIG.min_iou) == []


def test_an_empty_book_opens_a_track_per_face():
    assignments = TrackBook().assign([LEFT, RIGHT], NOW, CONFIG)
    assert [assignment.track_id for assignment in assignments] == [FIRST_TRACK_ID, 2]
    assert all(assignment.identity is None for assignment in assignments)
    book = TrackBook().updated(assignments, NOW, CONFIG)
    assert book.next_id == 3
    assert [track.box for track in book.tracks] == [LEFT, RIGHT]


def test_a_moving_face_keeps_its_track_and_identity_until_it_expires():
    book = TrackBook((Track(7, LEFT, NOW, COLLINS),), next_id=8)
    (moved,) = book.assign([shifted(LEFT, 10)], NOW + 1, CONFIG)
    assert (moved.track_id, moved.identity) == (7, COLLINS)
    (stale,) = book.assign([shifted(LEFT, 10)], NOW + CONFIG.verify_seconds, CONFIG)
    assert (stale.track_id, stale.identity) == (7, None)


def test_a_track_lost_for_a_moment_is_kept_and_then_forgotten():
    book = TrackBook((Track(1, LEFT, NOW, COLLINS),), next_id=2)
    assignments = book.assign([RIGHT], NOW + 0.5, CONFIG)
    kept = book.updated(assignments, NOW + 0.5, CONFIG)
    assert sorted(track.track_id for track in kept.tracks) == [1, 2]
    later = kept.updated(kept.assign([RIGHT], NOW + 1.5, CONFIG), NOW + 1.5, CONFIG)
    assert [track.track_id for track in later.tracks] == [2]


def test_an_identity_is_stored_with_its_track():
    (assignment,) = TrackBook().assign([LEFT], NOW, CONFIG)
    book = TrackBook().updated([identified(assignment, COLLINS)], NOW, CONFIG)
    assert book.tracks[0].identity == COLLINS
