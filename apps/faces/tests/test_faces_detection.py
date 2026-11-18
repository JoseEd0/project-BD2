"""La escala con que entra cada imagen al detector, las filas de YuNet y la regla de un
solo rostro; nada de esto necesita los modelos."""

from __future__ import annotations

import numpy as np
import pytest

from faces import (
    BoundingBox,
    DetectedFace,
    FaceLandmarks,
    MultipleFacesError,
    NoFaceError,
    PipelineConfig,
    Point,
    detection_scale,
    only_face,
)
from faces.detection import face_from_row

CONFIG = PipelineConfig(detection_min_side=320, detection_max_side=640)
YUNET_ROW = np.array(
    [10, 20, 30, 40, 11, 21, 12, 22, 13, 23, 14, 24, 15, 25, 0.95], dtype=np.float32
)


def face_with_area(side: float) -> DetectedFace:
    corner = Point(0, 0)
    return DetectedFace(BoundingBox(0, 0, side, side), FaceLandmarks(*[corner] * 5), 0.9)


@pytest.mark.parametrize(
    ("width", "height", "scale"),
    [
        (4000, 3000, 640 / 4000),
        (3000, 4000, 640 / 4000),
        (640, 480, 1.0),
        (320, 100, 1.0),
        (250, 250, 320 / 250),
        (64, 32, 320 / 64),
    ],
)
def test_the_longer_side_ends_between_the_two_bounds(width: int, height: int, scale: float):
    assert detection_scale(width, height, CONFIG) == pytest.approx(scale)


def test_a_row_is_brought_back_to_the_original_image():
    face = face_from_row(YUNET_ROW, scale=0.5)

    assert face.box == BoundingBox(20, 40, 60, 80)
    assert face.landmarks.right_eye == Point(22, 42)
    assert face.landmarks.left_mouth_corner == Point(30, 50)
    assert face.score == pytest.approx(0.95)


def test_a_face_turns_back_into_the_row_it_came_from():
    assert np.allclose(face_from_row(YUNET_ROW, scale=1.0).as_row(), YUNET_ROW)


def test_the_only_face_is_returned():
    face = face_with_area(10)

    assert only_face([face]) is face


def test_no_face_is_an_error_when_one_is_required():
    with pytest.raises(NoFaceError):
        only_face([])


def test_two_faces_are_an_error_when_one_is_required():
    with pytest.raises(MultipleFacesError, match="2 rostros"):
        only_face([face_with_area(10), face_with_area(5)])
