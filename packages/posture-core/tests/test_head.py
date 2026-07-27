"""Tests for `craniovertebral_angle_deg`."""

from __future__ import annotations

import pytest
from builders import flat_resolver, metric_of, unclear, value_of, with_thresholds, without

from posture_core import KeypointName as K
from posture_core.metrics.head import craniovertebral_angle_deg as cva
from posture_core.status import MetricStatus
from posture_core.synthetic import Anthropometry, Facing
from posture_core.thresholds import DEFAULT_THRESHOLDS


@pytest.mark.parametrize(
    ("trunk", "neck", "expected"),
    [
        (0.0, 0.0, 90.0),  # head directly above C7
        (0.0, 30.0, 60.0),  # head pushed forward
        (0.0, 45.0, 45.0),  # forward head posture
        (20.0, 20.0, 50.0),  # trunk and neck both contribute
        (0.0, -20.0, 110.0),  # head behind the shoulders
    ],
)
def test_the_angle_is_measured_from_the_horizontal(
    trunk: float, neck: float, expected: float
) -> None:
    """The clinical definition: 90° minus how far the C7-to-ear line has tipped from vertical.

    Constructing the figure at known angles makes the expected value arithmetic rather than
    judgement, which is the whole reason the builder exists.
    """
    assert value_of(cva, trunk_deg=trunk, neck_deg=neck) == pytest.approx(expected, abs=1e-6)


def test_smaller_is_worse_which_is_backwards_from_every_other_threshold() -> None:
    """Stated as a test because it is the single easiest thing in the package to invert.

    Every other angular threshold here means "more is worse". This one does not, and an
    implementation that got it the wrong way round would report the healthiest posture as the
    worst while producing entirely plausible numbers.
    """
    upright = value_of(cva, neck_deg=0.0)
    jutting = value_of(cva, neck_deg=40.0)
    assert jutting < upright


def test_trunk_lean_reduces_the_angle_even_with_the_head_aligned_to_the_spine() -> None:
    """Deliberate, not a leak.

    The reference is gravity, not the torso. Forward head posture is defined against the vertical,
    and the load on the neck depends on where the head is in space rather than on where it sits
    relative to a spine that is itself tilted.
    """
    aligned_upright = value_of(cva, trunk_deg=0.0, neck_deg=0.0)
    aligned_leaning = value_of(cva, trunk_deg=30.0, neck_deg=0.0)
    assert aligned_leaning < aligned_upright


def test_the_measurement_is_independent_of_facing_direction() -> None:
    assert value_of(cva, neck_deg=35.0, facing=Facing.RIGHT) == pytest.approx(
        value_of(cva, neck_deg=35.0, facing=Facing.LEFT), abs=1e-6
    )


@pytest.mark.parametrize("scale", [0.5, 1.0, 2.0])
def test_the_angle_does_not_move_with_body_size(scale: float) -> None:
    default = Anthropometry()
    scaled = Anthropometry(
        torso=default.torso * scale,
        neck=default.neck * scale,
        shoulder_width=default.shoulder_width * scale,
        head_width=default.head_width * scale,
    )
    assert value_of(cva, neck_deg=30.0, body=scaled) == pytest.approx(60.0, abs=1e-6)


@pytest.mark.parametrize(
    ("neck", "expected"),
    [
        (0.0, "balanced over your shoulders"),
        (36.0, "slightly forward"),
        (45.0, "well forward"),
    ],
)
def test_the_description_reflects_the_configured_bands(neck: float, expected: str) -> None:
    assert expected in metric_of(cva, neck_deg=neck).detail


def test_the_cutoff_comes_from_the_injected_thresholds() -> None:
    lenient = with_thresholds(cva_forward_head_deg=40.0, cva_borderline_deg=45.0)
    assert "well forward" in metric_of(cva, neck_deg=45.0).detail
    assert "balanced" in metric_of(cva, neck_deg=45.0, thresholds=lenient).detail


def test_a_missing_ear_produces_a_gap() -> None:
    metric = metric_of(cva, **without(K.LEFT_EAR))
    assert metric.value is None
    assert metric.status is MetricStatus.INSUFFICIENT_KEYPOINTS
    assert "left ear" in metric.detail


def test_an_unclear_ear_abstains_as_low_confidence() -> None:
    metric = metric_of(cva, **unclear(K.RIGHT_EAR))
    assert metric.status is MetricStatus.LOW_CONFIDENCE


def test_an_unknown_facing_abstains() -> None:
    metric = metric_of(cva, **without(K.NOSE))
    assert metric.value is None
    assert metric.status is MetricStatus.UNDEFINED_GEOMETRY


def test_a_head_collapsed_onto_the_shoulders_abstains_rather_than_reporting_zero() -> None:
    """Zero would mean "head maximally forward", a confident and specific claim about a
    measurement that does not exist."""
    metric = metric_of(cva, body=Anthropometry(neck=0.0))
    assert metric.value is None
    assert metric.status is MetricStatus.UNDEFINED_GEOMETRY


def test_a_two_dimensional_backend_abstains() -> None:
    metric = cva(flat_resolver(neck_deg=30.0), DEFAULT_THRESHOLDS)
    assert metric.value is None
    assert "world coordinates" in metric.detail


def test_the_facing_gap_names_the_facing_landmarks_not_the_ears() -> None:
    """Same defect as `trunk`, same fix — the ears and shoulders were never the problem."""
    metric = metric_of(cva, **without(K.NOSE))
    assert metric.value is None
    assert "which way you are facing" in metric.detail
    assert set(metric.inputs) == {K.NOSE, K.NECK}
    assert K.LEFT_EAR not in metric.inputs
