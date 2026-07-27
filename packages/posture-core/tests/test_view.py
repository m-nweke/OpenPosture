"""Tests for `view_confidence` — the check the original app never performed."""

from __future__ import annotations

import pytest
from builders import metric_of, unclear, value_of, with_thresholds, without

from posture_core import KeypointName as K
from posture_core.metrics.view import view_confidence as view
from posture_core.metrics.view import view_confidence_factor as factor
from posture_core.status import MetricStatus
from posture_core.synthetic import Anthropometry, View
from posture_core.thresholds import DEFAULT_THRESHOLDS as T


def test_a_side_on_view_collapses_the_shoulder_span_to_nothing() -> None:
    """One shoulder directly behind the other, so the projected separation is zero."""
    assert value_of(view, view=View.LATERAL) == pytest.approx(0.0, abs=1e-9)


def test_a_frontal_view_shows_the_subjects_real_proportions() -> None:
    assert value_of(view, view=View.FRONTAL) > T.frontal_view_min_ratio


@pytest.mark.parametrize(
    ("camera", "expected"),
    [
        (View.LATERAL, "from the side"),
        (View.FRONTAL, "from the front"),
    ],
)
def test_the_description_names_the_camera_angle(camera: View, expected: str) -> None:
    assert expected in metric_of(view, view=camera).detail


def test_an_intermediate_angle_is_described_as_such_rather_than_forced_into_a_bucket() -> None:
    """The gap between the two thresholds is a real state, not an oversight.

    Most real photographs are neither perfectly side-on nor perfectly face-on, and pretending
    otherwise would make the confidence factor jump discontinuously across a boundary that nothing
    physical corresponds to.
    """
    lenient = with_thresholds(lateral_view_max_ratio=0.05, frontal_view_min_ratio=0.9)
    assert (
        "between side-on and front-on"
        in metric_of(view, view=View.FRONTAL, thresholds=lenient).detail
    )


@pytest.mark.parametrize("scale", [0.5, 2.0])
def test_the_ratio_does_not_move_with_body_size(scale: float) -> None:
    default = Anthropometry()
    scaled = Anthropometry(
        torso=default.torso * scale,
        shoulder_width=default.shoulder_width * scale,
        hip_width=default.hip_width * scale,
    )
    baseline = value_of(view, view=View.FRONTAL)
    assert value_of(view, view=View.FRONTAL, body=scaled) == pytest.approx(baseline, abs=1e-9)


def test_a_missing_shoulder_produces_a_gap() -> None:
    metric = metric_of(view, **without(K.LEFT_SHOULDER))
    assert metric.value is None
    assert metric.status is MetricStatus.INSUFFICIENT_KEYPOINTS


def test_an_unclear_hip_abstains_as_low_confidence() -> None:
    assert metric_of(view, **unclear(K.LEFT_HIP)).status is MetricStatus.LOW_CONFIDENCE


@pytest.mark.parametrize("angle", [View.LATERAL, View.FRONTAL])
@pytest.mark.parametrize("torso", [0.0, 1e-13, 1e-12])
def test_a_degenerate_torso_abstains_rather_than_dividing_by_it(torso: float, angle: View) -> None:
    """Near-zero, not just zero — the same guard `arms_crossed` needed for the same division.

    `distance` is never negative, so a `<= 0.0` test catches only the exact case, and exact zero is
    the one a real backend is least likely to produce.

    Both views, because only the frontal one shows the damage. Side-on the shoulders coincide, so
    the numerator is zero and a tiny torso still yields 0.0 — the right answer by luck. Face-on the
    numerator is real and the ratio runs to ~3.8e10, which reads as "photographed from the front"
    against any threshold. That does not stay local: the ratio feeds `view_confidence_factor`, so
    one degenerate torso pins every sagittal finding in the report to the confidence floor.
    """
    metric = metric_of(view, body=Anthropometry(torso=torso), view=angle)
    assert metric.value is None
    assert metric.status is MetricStatus.UNDEFINED_GEOMETRY


def test_the_guard_is_a_divide_by_zero_check_not_a_plausibility_floor() -> None:
    """Worth stating outright, because the constant is named for metres and used here on pixels.

    `MIN_LENGTH` is 1e-9, and this metric divides in *image* space, so the guard trips at about
    1e-12 m of world torso rather than at anything a person would call short. A 5e-12 m torso is
    physically absurd and still measures. That is the shared constant behaving as intended — it
    exists to stop a division by nothing, not to judge whether a body is a plausible size — and it
    is preferable to a second, pixel-scaled epsilon here that would drift from the one in
    `arms_crossed` the first time either was retuned.
    """
    assert metric_of(view, body=Anthropometry(torso=5e-12)).status is MetricStatus.OK


# ---------------------------------------------------------------------------------------------
# The confidence factor
# ---------------------------------------------------------------------------------------------


def test_a_side_on_photo_earns_full_confidence() -> None:
    assert factor(0.0, T) == pytest.approx(1.0)
    assert factor(T.lateral_view_max_ratio, T) == pytest.approx(1.0)


def test_a_face_on_photo_is_downgraded_not_discarded() -> None:
    """The correction to the plan, stated as a test.

    The plan treats a frontal view as fatal to sagittal metrics, on the reasoning that a slump
    photographed head-on projects to nothing. That holds for a 2D engine. With metric 3D world
    landmarks the full lean is recovered — measured at exactly 30.0° for a figure built at 30°.

    What weakens is the *depth* estimate the measurement rests on, so the right response is
    reduced confidence, not a discarded finding. A quieter answer is more useful than no answer
    and more honest than a loud one.
    """
    downgraded = factor(T.frontal_view_min_ratio, T)
    assert 0.0 < downgraded < 1.0


def test_the_factor_falls_monotonically_as_the_view_turns_toward_the_camera() -> None:
    ratios = [0.0, 0.2, 0.35, 0.45, 0.55, 0.8]
    factors = [factor(ratio, T) for ratio in ratios]
    assert factors == sorted(factors, reverse=True)


def test_the_ramp_is_continuous_at_both_thresholds() -> None:
    """No cliff at a boundary that nothing physical corresponds to.

    A discontinuity would mean two photographs a degree apart could report visibly different
    confidence, which reads to a user as instability in the product rather than in the camera
    angle.
    """
    epsilon = 1e-6
    assert factor(T.lateral_view_max_ratio + epsilon, T) == pytest.approx(1.0, abs=1e-5)
    assert factor(T.frontal_view_min_ratio - epsilon, T) == pytest.approx(
        factor(T.frontal_view_min_ratio, T), abs=1e-5
    )


def test_an_unassessable_view_gets_the_floor_rather_than_the_benefit_of_the_doubt() -> None:
    """Assuming the best angle when we could not check is exactly the optimism this package
    exists to remove."""
    assert factor(None, T) == pytest.approx(factor(1.0, T))


def test_the_factor_never_leaves_its_stated_range() -> None:
    for ratio in (-1.0, 0.0, 0.3, 0.45, 0.6, 5.0):
        assert 0.4 <= factor(ratio, T) <= 1.0
