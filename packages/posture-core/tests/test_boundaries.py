"""Boundary tests: the behaviour at each threshold, from either side, by an epsilon.

Every rule in this package is a comparison, and a comparison has exactly two ways to be wrong that
no other test catches: the wrong operator (`<` where `<=` belongs) and the wrong side. Both produce
entirely plausible output everywhere except within a hair's breadth of the line.

The pattern throughout is the same — construct the figure at `threshold ± epsilon`, assert the
verdict flips exactly there. Because thresholds are injected rather than global, each case sets
the value it is testing rather than depending on whatever the defaults happen to be, so retuning a
default cannot silently invalidate a boundary test.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from builders import frame, with_thresholds

from posture_core import build_report
from posture_core.metrics.head import craniovertebral_angle_deg
from posture_core.metrics.knees import knee_flexion_deg
from posture_core.metrics.trunk import trunk_inclination_deg
from posture_core.resolver import KeypointResolver
from posture_core.rules import evaluate
from posture_core.status import Metric
from posture_core.status import Metric as MetricValue
from posture_core.status import MetricStatus as Status
from posture_core.thresholds import Thresholds

EPSILON = 1e-3
"""A thousandth of a degree: far inside any real measurement error, far outside float noise."""

UNITS = {"arms_crossed": "ratio", "heel_contact_m": "m", "view_confidence": "ratio"}


def codes_for(thresholds: Thresholds, **values: float) -> set[str]:
    """Rule verdicts for *exact* metric values, bypassing the geometry pipeline.

    Behaviour **at** a threshold has to be asserted on the comparison itself. Routing an exact
    value through the builder and the trigonometry does not test what it looks like it tests: a
    figure constructed at 10.000° measures bit-exact 10.0 on arm64 and fractionally above it on
    x86_64, so a `>` comparison fires on one architecture and not the other. CI found this; the
    local suite could not.

    The tests below therefore split in two. Exact-threshold cases call this. Cases an epsilon
    either side of a threshold go through the full pipeline, where a 1e-3 margin is thousands of
    times larger than the discrepancy.
    """
    metrics = {
        name: MetricValue(
            name=name,
            value=value,
            unit=UNITS.get(name, "deg"),
            status=Status.OK,
            detail="",
            confidence=0.95,
        )
        for name, value in values.items()
    }
    return {finding.code for finding in evaluate(metrics, thresholds)}


def codes_at(thresholds: Thresholds, **pose: Any) -> set[str]:
    return {finding.code for finding in build_report(frame(**pose), thresholds).findings}


def metric_at(
    compute: Callable[[KeypointResolver, Thresholds], Metric],
    thresholds: Thresholds,
    **pose: Any,
) -> float:
    metric = compute(KeypointResolver(frame(**pose), thresholds), thresholds)
    assert metric.value is not None
    return float(metric.value)


# ---------------------------------------------------------------------------------------------
# Trunk
# ---------------------------------------------------------------------------------------------


def test_the_slouch_threshold_is_inclusive_at_exactly_the_configured_angle() -> None:
    """`>=`, not `>`.

    A threshold documented as "beyond this is a slouch" that excluded the value itself would leave
    a one-value hole where the verdict silently drops to the weaker finding.
    """
    thresholds = with_thresholds(trunk_upright_deg=10.0, trunk_slouch_deg=20.0)
    assert "trunk_slouch" in codes_for(thresholds, trunk_inclination_deg=20.0)
    assert "trunk_slouch" in codes_at(thresholds, trunk_deg=20.0 + EPSILON)
    assert "trunk_slouch" not in codes_at(thresholds, trunk_deg=20.0 - EPSILON)


def test_just_under_the_slouch_line_is_still_reported_as_a_forward_lean() -> None:
    """The bands must abut with no gap. A posture cannot be neither upright nor leaning."""
    thresholds = with_thresholds(trunk_upright_deg=10.0, trunk_slouch_deg=20.0)
    assert "trunk_forward_lean" in codes_at(thresholds, trunk_deg=20.0 - EPSILON)


def test_the_upright_band_is_exclusive_at_its_upper_edge() -> None:
    """Exactly at `trunk_upright_deg` is still neutral: the field is documented as "at or below
    this is neutral", and an off-by-one here would report a fault for textbook posture."""
    thresholds = with_thresholds(trunk_upright_deg=10.0, trunk_slouch_deg=20.0)
    assert codes_for(thresholds, trunk_inclination_deg=10.0) == set()
    assert "trunk_forward_lean" in codes_at(thresholds, trunk_deg=10.0 + EPSILON)


def test_the_recline_threshold_fires_at_exactly_its_configured_angle() -> None:
    thresholds = with_thresholds(trunk_recline_deg=-20.0)
    assert "trunk_recline" in codes_for(thresholds, trunk_inclination_deg=-20.0)
    assert "trunk_recline" not in codes_at(thresholds, trunk_deg=-20.0 + EPSILON)


def test_upright_and_recline_do_not_overlap() -> None:
    """A single posture must never produce two contradictory trunk findings."""
    thresholds = with_thresholds(
        trunk_upright_deg=10.0, trunk_slouch_deg=20.0, trunk_recline_deg=-20.0
    )
    for angle in (-25.0, -20.0, -5.0, 0.0, 5.0, 15.0, 25.0):
        trunk_codes = {
            code for code in codes_at(thresholds, trunk_deg=angle) if code.startswith("trunk_")
        }
        assert len(trunk_codes) <= 1, f"{angle}° produced {trunk_codes}"


# ---------------------------------------------------------------------------------------------
# Craniovertebral angle — where smaller is worse
# ---------------------------------------------------------------------------------------------


def test_the_forward_head_cutoff_is_strict_and_points_the_right_way() -> None:
    """`<`, not `<=`, and *below* is the fault.

    This is the one threshold in the package that runs the other way, so it is the one most likely
    to be implemented with the comparison flipped — and a flipped comparison here reports the
    healthiest posture as the worst while producing believable numbers throughout.
    """
    thresholds = with_thresholds(cva_forward_head_deg=50.0, cva_borderline_deg=55.0)
    assert "forward_head" not in codes_for(thresholds, craniovertebral_angle_deg=50.0)
    assert "forward_head" in codes_for(thresholds, craniovertebral_angle_deg=50.0 - EPSILON)
    # The builder's craniovertebral angle is 90 - (trunk + neck), so neck_deg=40 gives 50.
    assert "forward_head" in codes_at(thresholds, trunk_deg=0.0, neck_deg=40.0 + EPSILON)


def test_the_borderline_band_sits_immediately_above_the_cutoff() -> None:
    thresholds = with_thresholds(cva_forward_head_deg=50.0, cva_borderline_deg=55.0)
    assert "forward_head_borderline" in codes_for(thresholds, craniovertebral_angle_deg=50.0)
    assert "forward_head_borderline" not in codes_for(thresholds, craniovertebral_angle_deg=55.0)


def test_the_two_head_findings_are_mutually_exclusive() -> None:
    thresholds = with_thresholds(cva_forward_head_deg=50.0, cva_borderline_deg=55.0)
    for neck in (0.0, 30.0, 35.0, 40.0, 50.0):
        head_codes = {
            code
            for code in codes_at(thresholds, trunk_deg=0.0, neck_deg=neck)
            if code.startswith("forward_head")
        }
        assert len(head_codes) <= 1, f"neck {neck}° produced {head_codes}"


# ---------------------------------------------------------------------------------------------
# Knees
# ---------------------------------------------------------------------------------------------


def test_the_kneeling_threshold_is_inclusive() -> None:
    thresholds = with_thresholds(knee_kneeling_max_deg=60.0, knee_seated_min_deg=70.0)
    assert "kneeling" in codes_for(thresholds, knee_flexion_deg=60.0)
    assert "kneeling" not in codes_for(thresholds, knee_flexion_deg=60.0 + EPSILON)
    # 180 - |thigh - shank| = 55 at a 125° separation, comfortably inside the band.
    assert "kneeling" in codes_at(thresholds, thigh_deg=125.0, shank_deg=5.0)


def test_the_comfortable_seated_band_produces_no_finding_at_either_edge() -> None:
    """Both edges are inclusive, so a knee at exactly 70° or exactly 120° is fine.

    Worth pinning because an exclusive edge would report a fault for a posture the thresholds
    describe as comfortable, which is the kind of thing users notice and nobody can reproduce.
    """
    thresholds = with_thresholds(knee_seated_min_deg=70.0, knee_seated_max_deg=120.0)
    assert codes_for(thresholds, knee_flexion_deg=70.0) == set()
    assert codes_for(thresholds, knee_flexion_deg=120.0) == set()
    assert "knee_tucked" in codes_for(thresholds, knee_flexion_deg=70.0 - EPSILON)


def test_the_measured_angle_matches_the_threshold_it_is_compared_against() -> None:
    """Guards the arithmetic the boundary cases above depend on.

    If the builder and the metric disagreed by even a degree, every boundary test here would be
    testing a threshold slightly away from the one it names — and would still pass.
    """
    thresholds = with_thresholds()
    assert metric_at(knee_flexion_deg, thresholds, thigh_deg=110.0, shank_deg=0.0) == pytest.approx(
        70.0, abs=1e-6
    )
    assert metric_at(
        craniovertebral_angle_deg, thresholds, trunk_deg=0.0, neck_deg=40.0
    ) == pytest.approx(50.0, abs=1e-6)
    assert metric_at(trunk_inclination_deg, thresholds, trunk_deg=20.0) == pytest.approx(
        20.0, abs=1e-6
    )


# ---------------------------------------------------------------------------------------------
# Confidence thresholds
# ---------------------------------------------------------------------------------------------


def test_a_landmark_exactly_at_the_visibility_threshold_is_usable() -> None:
    """`<` is the rejection, so the threshold value itself passes.

    Documented as "below this, not trusted". An off-by-one would make the configured value mean
    something one epsilon away from what it says.
    """
    from posture_core import KeypointName as K
    from posture_core.resolver import KeypointResolver as Resolver
    from posture_core.status import KeypointStatus

    thresholds = with_thresholds(min_visibility=0.5)
    at = Resolver(frame(confidence={K.LEFT_HIP: (0.5, 0.9)}), thresholds)
    below = Resolver(frame(confidence={K.LEFT_HIP: (0.5 - EPSILON, 0.9)}), thresholds)
    assert at.status(K.LEFT_HIP) is KeypointStatus.OK
    assert below.status(K.LEFT_HIP) is KeypointStatus.LOW_CONFIDENCE


def test_presence_is_evaluated_before_visibility_at_the_boundary() -> None:
    """A landmark that fails both must be reported as out of frame, not merely unclear.

    Out of frame is the stronger and more actionable statement, and it is the one whose remedy —
    step back — subsumes the other.
    """
    from posture_core import KeypointName as K
    from posture_core.resolver import KeypointResolver as Resolver
    from posture_core.status import KeypointStatus

    thresholds = with_thresholds(min_visibility=0.5, min_presence=0.5)
    resolver = Resolver(frame(confidence={K.LEFT_HIP: (0.1, 0.1)}), thresholds)
    assert resolver.status(K.LEFT_HIP) is KeypointStatus.OUT_OF_FRAME
