"""Tests for `heel_contact_m` — the capability the original project never delivered."""

from __future__ import annotations

import pytest
from builders import flat_resolver, frame, metric_of, unclear, value_of, with_thresholds, without

from posture_core import KeypointName as K
from posture_core import Landmark, PoseFrame
from posture_core.metrics.feet import heel_contact_m as heel
from posture_core.resolver import KeypointResolver
from posture_core.status import MetricStatus
from posture_core.thresholds import DEFAULT_THRESHOLDS


def frame_with_foot(*, heel_rise_m: float) -> PoseFrame:
    """A figure whose heels sit `heel_rise_m` above their toes.

    Built by displacing the heel directly rather than through a joint angle, because there is no
    ankle-flexion parameter in the stick figure and adding one would be a lot of machinery for a
    single vertical offset. y is DOWN, so raising the heel means *decreasing* its y.
    """
    original = frame()
    landmarks = dict(original.landmarks)
    for heel_name, toe_name in (
        (K.LEFT_HEEL, K.LEFT_FOOT_INDEX),
        (K.RIGHT_HEEL, K.RIGHT_FOOT_INDEX),
    ):
        toe = landmarks[toe_name]
        existing = landmarks[heel_name]
        assert toe.y_world is not None
        landmarks[heel_name] = Landmark(
            x=existing.x,
            y=existing.y,
            visibility=existing.visibility,
            presence=existing.presence,
            x_world=existing.x_world,
            y_world=toe.y_world - heel_rise_m,
            z_world=existing.z_world,
        )
    return PoseFrame(
        landmarks=landmarks,
        image_width=original.image_width,
        image_height=original.image_height,
        backend="synthetic",
        inference_ms=0.0,
    )


def heel_value(*, heel_rise_m: float) -> float:
    metric = heel(
        KeypointResolver(frame_with_foot(heel_rise_m=heel_rise_m), DEFAULT_THRESHOLDS),
        DEFAULT_THRESHOLDS,
    )
    assert metric.value is not None
    return metric.value


@pytest.mark.parametrize("rise", [-0.03, 0.0, 0.02, 0.12])
def test_the_measurement_is_the_heels_height_above_its_own_toe(rise: float) -> None:
    """Signed, and the sign is not clamped.

    Negative — toe above heel — is what someone resting back on their heel looks like. Clamping it
    to zero would turn a real and unusual foot position into a report of a perfectly ordinary one.
    """
    assert heel_value(heel_rise_m=rise) == pytest.approx(rise, abs=1e-9)


def test_the_sign_convention_is_not_inverted() -> None:
    """The single easiest thing to get backwards in this module.

    World `y` points DOWN, so a raised heel has a *smaller* y than its toe. An implementation with
    the subtraction the other way round would report every planted foot as dangling and every
    dangling one as planted — plausible numbers, opposite verdicts, no error anywhere.
    """
    assert heel_value(heel_rise_m=0.10) > 0
    assert heel_value(heel_rise_m=-0.10) < 0


def test_a_raised_heel_is_reported_as_unsupported() -> None:
    """What the legacy engine could not do at all.

    Its 18-point COCO schema had no foot landmarks, so its feet check was a tautology that returned
    "on the floor" for a fixture whose subject's feet visibly dangle (FINDINGS §2.3).
    """
    metric = heel(
        KeypointResolver(frame_with_foot(heel_rise_m=0.12), DEFAULT_THRESHOLDS), DEFAULT_THRESHOLDS
    )
    assert "not resting on the floor" in metric.detail
    assert "12 cm" in metric.detail


def test_a_flat_foot_is_reported_as_supported() -> None:
    metric = heel(
        KeypointResolver(frame_with_foot(heel_rise_m=0.01), DEFAULT_THRESHOLDS), DEFAULT_THRESHOLDS
    )
    assert "flat and supported" in metric.detail


def test_the_tolerance_is_injected() -> None:
    strict = with_thresholds(heel_contact_tolerance_m=0.005)
    metric = heel(KeypointResolver(frame_with_foot(heel_rise_m=0.01), strict), strict)
    assert "not resting on the floor" in metric.detail


def test_the_visible_foot_is_used_when_the_far_one_is_occluded() -> None:
    metric = metric_of(heel, **unclear(K.RIGHT_HEEL, K.RIGHT_FOOT_INDEX))
    assert metric.value is not None
    assert "left foot" in metric.detail or "left heel" in metric.detail


def test_a_missing_heel_on_both_sides_produces_a_gap() -> None:
    metric = metric_of(heel, **without(K.LEFT_HEEL, K.RIGHT_HEEL))
    assert metric.value is None
    assert metric.status is MetricStatus.INSUFFICIENT_KEYPOINTS
    assert "left heel" in metric.detail


def test_feet_out_of_frame_are_reported_as_such_rather_than_as_grounded() -> None:
    """The exact shape of the original's failure: feet it could not see were reported as fine.

    Low *presence* rather than low visibility, so the gap says the feet are outside the picture —
    which tells the user to step back rather than to improve the lighting.
    """
    metric = metric_of(
        heel,
        confidence=dict.fromkeys(
            (K.LEFT_HEEL, K.RIGHT_HEEL, K.LEFT_FOOT_INDEX, K.RIGHT_FOOT_INDEX), (0.9, 0.1)
        ),
    )
    assert metric.value is None
    assert metric.status is MetricStatus.INSUFFICIENT_KEYPOINTS
    assert "could not see" in metric.detail


def test_a_two_dimensional_backend_abstains() -> None:
    """A 2D backend has no metres, and a heel height in normalised pixels means nothing."""
    assert heel(flat_resolver(), DEFAULT_THRESHOLDS).value is None


def test_the_metric_reports_metres() -> None:
    assert metric_of(heel).unit == "m"


def test_the_value_does_not_move_with_frame_size() -> None:
    assert value_of(heel, image_width=1920, image_height=1080) == pytest.approx(
        value_of(heel), abs=1e-9
    )


def test_the_more_raised_foot_is_reported_when_both_are_usable() -> None:
    """Not the more confident one.

    Confidence is the right tiebreak for a joint angle, where either side answers the same question
    and the better-seen one answers it more reliably. It is the wrong tiebreak here: one
    unsupported foot is worth reporting, and choosing by confidence would let a well-seen planted
    foot mask a dangling one.
    """
    original = frame_with_foot(heel_rise_m=0.0)
    landmarks = dict(original.landmarks)

    # Left foot clearly raised but less confidently seen; right foot flat and seen perfectly.
    left_toe = landmarks[K.LEFT_FOOT_INDEX]
    assert left_toe.y_world is not None
    for name in (K.LEFT_HEEL, K.LEFT_FOOT_INDEX):
        existing = landmarks[name]
        landmarks[name] = Landmark(
            x=existing.x,
            y=existing.y,
            visibility=0.6,
            presence=0.99,
            x_world=existing.x_world,
            y_world=(left_toe.y_world - 0.14) if name is K.LEFT_HEEL else existing.y_world,
            z_world=existing.z_world,
        )

    frame = PoseFrame(
        landmarks=landmarks,
        image_width=original.image_width,
        image_height=original.image_height,
        backend="synthetic",
        inference_ms=0.0,
    )
    metric = heel(KeypointResolver(frame, DEFAULT_THRESHOLDS), DEFAULT_THRESHOLDS)
    assert metric.value == pytest.approx(0.14, abs=1e-6)
    assert "left" in metric.detail


def test_confidence_still_decides_when_only_one_foot_is_usable() -> None:
    """The lateral-view case, which is the common one."""
    metric = metric_of(heel, **unclear(K.RIGHT_HEEL, K.RIGHT_FOOT_INDEX))
    assert metric.value is not None
    assert "left" in metric.detail
