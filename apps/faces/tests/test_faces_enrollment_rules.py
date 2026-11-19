"""Las reglas del registro sin HTTP ni gestor: el plan de poses, el nombre, el parecido con
la galería y el consentimiento."""

from __future__ import annotations

import pytest

from faces.consent import consent_text
from faces.enrollment import (
    COMPLETE_GUIDANCE,
    DISPLAY_NAME_MAX_LENGTH,
    POSE_GUIDANCE,
    Candidate,
    IdentityThresholds,
    InvalidDisplayNameError,
    Neighbour,
    PosePlan,
    Resemblance,
    best_per_person,
    normalized_display_name,
    resemblance_of,
    resemblance_per_person,
)
from faces.pose import Pose
from faces.quality import REJECTION_MESSAGES, RejectionReason

PLAN = PosePlan((Pose.FRONT, Pose.LEFT, Pose.RIGHT))


def test_the_plan_asks_for_its_poses_in_order():
    assert PLAN.remaining([]) == (Pose.FRONT, Pose.LEFT, Pose.RIGHT)
    assert PLAN.guidance([]) == POSE_GUIDANCE[Pose.FRONT]
    assert PLAN.remaining([Pose.FRONT]) == (Pose.LEFT, Pose.RIGHT)
    assert PLAN.guidance([Pose.FRONT]) == POSE_GUIDANCE[Pose.LEFT]


def test_poses_can_come_in_any_order():
    assert PLAN.remaining([Pose.RIGHT]) == (Pose.FRONT, Pose.LEFT)
    assert PLAN.is_complete([Pose.RIGHT, Pose.FRONT, Pose.LEFT])
    assert PLAN.guidance([Pose.RIGHT, Pose.FRONT, Pose.LEFT]) == COMPLETE_GUIDANCE


def test_a_plan_can_repeat_a_pose():
    plan = PosePlan((Pose.FRONT, Pose.FRONT))
    assert plan.remaining([Pose.FRONT]) == (Pose.FRONT,)
    assert plan.is_complete([Pose.FRONT, Pose.FRONT])


def test_a_pose_already_covered_is_rejected_with_the_next_step():
    rejection = PLAN.placement_rejection(Pose.FRONT, [Pose.FRONT])
    assert rejection is not None
    assert rejection.reason is RejectionReason.POSE_ALREADY_TAKEN
    assert rejection.message == (
        f"{REJECTION_MESSAGES[RejectionReason.POSE_ALREADY_TAKEN]} {POSE_GUIDANCE[Pose.LEFT]}"
    )


def test_an_unclear_pose_is_rejected_with_the_next_step():
    rejection = PLAN.placement_rejection(None, [Pose.FRONT, Pose.LEFT])
    assert rejection is not None
    assert rejection.reason is RejectionReason.POSE_UNCLEAR
    assert rejection.message.endswith(POSE_GUIDANCE[Pose.RIGHT])


def test_a_missing_pose_is_placed():
    assert PLAN.placement_rejection(Pose.LEFT, [Pose.FRONT]) is None
    assert PLAN.accepted_message([Pose.FRONT, Pose.LEFT]) == (
        f"Toma girada a tu izquierda lista. {POSE_GUIDANCE[Pose.RIGHT]}"
    )


def test_a_shot_after_the_plan_is_complete_is_rejected():
    rejection = PLAN.placement_rejection(Pose.FRONT, [Pose.FRONT, Pose.LEFT, Pose.RIGHT])
    assert rejection is not None
    assert rejection.message.endswith(COMPLETE_GUIDANCE)


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("  Ana   María  ", "Ana María"),
        ("José", "José"),
        ("Ñandú 👋", "Ñandú 👋"),
    ],
)
def test_names_are_normalized(raw: str, name: str):
    assert normalized_display_name(raw) == name


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "x" * (DISPLAY_NAME_MAX_LENGTH + 1), "Ana‮airaM", "Ana\x00", "Ana​"],
)
def test_names_that_cannot_be_shown_are_refused(raw: str):
    with pytest.raises(InvalidDisplayNameError):
        normalized_display_name(raw)


def test_the_longest_name_fits():
    assert normalized_display_name("x" * DISPLAY_NAME_MAX_LENGTH) == "x" * DISPLAY_NAME_MAX_LENGTH


def test_each_person_counts_with_their_best_embedding():
    neighbours = [Neighbour("a", 0.4), Neighbour("b", 0.7), Neighbour("a", 0.9)]
    assert best_per_person(neighbours) == [Candidate("a", 0.9), Candidate("b", 0.7)]
    assert best_per_person([]) == []


def test_resemblance_averages_over_the_shots():
    shots = [
        [Neighbour("a", 0.8), Neighbour("b", 0.6)],
        [Neighbour("a", 0.6), Neighbour("b", 0.2)],
    ]
    assert resemblance_per_person(shots) == [Candidate("a", 0.7), Candidate("b", 0.4)]


def test_a_person_missing_from_a_shot_counts_with_its_last_neighbour():
    """Una cota superior: quien no salió entre los vecinos se parece a lo sumo como el último."""
    shots = [[Neighbour("a", 0.9), Neighbour("b", 0.5)], [Neighbour("b", 0.3)]]
    assert resemblance_per_person(shots) == [Candidate("a", 0.6), Candidate("b", 0.4)]


THRESHOLDS = IdentityThresholds(session=0.363, match=0.45, duplicate=0.5, lookalike=0.45)


@pytest.mark.parametrize(
    ("score", "resemblance"),
    [
        (0.9, Resemblance.SAME_PERSON),
        (0.5, Resemblance.SAME_PERSON),
        (0.49, Resemblance.LOOKALIKE),
        (0.45, Resemblance.LOOKALIKE),
        (0.44, None),
    ],
)
def test_resemblance_bands(score: float, resemblance: Resemblance | None):
    assert resemblance_of(score, THRESHOLDS) is resemblance


def test_lookalike_equal_to_duplicate_disables_the_warning():
    thresholds = IdentityThresholds(duplicate=0.5, lookalike=0.5)
    assert resemblance_of(0.49, thresholds) is None


def test_the_consent_says_what_is_kept_and_for_how_long():
    consent = consent_text(retention_days=30)
    assert "30 días" in consent.text
    assert "fotos" in consent.not_stored
    assert all(item in consent.text for item in consent.stored_data)


def test_the_consent_version_follows_its_text():
    assert consent_text(30).version == consent_text(30).version
    assert consent_text(30).version != consent_text(7).version
