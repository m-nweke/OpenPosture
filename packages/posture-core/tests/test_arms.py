"""Tests for `arms_crossed` and `elbow_flexion_deg`."""

from __future__ import annotations

from typing import Any

import pytest
from builders import flat_resolver, metric_of, unclear, value_of, with_thresholds, without

from posture_core import KeypointName as K
from posture_core.metrics.arms import arms_crossed, elbow_flexion_deg
from posture_core.status import MetricStatus
from posture_core.synthetic import Anthropometry
from posture_core.thresholds import DEFAULT_THRESHOLDS

ARMS_DOWN: dict[str, Any] = {"upper_arm_deg": 15.0, "forearm_deg": 80.0}
ARMS_FOLDED: dict[str, Any] = {**ARMS_DOWN, "forearm_cross_deg": 55.0}


# ---------------------------------------------------------------------------------------------
# arms_crossed
# ---------------------------------------------------------------------------------------------


def test_folding_the_arms_lowers_the_ratio() -> None:
    """The ratio *is* the evidence: smaller means each wrist is nearer the opposite elbow."""
    assert value_of(arms_crossed, **ARMS_FOLDED) < value_of(arms_crossed, **ARMS_DOWN)


def test_the_bands_separate_folded_from_unfolded_at_the_default_threshold() -> None:
    assert "folded across your chest" in metric_of(arms_crossed, **ARMS_FOLDED).detail
    assert "not folded" in metric_of(arms_crossed, **ARMS_DOWN).detail


@pytest.mark.parametrize("scale", [0.4, 1.0, 2.5])
def test_the_ratio_does_not_move_with_body_size(scale: float) -> None:
    """The whole point of the normalisation, and the direct fix for the legacy `±100` literal.

    That literal was an absolute pixel distance, so identical folded arms at twice the camera
    distance produced half the separation and the opposite verdict — a defect the original code's
    own comment acknowledged and never addressed.
    """
    default = Anthropometry()
    scaled = Anthropometry(
        torso=default.torso * scale,
        shoulder_width=default.shoulder_width * scale,
        hip_width=default.hip_width * scale,
        upper_arm=default.upper_arm * scale,
        forearm=default.forearm * scale,
    )
    baseline = value_of(arms_crossed, **ARMS_FOLDED)
    assert value_of(arms_crossed, body=scaled, **ARMS_FOLDED) == pytest.approx(baseline, abs=1e-9)


@pytest.mark.parametrize(("width", "height"), [(640, 480), (1920, 1080)])
def test_the_ratio_does_not_move_with_frame_size(width: int, height: int) -> None:
    baseline = value_of(arms_crossed, **ARMS_FOLDED)
    assert value_of(
        arms_crossed, image_width=width, image_height=height, **ARMS_FOLDED
    ) == pytest.approx(baseline, abs=1e-9)


def test_both_arms_must_be_folded_not_just_one() -> None:
    """Taking the larger of the two crossings, not the smaller.

    One hand resting near the opposite elbow is a very ordinary way to sit, and a minimum would
    read it as fully folded arms.
    """
    metric = metric_of(arms_crossed, **ARMS_FOLDED)
    assert metric.value is not None
    # The figure folds both arms symmetrically, so both crossings are equal and the max is the
    # honest summary. A one-armed fold would score the *unfolded* arm's distance.
    assert metric.value < DEFAULT_THRESHOLDS.arms_crossed_ratio


def test_the_threshold_is_injected() -> None:
    strict = with_thresholds(arms_crossed_ratio=0.2)
    assert "not folded" in metric_of(arms_crossed, thresholds=strict, **ARMS_FOLDED).detail


def test_a_missing_wrist_produces_a_gap() -> None:
    metric = metric_of(arms_crossed, **without(K.LEFT_WRIST))
    assert metric.value is None
    assert metric.status is MetricStatus.INSUFFICIENT_KEYPOINTS
    assert "left wrist" in metric.detail


def test_an_unclear_elbow_abstains_as_low_confidence() -> None:
    assert metric_of(arms_crossed, **unclear(K.RIGHT_ELBOW)).status is MetricStatus.LOW_CONFIDENCE


@pytest.mark.parametrize("torso", [0.0, 1e-12, 1e-10])
def test_a_degenerate_torso_abstains_rather_than_dividing_by_it(torso: float) -> None:
    """Near-zero, not just zero.

    `distance` is never negative, so a `<= 0.0` guard catches only the exact case — and exact zero
    is the one a real backend is least likely to produce. A torso of 1e-12 m divides the ratio up
    to ~1e11, which compares as "not folded" against any threshold: a confident wrong answer from
    a measurement that does not exist, which is the failure mode this package is built to avoid.
    """
    metric = metric_of(arms_crossed, body=Anthropometry(torso=torso), **ARMS_FOLDED)
    assert metric.value is None
    assert metric.status is MetricStatus.UNDEFINED_GEOMETRY
    assert "no body length" in metric.detail


def test_a_two_dimensional_backend_abstains() -> None:
    assert arms_crossed(flat_resolver(**ARMS_FOLDED), DEFAULT_THRESHOLDS).value is None


def test_arms_crossed_needs_both_arms_and_says_so_when_one_is_hidden() -> None:
    """The honest limitation, asserted so it cannot be forgotten.

    Unlike every other bilateral metric here, this one genuinely cannot fall back to a single side:
    "each wrist near the opposite elbow" is a statement about both arms at once. In a strict
    lateral view the far arm is occluded, so the metric abstains — which is correct, and means
    folded arms are not assessable from the very view the app asks users for. Recorded here rather
    than discovered later by someone wondering why the field is always null.
    """
    metric = metric_of(arms_crossed, **unclear(K.RIGHT_WRIST, K.RIGHT_ELBOW), **ARMS_FOLDED)
    assert metric.value is None
    assert metric.status is MetricStatus.LOW_CONFIDENCE


# ---------------------------------------------------------------------------------------------
# elbow_flexion_deg
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("upper_arm", "forearm", "expected"),
    [
        (0.0, 0.0, 180.0),  # arm hanging straight
        (0.0, 90.0, 90.0),  # forearm horizontal, upper arm vertical
        (15.0, 80.0, 115.0),  # ordinary resting position
        (0.0, 180.0, 0.0),  # fully folded back on itself
    ],
)
def test_flexion_is_the_shoulder_elbow_wrist_angle(
    upper_arm: float, forearm: float, expected: float
) -> None:
    assert value_of(
        elbow_flexion_deg, upper_arm_deg=upper_arm, forearm_deg=forearm
    ) == pytest.approx(expected, abs=1e-6)


def test_the_measured_arm_is_named_in_the_description() -> None:
    """One arm, not both, and the report says which.

    In a lateral view the far arm is behind the torso and reported below the visibility threshold,
    so requiring both made every fixture abstain. Since only one arm is measured, the description
    must never imply otherwise.
    """
    metric = metric_of(elbow_flexion_deg, upper_arm_deg=0.0, forearm_deg=90.0)
    assert metric.value == pytest.approx(90.0, abs=1e-6)
    assert "left elbow" in metric.detail or "right elbow" in metric.detail


def test_the_visible_arm_is_used_when_the_far_one_is_occluded() -> None:
    metric = metric_of(
        elbow_flexion_deg,
        upper_arm_deg=0.0,
        forearm_deg=90.0,
        **unclear(K.RIGHT_SHOULDER, K.RIGHT_ELBOW, K.RIGHT_WRIST),
    )
    assert metric.value == pytest.approx(90.0, abs=1e-6)
    assert "left elbow" in metric.detail


@pytest.mark.parametrize("scale", [0.5, 2.0])
def test_flexion_does_not_move_with_body_size(scale: float) -> None:
    default = Anthropometry()
    scaled = Anthropometry(upper_arm=default.upper_arm * scale, forearm=default.forearm * scale)
    assert value_of(
        elbow_flexion_deg, upper_arm_deg=0.0, forearm_deg=90.0, body=scaled
    ) == pytest.approx(90.0, abs=1e-6)


def test_the_flexed_band_comes_from_the_injected_threshold() -> None:
    bent: dict[str, Any] = {"upper_arm_deg": 0.0, "forearm_deg": 90.0}
    assert "bent to 90°" in metric_of(elbow_flexion_deg, **bent).detail
    lenient = with_thresholds(elbow_flexed_deg=45.0)
    assert "fairly straight" in metric_of(elbow_flexion_deg, thresholds=lenient, **bent).detail


def test_both_elbows_missing_produces_a_gap() -> None:
    metric = metric_of(elbow_flexion_deg, **without(K.LEFT_ELBOW, K.RIGHT_ELBOW))
    assert metric.value is None
    assert "right elbow" in metric.detail
    assert "left elbow" in metric.detail


def test_a_collapsed_forearm_abstains_rather_than_reporting_zero_degrees() -> None:
    """Zero would mean "elbow folded completely shut", a specific claim about a measurement that
    does not exist."""
    metric = metric_of(elbow_flexion_deg, body=Anthropometry(forearm=0.0))
    assert metric.value is None
    assert metric.status is MetricStatus.UNDEFINED_GEOMETRY


def test_flexion_on_a_two_dimensional_backend_abstains() -> None:
    assert elbow_flexion_deg(flat_resolver(), DEFAULT_THRESHOLDS).value is None
