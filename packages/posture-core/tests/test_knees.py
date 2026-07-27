"""Tests for `knee_flexion_deg`."""

from __future__ import annotations

import pytest
from builders import flat_resolver, metric_of, unclear, value_of, with_thresholds, without

from posture_core import KeypointName as K
from posture_core.metrics.knees import knee_flexion_deg as knee
from posture_core.status import MetricStatus
from posture_core.synthetic import Anthropometry
from posture_core.thresholds import DEFAULT_THRESHOLDS


@pytest.mark.parametrize(
    ("thigh", "shank", "expected"),
    [
        (0.0, 0.0, 180.0),  # standing, leg straight
        (85.0, 5.0, 100.0),  # sitting on a chair
        (90.0, 0.0, 90.0),  # a right angle
        (15.0, 165.0, 30.0),  # kneeling, shin folded back
    ],
)
def test_flexion_is_the_hip_knee_ankle_angle(thigh: float, shank: float, expected: float) -> None:
    """Constructed at known segment angles, so the expected value is arithmetic.

    The relationship is `180 - |thigh - shank|`, which the builder guarantees by making flexion
    emergent rather than an input — a figure cannot be asked for a knee angle that contradicts the
    direction of its own shin.
    """
    assert value_of(knee, thigh_deg=thigh, shank_deg=shank) == pytest.approx(expected, abs=1e-6)


def test_the_legacy_unbound_local_path_now_returns_a_value() -> None:
    """`checkKneeling` raised UnboundLocalError on a path where its result was never assigned.

    It could not even fail cleanly. Any input that produces a number here is an improvement on
    that, which is why this reads as a trivially simple test.
    """
    metric = metric_of(knee, thigh_deg=15.0, shank_deg=165.0)
    assert metric.value is not None
    assert "kneeling" in metric.detail


@pytest.mark.parametrize("scale", [0.5, 2.0])
def test_the_angle_does_not_move_with_leg_length(scale: float) -> None:
    default = Anthropometry()
    scaled = Anthropometry(thigh=default.thigh * scale, shank=default.shank * scale)
    assert value_of(knee, thigh_deg=85.0, shank_deg=5.0, body=scaled) == pytest.approx(
        100.0, abs=1e-6
    )


@pytest.mark.parametrize(
    ("thigh", "shank", "expected"),
    [
        (15.0, 165.0, "kneeling"),
        (120.0, 5.0, "tucked back sharply"),
        (85.0, 5.0, "comfortable seated angle"),
        (0.0, 0.0, "legs are extended"),
    ],
)
def test_the_description_reflects_the_configured_bands(
    thigh: float, shank: float, expected: str
) -> None:
    assert expected in metric_of(knee, thigh_deg=thigh, shank_deg=shank).detail


def test_the_bands_are_injected() -> None:
    strict = with_thresholds(knee_kneeling_max_deg=110.0)
    assert "kneeling" in metric_of(knee, thigh_deg=85.0, shank_deg=5.0, thresholds=strict).detail


def test_the_visible_leg_is_used_when_the_far_one_is_occluded() -> None:
    """The design change the real fixtures forced.

    Requiring both legs made every one of the eight fixtures abstain — in a lateral view the far
    leg is behind the torso and MediaPipe reports it below the visibility threshold, correctly. An
    engine that abstains on every photograph it was designed for is honest and useless.
    """
    metric = metric_of(knee, **unclear(K.RIGHT_KNEE, K.RIGHT_ANKLE, K.RIGHT_HIP))
    assert metric.value is not None
    assert "left knee" in metric.detail or "seated angle" in metric.detail


def test_the_more_confident_side_wins_rather_than_the_first_one() -> None:
    """So the near leg is chosen because it was seen better, not because of alphabetical order."""
    metric = metric_of(knee, **unclear(K.LEFT_KNEE, visibility=0.55))
    assert metric.value is not None
    assert metric.confidence is not None
    assert metric.confidence > 0.55


def test_both_legs_missing_produces_a_gap_naming_them() -> None:
    metric = metric_of(knee, **without(K.LEFT_KNEE, K.RIGHT_KNEE))
    assert metric.value is None
    assert metric.status is MetricStatus.INSUFFICIENT_KEYPOINTS
    assert "left knee" in metric.detail
    assert "right knee" in metric.detail


def test_both_legs_unclear_abstains_as_low_confidence() -> None:
    metric = metric_of(knee, **unclear(K.LEFT_KNEE, K.RIGHT_KNEE))
    assert metric.status is MetricStatus.LOW_CONFIDENCE


def test_a_collapsed_shank_abstains_rather_than_reporting_zero() -> None:
    metric = metric_of(knee, body=Anthropometry(shank=0.0))
    assert metric.value is None
    assert metric.status is MetricStatus.UNDEFINED_GEOMETRY


def test_a_two_dimensional_backend_abstains() -> None:
    assert knee(flat_resolver(), DEFAULT_THRESHOLDS).value is None
