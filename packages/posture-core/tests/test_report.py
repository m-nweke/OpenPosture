"""Tests for the rules layer and the assembled report.

This is the layer a user actually meets, so these tests are mostly about what the report *says* —
including what it says about the parts of the body it could not see.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from builders import SEATED, frame, unclear, with_thresholds, without

from posture_core import Finding, Severity, build_report
from posture_core import KeypointName as K
from posture_core.report import SCHEMA_VERSION, PostureReport
from posture_core.status import MetricStatus
from posture_core.synthetic import View
from posture_core.thresholds import DEFAULT_THRESHOLDS, RULES_VERSION

SLOUCHED: dict[str, Any] = {"trunk_deg": 35.0, "neck_deg": 30.0}
UPRIGHT: dict[str, Any] = {"trunk_deg": 3.0, "neck_deg": 3.0}


def report(**pose_kwargs: Any) -> PostureReport:
    thresholds = pose_kwargs.pop("thresholds", DEFAULT_THRESHOLDS)
    return build_report(frame(**pose_kwargs), thresholds)


def codes(**pose_kwargs: Any) -> list[str]:
    return [finding.code for finding in report(**pose_kwargs).findings]


# ---------------------------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------------------------


def test_a_slouched_figure_produces_the_findings_it_should() -> None:
    found = codes(**SLOUCHED)
    assert "trunk_slouch" in found
    assert "forward_head" in found


def test_an_upright_figure_produces_no_faults() -> None:
    """The only case where saying nothing is correct — and it must be reached by measuring, not
    by failing to measure. `test_a_frame_the_engine_cannot_read_says_so` is the other half."""
    assert [
        finding for finding in report(**UPRIGHT).findings if finding.severity is not Severity.INFO
    ] == []


def test_reclining_is_reported_differently_from_slouching() -> None:
    """Opposite postures with different advice. An unsigned metric could not separate them."""
    assert "trunk_recline" in codes(trunk_deg=-30.0)
    assert "trunk_slouch" in codes(trunk_deg=30.0)


def test_findings_are_ordered_most_severe_first() -> None:
    """The user's eye goes to the top of the list, so the top should be worth acting on."""
    severities = [finding.severity for finding in report(**SLOUCHED).findings]
    order = {Severity.MAJOR: 0, Severity.MINOR: 1, Severity.INFO: 2}
    assert [order[severity] for severity in severities] == sorted(
        order[severity] for severity in severities
    )


def test_every_finding_names_the_metric_it_came_from() -> None:
    """So a reader can check the working rather than take the verdict on faith."""
    result = report(**SLOUCHED)
    for finding in result.findings:
        assert finding.metric in result.metrics


def test_findings_carry_confidence_rather_than_asserting_facts() -> None:
    for finding in report(**SLOUCHED).findings:
        assert 0.0 <= finding.confidence <= 1.0


def test_an_unclear_landmark_lowers_the_confidence_of_the_finding_that_rests_on_it() -> None:
    """The weakest input, not the average — a finding cannot be more certain than its evidence."""
    confident = next(f for f in report(**SLOUCHED).findings if f.code == "trunk_slouch")
    hedged = next(
        f
        for f in report(**SLOUCHED, **unclear(K.LEFT_HIP, visibility=0.55)).findings
        if f.code == "trunk_slouch"
    )
    assert hedged.confidence < confident.confidence


def test_a_frontal_photo_lowers_confidence_and_says_why() -> None:
    """The correction to the plan, visible in the output.

    A head-on photograph does not lose the measurement — world landmarks recover the full lean —
    but it does rest on the model's weakest dimension. So the finding survives with reduced
    confidence, and a separate INFO finding tells the user a side-on photo would be firmer.
    """
    side_on = next(f for f in report(**SLOUCHED).findings if f.code == "trunk_slouch")
    head_on_report = report(**SLOUCHED, view=View.FRONTAL)
    head_on = next(f for f in head_on_report.findings if f.code == "trunk_slouch")

    assert head_on.confidence < side_on.confidence
    assert "frontal_view" in [finding.code for finding in head_on_report.findings]


def test_the_thresholds_that_decide_are_injected() -> None:
    strict = with_thresholds(trunk_upright_deg=2.0, trunk_slouch_deg=5.0)
    assert "trunk_slouch" not in codes(trunk_deg=8.0)
    assert "trunk_slouch" in codes(trunk_deg=8.0, thresholds=strict)


# ---------------------------------------------------------------------------------------------
# Gaps — the half the original never had
# ---------------------------------------------------------------------------------------------


def test_a_metric_that_abstained_appears_as_a_gap_not_as_silence() -> None:
    """The correction to "Straight back position", in its final form.

    A report saying "you are slouching, and I could not see your knees" is more useful and more
    honest than one saying only "you are slouching". The silence in the second version is what the
    inherited engine shipped.
    """
    result = report(**SLOUCHED, **without(K.LEFT_KNEE, K.RIGHT_KNEE))
    gap = next(gap for gap in result.quality.gaps if gap.metric == "knee_flexion_deg")
    assert gap.status is MetricStatus.INSUFFICIENT_KEYPOINTS
    assert "knee" in gap.detail
    assert "trunk_slouch" in [finding.code for finding in result.findings]


def test_a_gap_names_the_keypoints_responsible_so_the_advice_can_be_specific() -> None:
    result = report(**without(K.LEFT_HEEL, K.RIGHT_HEEL))
    gap = next(gap for gap in result.quality.gaps if gap.metric == "heel_contact_m")
    assert set(gap.keypoints) == {K.LEFT_HEEL, K.RIGHT_HEEL}


def test_coverage_reports_how_much_of_the_body_was_assessed() -> None:
    full = report(**SEATED)
    partial = report(**without(K.LEFT_KNEE, K.RIGHT_KNEE, K.LEFT_HEEL, K.RIGHT_HEEL))
    assert partial.quality.coverage < full.quality.coverage
    assert 0.0 <= partial.quality.coverage <= 1.0


def test_the_score_is_none_when_nothing_could_be_measured() -> None:
    """Not 0, not 100. Both are confident claims about a photograph the engine could not read —
    the same defect as "Straight back position" wearing different clothes."""
    empty = build_report(
        frame(omit=tuple(K)),
        DEFAULT_THRESHOLDS,
    )
    assert empty.overall_score is None
    assert empty.findings == []
    assert len(empty.quality.gaps) == empty.quality.total


def test_a_frame_the_engine_cannot_read_says_so_rather_than_reporting_good_posture() -> None:
    result = build_report(frame(omit=tuple(K)), DEFAULT_THRESHOLDS)
    assert result.quality.assessed == 0
    assert all(gap.status is not MetricStatus.OK for gap in result.quality.gaps)


# ---------------------------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------------------------


def test_worse_posture_scores_lower() -> None:
    good = report(**UPRIGHT).overall_score
    bad = report(**SLOUCHED).overall_score
    assert good is not None and bad is not None
    assert bad < good


def test_an_informational_finding_does_not_cost_points() -> None:
    """Kneeling and folded arms are context, not faults, and scoring them would tell users to
    change things that are not problems."""
    upright = report(**UPRIGHT).overall_score
    kneeling = report(trunk_deg=3.0, neck_deg=3.0, thigh_deg=15.0, shank_deg=165.0)
    assert "kneeling" in [finding.code for finding in kneeling.findings]
    assert kneeling.overall_score == upright


def test_the_score_stays_inside_its_stated_range() -> None:
    terrible = report(trunk_deg=60.0, neck_deg=60.0, thigh_deg=120.0, shank_deg=5.0)
    assert terrible.overall_score is not None
    assert 0.0 <= terrible.overall_score <= 100.0


# ---------------------------------------------------------------------------------------------
# Shape, purity, versioning
# ---------------------------------------------------------------------------------------------


def test_the_report_is_stamped_with_both_versions() -> None:
    """A stored report with no stamps becomes uninterpretable the first time a threshold moves:
    you cannot tell whether the user's posture changed or the yardstick did."""
    result = report(**SLOUCHED)
    assert result.schema_version == SCHEMA_VERSION
    assert result.rules_version == RULES_VERSION


def test_the_rules_version_follows_the_thresholds_actually_used() -> None:
    custom = with_thresholds(version="9.9.9")
    assert report(**SLOUCHED, thresholds=custom).rules_version == "9.9.9"


def test_backend_and_latency_travel_with_the_report() -> None:
    result = report(**SLOUCHED)
    assert result.backend == "synthetic"
    assert result.inference_ms == 0.0


def test_building_a_report_twice_gives_the_same_answer() -> None:
    """Purity, stated as a test. No clock, no randomness, no globals — which is what makes golden
    snapshots (OP-35) possible at all."""
    first = build_report(frame(**SLOUCHED), DEFAULT_THRESHOLDS).to_dict()
    second = build_report(frame(**SLOUCHED), DEFAULT_THRESHOLDS).to_dict()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_serialised_report_is_json_and_sorted() -> None:
    payload = report(**SLOUCHED).to_dict()
    encoded = json.dumps(payload)
    assert json.loads(encoded) == payload
    assert list(payload["metrics"]) == sorted(payload["metrics"])


def test_the_serialised_report_lists_only_problem_keypoints() -> None:
    """All 34 on every report would quadruple its size to say "fine" thirty times."""
    payload = report(**SLOUCHED, **without(K.LEFT_HEEL)).to_dict()
    assert set(payload["quality"]["keypoints"]) == {"left_heel"}


def test_every_metric_appears_in_the_report_whether_or_not_it_succeeded() -> None:
    """A metric silently missing from the output is indistinguishable from one that was never
    written, which is how a capability quietly disappears."""
    result = report(**without(K.LEFT_KNEE, K.RIGHT_KNEE))
    assert "knee_flexion_deg" in result.metrics
    assert result.metrics["knee_flexion_deg"].value is None


def test_a_report_is_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        report(**SLOUCHED).overall_score = 100.0  # type: ignore[misc]


def test_a_finding_cannot_carry_an_impossible_confidence() -> None:
    """Confidence is a probability. A value outside [0, 1] would render as a nonsense percentage
    in the UI rather than failing anywhere the cause could be found."""
    with pytest.raises(ValueError, match="confidence"):
        Finding(
            code="x",
            severity=Severity.MINOR,
            message="",
            metric="trunk_inclination_deg",
            value=1.0,
            confidence=1.5,
        )


def test_a_borderline_forward_head_is_reported_as_minor() -> None:
    """The middle band exists so the report can mention something without calling it a fault."""
    assert "forward_head_borderline" in codes(trunk_deg=0.0, neck_deg=37.0)


def test_a_tucked_knee_is_reported() -> None:
    assert "knee_tucked" in codes(thigh_deg=120.0, shank_deg=5.0)


def test_folded_arms_are_reported_as_context_not_as_a_fault() -> None:
    result = report(upper_arm_deg=15.0, forearm_deg=80.0, forearm_cross_deg=55.0)
    folded = next(f for f in result.findings if f.code == "arms_folded")
    assert folded.severity is Severity.INFO


def test_a_metric_with_no_confidence_recorded_does_not_break_the_finding() -> None:
    """`Metric.confidence` is optional, and a rule must not assume it was populated."""
    for finding in report(**SLOUCHED).findings:
        assert finding.confidence > 0.0


def test_a_mild_forward_lean_is_reported_as_minor_rather_than_ignored() -> None:
    """The band between "upright" and "slouching" exists so the report can nudge without alarming.

    Collapsing it would force every posture into either silence or a major finding, and most real
    sitting falls between the two.
    """
    findings = [f for f in report(trunk_deg=15.0).findings if f.code == "trunk_forward_lean"]
    assert len(findings) == 1
    assert findings[0].severity is Severity.MINOR


def test_an_unsupported_foot_is_reported_with_a_practical_suggestion() -> None:
    """The capability the original listed as a goal and never delivered, surfaced to the user."""
    from posture_core import Landmark, PoseFrame

    original = frame()
    landmarks = dict(original.landmarks)
    for heel, toe in ((K.LEFT_HEEL, K.LEFT_FOOT_INDEX), (K.RIGHT_HEEL, K.RIGHT_FOOT_INDEX)):
        existing, toe_landmark = landmarks[heel], landmarks[toe]
        assert toe_landmark.y_world is not None
        landmarks[heel] = Landmark(
            x=existing.x,
            y=existing.y,
            visibility=existing.visibility,
            presence=existing.presence,
            x_world=existing.x_world,
            y_world=toe_landmark.y_world - 0.12,
            z_world=existing.z_world,
        )

    result = build_report(
        PoseFrame(
            landmarks=landmarks,
            image_width=original.image_width,
            image_height=original.image_height,
            backend="synthetic",
            inference_ms=0.0,
        ),
        DEFAULT_THRESHOLDS,
    )
    finding = next(f for f in result.findings if f.code == "feet_unsupported")
    assert finding.severity is Severity.MINOR
    assert "footrest" in finding.message


def test_a_reports_quality_section_cannot_be_edited_after_the_fact() -> None:
    """Same guard as `Gap` and `PoseFrame`, for the same reason.

    `frozen=True` freezes the attribute bindings, not the containers bound to them. The quality
    section is the record of what the engine could not assess, which is precisely the part of a
    report that should not be quietly revisable.
    """
    quality = report(**SLOUCHED, **without(K.LEFT_KNEE)).quality
    with pytest.raises(TypeError):
        quality.keypoints[K.NOSE] = "ok"  # type: ignore[index]
    with pytest.raises(AttributeError):
        quality.gaps.append(None)  # type: ignore[attr-defined]
