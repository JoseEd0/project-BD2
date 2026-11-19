"""Cada control de calidad con su mensaje: primero como función de las medidas, después con
transformaciones de las fotos de `fixtures/` analizadas por los modelos reales."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from faces_support import (
    GROUP_PHOTO,
    OTHER_PORTRAIT,
    SINGLE_PORTRAIT,
    crew_member,
    jpeg,
    portrait_image,
    selfie,
    solid,
)
from PIL import Image, ImageEnhance, ImageFilter

from faces import FacePipeline
from faces.detection import BoundingBox, DetectedFace, FaceLandmarks, Point
from faces.pose import HeadPose
from faces.quality import (
    REJECTION_MESSAGES,
    QualityConfig,
    Rejection,
    RejectionReason,
    ShotMeasurements,
    first_rejection,
    laplacian_variance,
    outside_fraction,
    subject_faces,
)
from faces.shots import AcceptedShot, RejectedShot, analyze_query, analyze_shot

CONFIG = QualityConfig()
GOOD = ShotMeasurements(
    face_width=200,
    outside_fraction=0,
    brightness=130,
    overexposed_fraction=0,
    sharpness=300,
    head=HeadPose(yaw=0, pitch=0, roll=0),
)
BLUR_RADIUS = 4
DARK_FACTOR = 0.25
BRIGHT_FACTOR = 1.5
TILT_DEGREES = 35
GRAY = 128
# Cortar la foto por debajo del 80 % del alto del rostro deja la barbilla fuera.
CUT_AT_FACE_HEIGHT = 0.8
BACKGROUND_FACE_SCALE = 0.3


def reason_of(measured: ShotMeasurements) -> RejectionReason | None:
    rejection = first_rejection(measured, CONFIG)
    return None if rejection is None else rejection.reason


def head(yaw: float = 0, pitch: float = 0, roll: float = 0) -> HeadPose:
    return HeadPose(yaw=yaw, pitch=pitch, roll=roll)


def test_every_reason_has_a_message():
    assert set(REJECTION_MESSAGES) == set(RejectionReason)
    assert all(message.endswith(".") for message in REJECTION_MESSAGES.values())


def test_guidance_follows_the_message():
    rejection = Rejection.of(RejectionReason.POSE_UNCLEAR, "Mira directo a la cámara.")
    assert rejection.message == (
        f"{REJECTION_MESSAGES[RejectionReason.POSE_UNCLEAR]} Mira directo a la cámara."
    )


def test_a_good_shot_passes():
    assert first_rejection(GOOD, CONFIG) is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"outside_fraction": 0.05}, RejectionReason.FACE_CUT_OFF),
        ({"face_width": 79}, RejectionReason.FACE_TOO_SMALL),
        ({"brightness": 49}, RejectionReason.TOO_DARK),
        ({"brightness": 201}, RejectionReason.OVEREXPOSED),
        ({"overexposed_fraction": 0.2}, RejectionReason.OVEREXPOSED),
        ({"sharpness": 39}, RejectionReason.BLURRY),
        ({"head": head(roll=16)}, RejectionReason.HEAD_TILTED),
        ({"head": head(roll=-16)}, RejectionReason.HEAD_TILTED),
        ({"head": head(yaw=41)}, RejectionReason.TURNED_TOO_FAR),
        ({"head": head(yaw=-41)}, RejectionReason.TURNED_TOO_FAR),
        ({"head": head(pitch=26)}, RejectionReason.LOOKING_DOWN),
        ({"head": head(pitch=-26)}, RejectionReason.LOOKING_UP),
    ],
)
def test_each_check_rejects_past_its_threshold(changes: dict[str, object], reason: RejectionReason):
    rejection = first_rejection(replace(GOOD, **changes), CONFIG)  # type: ignore[arg-type]
    assert rejection == Rejection(reason, REJECTION_MESSAGES[reason])


@pytest.mark.parametrize(
    "changes",
    [
        {"face_width": 80},
        {"brightness": 50},
        {"brightness": 200},
        {"sharpness": 40},
        {"head": head(yaw=40, pitch=25, roll=15)},
        {"head": head(yaw=-40, pitch=-25, roll=-15)},
    ],
)
def test_the_thresholds_themselves_pass(changes: dict[str, object]):
    assert first_rejection(replace(GOOD, **changes), CONFIG) is None  # type: ignore[arg-type]


def test_light_is_judged_before_sharpness_and_pose():
    """Una foto oscura también pierde bordes: lo que hay que corregir es la luz."""
    dark_and_soft = replace(GOOD, brightness=20, sharpness=5, head=head(yaw=60))
    assert reason_of(dark_and_soft) is RejectionReason.TOO_DARK
    assert reason_of(replace(GOOD, sharpness=5, head=head(yaw=60))) is RejectionReason.BLURRY


def test_outside_fraction_of_a_box():
    assert outside_fraction(BoundingBox(10, 10, 50, 50), 100, 100) == 0
    assert outside_fraction(BoundingBox(-25, 0, 50, 40), 100, 100) == pytest.approx(0.5)
    assert outside_fraction(BoundingBox(80, 80, 40, 40), 100, 100) == pytest.approx(0.75)
    assert outside_fraction(BoundingBox(200, 200, 10, 10), 100, 100) == 1
    assert outside_fraction(BoundingBox(0, 0, 0, 10), 100, 100) == 1


def test_laplacian_variance_drops_with_blur():
    rng = np.random.default_rng(90)
    texture = rng.integers(0, 256, size=(64, 64), dtype=np.uint8)
    blurred = np.asarray(Image.fromarray(texture).filter(ImageFilter.GaussianBlur(2)))
    assert laplacian_variance(np.full((64, 64), 7, dtype=np.uint8)) == 0
    assert laplacian_variance(blurred) < laplacian_variance(texture) / 10


def face_of_width(width: float) -> DetectedFace:
    point = Point(0, 0)
    return DetectedFace(BoundingBox(0, 0, width, width), FaceLandmarks(*[point] * 5), 0.95)


def test_people_in_the_background_do_not_count():
    faces = [face_of_width(200), face_of_width(100), face_of_width(99)]
    assert subject_faces(faces, CONFIG) == faces[:2]
    assert subject_faces([], CONFIG) == []


def rejection_for(pipeline: FacePipeline, image: Image.Image) -> Rejection:
    result = analyze_shot(pipeline, jpeg(image), CONFIG)
    assert isinstance(result, RejectedShot), f"se aceptó: {result}"
    rejection = result.rejection
    assert rejection.message == REJECTION_MESSAGES[rejection.reason]
    single_face = rejection.reason not in {RejectionReason.NO_FACE, RejectionReason.MULTIPLE_FACES}
    assert (result.measurements is not None) == single_face
    return rejection


@pytest.mark.parametrize("name", [SINGLE_PORTRAIT, OTHER_PORTRAIT])
def test_a_selfie_is_accepted_with_its_measurements(pipeline: FacePipeline, name: str):
    result = analyze_shot(pipeline, jpeg(selfie(name)), CONFIG)
    assert isinstance(result, AcceptedShot)
    measured = result.measurements
    assert measured.face_width > CONFIG.min_face_width
    assert abs(measured.head.yaw) < CONFIG.pose.frontal_max_yaw
    assert result.embedding.shape == (128,)


def test_a_blurred_shot_asks_to_hold_still(pipeline: FacePipeline):
    blurred = selfie().filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
    assert rejection_for(pipeline, blurred).reason is RejectionReason.BLURRY


def test_a_dark_shot_asks_for_light(pipeline: FacePipeline):
    dark = ImageEnhance.Brightness(selfie()).enhance(DARK_FACTOR)
    assert rejection_for(pipeline, dark).reason is RejectionReason.TOO_DARK


def test_an_overexposed_shot_asks_for_less_light(pipeline: FacePipeline):
    bright = ImageEnhance.Brightness(selfie()).enhance(BRIGHT_FACTOR)
    assert rejection_for(pipeline, bright).reason is RejectionReason.OVEREXPOSED


@pytest.mark.parametrize("name", [SINGLE_PORTRAIT, OTHER_PORTRAIT])
def test_a_tilted_head_asks_to_straighten_it(pipeline: FacePipeline, name: str):
    tilted = selfie(name).rotate(TILT_DEGREES, resample=Image.Resampling.BICUBIC, fillcolor=GRAY)
    assert rejection_for(pipeline, tilted).reason is RejectionReason.HEAD_TILTED


def test_a_far_away_face_asks_to_come_closer(pipeline: FacePipeline):
    """El retrato original tiene el rostro de 65 px: menos de lo que SFace necesita."""
    far = portrait_image(SINGLE_PORTRAIT)
    assert rejection_for(pipeline, far).reason is RejectionReason.FACE_TOO_SMALL


def test_a_cut_face_asks_to_step_back(pipeline: FacePipeline):
    image = selfie(OTHER_PORTRAIT)
    (face,) = pipeline.detect(pipeline.decode(jpeg(image)))
    bottom = round(face.box.y + face.box.height * CUT_AT_FACE_HEIGHT)
    cut = image.crop((0, 0, image.width, bottom))
    assert rejection_for(pipeline, cut).reason is RejectionReason.FACE_CUT_OFF


def test_a_group_photo_asks_for_one_face(pipeline: FacePipeline):
    group = portrait_image(GROUP_PHOTO)
    assert rejection_for(pipeline, group).reason is RejectionReason.MULTIPLE_FACES


def test_a_photo_without_a_face_says_so(pipeline: FacePipeline):
    assert rejection_for(pipeline, solid(640, 480, GRAY)).reason is RejectionReason.NO_FACE


def test_someone_small_in_the_background_does_not_block_the_shot(pipeline: FacePipeline):
    image = selfie()
    passerby = crew_member("other")
    small = passerby.resize(
        (
            round(passerby.width * BACKGROUND_FACE_SCALE),
            round(passerby.height * BACKGROUND_FACE_SCALE),
        )
    )
    image.paste(small, (0, image.height - small.height))
    faces = pipeline.detect(pipeline.decode(jpeg(image)))
    assert len(faces) == 2
    assert isinstance(analyze_shot(pipeline, jpeg(image), CONFIG), AcceptedShot)


def test_a_query_describes_every_face_big_enough(pipeline: FacePipeline):
    group = portrait_image(GROUP_PHOTO)
    faces = analyze_query(pipeline, jpeg(group), CONFIG, max_faces=3)
    assert len(faces) == 3
    assert all(face.embedding is None for face in faces)
    zoomed = group.resize((group.width * 3, group.height * 3))
    described = analyze_query(pipeline, jpeg(zoomed), CONFIG, max_faces=10)
    assert len(described) == 7
    assert all(face.embedding is not None for face in described)
