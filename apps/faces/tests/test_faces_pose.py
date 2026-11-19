"""La pose estimada con cinco puntos, contra los puntos de una cara promedio girada en 3D y
proyectada: con la cara del modelo, la estimación es exacta. Con una cara real es
aproximada (ver el README del servicio)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from faces.detection import FaceLandmarks, Point
from faces.pose import (
    FRONTAL_NOSE_DROP,
    MOUTH_DEPTH_PER_EYE_TO_MOUTH,
    NOSE_DEPTH_PER_EYE_DISTANCE,
    NOSE_DEPTH_PER_EYE_TO_MOUTH,
    HeadPose,
    Pose,
    PoseLimits,
    estimate_head_pose,
    pose_of,
)

EYE_DISTANCE = NOSE_DEPTH_PER_EYE_TO_MOUTH / NOSE_DEPTH_PER_EYE_DISTANCE
MOUTH_WIDTH = 0.5
PIXELS = 100.0
CENTRE = np.array([320.0, 240.0])
EXACT = 0.01


def average_face() -> np.ndarray:
    """Ojo derecho, izquierdo, nariz, comisura derecha e izquierda (x a la derecha de la
    imagen, y hacia abajo, z hacia la cámara), en distancias de los ojos a la boca."""
    half_eyes, half_mouth = EYE_DISTANCE / 2, MOUTH_WIDTH / 2
    return np.array(
        [
            [-half_eyes, 0.0, 0.0],
            [half_eyes, 0.0, 0.0],
            [0.0, FRONTAL_NOSE_DROP, NOSE_DEPTH_PER_EYE_TO_MOUTH],
            [-half_mouth, 1.0, MOUTH_DEPTH_PER_EYE_TO_MOUTH],
            [half_mouth, 1.0, MOUTH_DEPTH_PER_EYE_TO_MOUTH],
        ]
    )


def landmarks_of(yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0) -> FaceLandmarks:
    """Los cinco puntos de la cara promedio cabeceada, girada de lado e inclinada, en ese
    orden, y proyectada sin perspectiva."""
    a, b, c = (math.radians(angle) for angle in (yaw, pitch, roll))
    turn = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    nod = np.array([[1, 0, 0], [0, math.cos(b), math.sin(b)], [0, -math.sin(b), math.cos(b)]])
    tilt = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    projected = (average_face() @ (tilt @ turn @ nod).T)[:, :2] * PIXELS + CENTRE
    return FaceLandmarks(*(Point(float(x), float(y)) for x, y in projected))


def test_a_frontal_face_has_no_rotation():
    head = estimate_head_pose(landmarks_of())
    assert (head.yaw, head.pitch, head.roll) == pytest.approx((0, 0, 0), abs=EXACT)


@pytest.mark.parametrize("yaw", [-35.0, -20.0, -5.0, 5.0, 20.0, 35.0])
def test_turning_alone_is_recovered_exactly(yaw: float):
    head = estimate_head_pose(landmarks_of(yaw=yaw))
    assert head.yaw == pytest.approx(yaw, abs=EXACT)
    assert head.roll == pytest.approx(0, abs=EXACT)


@pytest.mark.parametrize("pitch", [-30.0, -10.0, 10.0, 30.0])
def test_nodding_alone_is_recovered_exactly(pitch: float):
    head = estimate_head_pose(landmarks_of(pitch=pitch))
    assert head.pitch == pytest.approx(pitch, abs=EXACT)
    assert head.yaw == pytest.approx(0, abs=EXACT)


@pytest.mark.parametrize("roll", [-30.0, -10.0, 10.0, 30.0])
def test_tilting_alone_is_recovered_exactly(roll: float):
    head = estimate_head_pose(landmarks_of(roll=roll))
    assert head.roll == pytest.approx(roll, abs=EXACT)
    assert (head.yaw, head.pitch) == pytest.approx((0, 0), abs=EXACT)


def test_the_estimate_does_not_depend_on_the_tilt():
    """La inclinación se quita antes de medir el giro y el cabeceo."""
    upright = estimate_head_pose(landmarks_of(yaw=20, pitch=10))
    tilted = estimate_head_pose(landmarks_of(yaw=20, pitch=10, roll=25))
    assert (tilted.yaw, tilted.pitch) == pytest.approx((upright.yaw, upright.pitch), abs=EXACT)


@pytest.mark.parametrize(("yaw", "pitch", "roll"), [(20, 15, 8), (30, -20, 10), (-15, 10, -5)])
def test_combined_rotations_are_recovered(yaw: float, pitch: float, roll: float):
    """Cabecear cambia cuánto sobresale la nariz, y el giro de lado lo tiene en cuenta."""
    head = estimate_head_pose(landmarks_of(yaw=yaw, pitch=pitch, roll=roll))
    assert (head.yaw, head.pitch, head.roll) == pytest.approx((yaw, pitch, roll), abs=EXACT)


def test_signs_follow_the_person():
    """Girar hacia su izquierda corre la nariz a la derecha de la imagen; bajar la cabeza la
    corre hacia la boca."""
    assert estimate_head_pose(landmarks_of(yaw=20)).yaw > 0
    assert estimate_head_pose(landmarks_of(pitch=20)).pitch > 0


def test_degenerate_points_give_a_huge_angle_and_no_error():
    """La boca sobre la línea de los ojos no es una cara: el ángulo sale enorme y se rechaza."""
    right, left = Point(10, 10), Point(20, 10)
    head = estimate_head_pose(FaceLandmarks(right, left, Point(15, 15), right, left))
    assert abs(head.pitch) >= PoseLimits().max_pitch


LIMITS = PoseLimits(frontal_max_yaw=10, turned_min_yaw=15)


@pytest.mark.parametrize(
    ("yaw", "pose"),
    [
        (0, Pose.FRONT),
        (-10, Pose.FRONT),
        (12, None),
        (-14.9, None),
        (15, Pose.LEFT),
        (30, Pose.LEFT),
        (-15, Pose.RIGHT),
        (-30, Pose.RIGHT),
    ],
)
def test_the_covered_pose_depends_on_the_turn(yaw: float, pose: Pose | None):
    assert pose_of(HeadPose(yaw=yaw, pitch=0, roll=0), LIMITS) is pose
