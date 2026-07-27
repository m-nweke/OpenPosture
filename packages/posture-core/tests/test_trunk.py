"""Tests for `trunk_inclination_deg`, the project's central measurement.

Every assertion is against a figure built at a known angle, so these are statements about geometry
rather than about somebody's reading of a photograph.
"""

from __future__ import annotations

import pytest
from builders import (
    flat_resolver,
    metric_of,
    unclear,
    value_of,
    with_thresholds,
    without,
)

from posture_core import KeypointName as K
from posture_core.metrics.trunk import NAME
from posture_core.metrics.trunk import trunk_inclination_deg as trunk
from posture_core.status import MetricStatus
from posture_core.synthetic import Anthropometry, Facing, View
from posture_core.thresholds import DEFAULT_THRESHOLDS


@pytest.mark.parametrize("requested", [-30.0, -12.0, 0.0, 8.0, 25.0, 45.0])
def test_the_measured_angle_is_the_angle_the_figure_was_built_at(requested: float) -> None:
    assert value_of(trunk, trunk_deg=requested) == pytest.approx(requested, abs=1e-6)


def test_forward_lean_is_positive_and_reclining_is_negative() -> None:
    """The sign is the advice.

    A slouch and a recline are different postures needing different suggestions, and an
    implementation that took `abs()` somewhere would collapse them into one verdict while still
    producing a plausible number.
    """
    assert value_of(trunk, trunk_deg=25.0) > 0
    assert value_of(trunk, trunk_deg=-25.0) < 0


def test_the_verdict_does_not_depend_on_which_way_the_subject_faces() -> None:
    """The legacy engine's defining bug, reproduced as a guard.

    `API/config` declared landmark 16 as the left ear, `posture_image.py` commented it as the
    right, and the laterality flag keyed off that misreading decided whether to apply
    `degrees = 180 - degrees`. Spine classification therefore came out backwards for subjects
    facing one direction. Here facing is derived from the nose, so there is nothing to invert.
    """
    facing_right = value_of(trunk, trunk_deg=30.0, facing=Facing.RIGHT)
    facing_left = value_of(trunk, trunk_deg=30.0, facing=Facing.LEFT)
    assert facing_right == pytest.approx(facing_left, abs=1e-6)
    assert facing_right == pytest.approx(30.0, abs=1e-6)


@pytest.mark.parametrize("scale", [0.4, 1.0, 2.5])
def test_the_angle_does_not_move_with_body_size(scale: float) -> None:
    """FINDINGS §2.6, fixed by construction rather than by normalisation.

    The legacy engine compared raw pixel distances against literal thresholds, so identical posture
    at twice the camera distance produced a different verdict. Measuring an angle in metric world
    space means scale never enters the calculation at all.
    """
    default = Anthropometry()
    stretched = Anthropometry(
        torso=default.torso * scale,
        neck=default.neck * scale,
        shoulder_width=default.shoulder_width * scale,
        hip_width=default.hip_width * scale,
    )
    assert value_of(trunk, trunk_deg=27.0, body=stretched) == pytest.approx(27.0, abs=1e-6)


@pytest.mark.parametrize(("width", "height"), [(640, 480), (1920, 1080), (720, 1280)])
def test_the_angle_does_not_move_with_frame_size_or_aspect_ratio(width: int, height: int) -> None:
    assert value_of(trunk, trunk_deg=22.0, image_width=width, image_height=height) == pytest.approx(
        22.0, abs=1e-6
    )


# ---------------------------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("angle", "expected"),
    [
        (3.0, "upright"),
        (15.0, "slightly hunched"),
        (35.0, "pronounced slouch"),
        (-30.0, "leaning 30° back"),
    ],
)
def test_the_description_reflects_the_configured_bands(angle: float, expected: str) -> None:
    assert expected in metric_of(trunk, trunk_deg=angle).detail


def test_the_bands_come_from_the_injected_thresholds() -> None:
    """Not from literals in the metric, which is the point of OP-24.

    A stricter deployment can call 12° a slouch without anyone editing this module.
    """
    strict = with_thresholds(trunk_upright_deg=5.0, trunk_slouch_deg=12.0)
    assert "pronounced slouch" in metric_of(trunk, trunk_deg=15.0, thresholds=strict).detail
    assert "slightly hunched" in metric_of(trunk, trunk_deg=15.0).detail


# ---------------------------------------------------------------------------------------------
# Abstention
# ---------------------------------------------------------------------------------------------


def test_a_missing_hip_produces_a_gap_rather_than_a_number() -> None:
    """The replacement for "Straight back position."

    The legacy engine returned None here and its caller rendered that as good posture. What the
    user must now receive is a specific statement about what could not be seen.
    """
    metric = metric_of(trunk, **without(K.LEFT_HIP))
    assert metric.value is None
    assert metric.status is MetricStatus.INSUFFICIENT_KEYPOINTS
    assert "left hip" in metric.detail


def test_an_unclear_shoulder_abstains_as_low_confidence() -> None:
    metric = metric_of(trunk, **unclear(K.RIGHT_SHOULDER))
    assert metric.value is None
    assert metric.status is MetricStatus.LOW_CONFIDENCE


def test_an_unknown_facing_abstains_rather_than_reporting_an_unsigned_lean() -> None:
    """Half an answer is worse than none when the missing half decides the advice.

    Without a facing direction the magnitude of the lean is still computable — and reporting it
    would present a recline as a slouch.
    """
    metric = metric_of(trunk, trunk_deg=30.0, **without(K.NOSE))
    assert metric.value is None
    assert metric.status is MetricStatus.UNDEFINED_GEOMETRY
    assert "which way you are facing" in metric.detail


def test_a_frontal_view_still_recovers_the_lean_because_the_landmarks_are_three_dimensional() -> (
    None
):
    """Measured, not assumed — and it corrects an assumption the plan made.

    The intuition is that a slump photographed head-on projects into depth and vanishes, so the
    sagittal angle should read near zero. That is true of a *2D* engine, and it was the reasoning
    behind `view_confidence`. It is not true here: world landmarks are metric 3D, so the facing
    axis comes out along the view direction and the full 30° is recovered.

    The consequence is worth stating plainly, because it changes what OP-31 is for. A frontal view
    does not make this measurement impossible — it makes it *less reliable*, since MediaPipe's
    depth estimate is far weaker along the camera axis than across it. So `view_confidence` should
    downgrade confidence on a frontal shot rather than suppress the finding outright.
    """
    metric = metric_of(trunk, trunk_deg=30.0, view=View.FRONTAL)
    assert metric.value is not None
    assert metric.value == pytest.approx(30.0, abs=1e-6)


def test_nothing_raises_when_every_input_is_dropped() -> None:
    """Degradation must be quiet at the boundary and loud in the report, never an exception."""
    metric = metric_of(trunk, **without(K.LEFT_HIP, K.RIGHT_HIP, K.LEFT_SHOULDER, K.RIGHT_SHOULDER))
    assert metric.status is MetricStatus.INSUFFICIENT_KEYPOINTS


def test_the_metric_records_what_it_measured_from() -> None:
    metric = metric_of(trunk, trunk_deg=10.0)
    assert metric.name == NAME
    assert metric.unit == "deg"
    assert set(metric.inputs) == {K.LEFT_HIP, K.RIGHT_HIP, K.LEFT_SHOULDER, K.RIGHT_SHOULDER}


def test_confidence_is_carried_through_from_the_weakest_input() -> None:
    metric = metric_of(trunk, **unclear(K.RIGHT_HIP, visibility=0.6))
    assert metric.confidence == pytest.approx(0.6)


def test_a_two_dimensional_backend_abstains_instead_of_guessing_in_pixels() -> None:
    """ADR-0005's image-space fallback is not implemented, and says so rather than quietly
    computing an angle in a space stretched by the aspect ratio."""
    metric = trunk(flat_resolver(trunk_deg=20.0), DEFAULT_THRESHOLDS)
    assert metric.value is None
    assert "world coordinates" in metric.detail


def test_coincident_landmarks_abstain_with_undefined_geometry() -> None:
    """A torso of zero length gives a vector with no direction, which is not an angle of zero.

    Real backends produce this: a fully occluded joint can collapse two landmarks onto the same
    point. The distinction matters because zero degrees means "sitting perfectly upright", which
    is a specific and reassuring claim to make about a measurement that does not exist.
    """
    metric = metric_of(trunk, trunk_deg=20.0, body=Anthropometry(torso=0.0))
    assert metric.value is None
    assert metric.status is MetricStatus.UNDEFINED_GEOMETRY
    assert "overlap" in metric.detail
