"""Turning measurements into findings — the layer that decides, separately from the layer that
measures.

A metric answers "how far forward is this torso leaning?". A rule answers "is that worth telling
the user about?". Keeping them apart is what lets a threshold move without touching a
measurement, and it is why the report can show a user *how close to the line* they were rather
than only which side of it they landed on.

## Findings and gaps are both outputs

A metric that abstained produces a :class:`~posture_core.status.Gap`, never a
:class:`Finding` — and never silence. The report carries both. That is the whole correction to
the inherited engine, which had exactly one output channel and used it to say "Straight back
position" whenever it had failed (FINDINGS §2.5).

## Confidence travels with the finding

Every finding carries the confidence of the weakest landmark it rests on, multiplied by the view
factor (OP-31) when the measurement is a sagittal one. A finding is therefore always
*qualified* — the API and the frontend can present a 0.42-confidence slouch differently from a
0.95-confidence one, instead of presenting both as fact.
"""

from __future__ import annotations

import dataclasses
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from posture_core.metrics import arms, feet, head, knees, trunk, view

if TYPE_CHECKING:
    from collections.abc import Mapping

    from posture_core.status import Metric
    from posture_core.thresholds import Thresholds

__all__ = ["Finding", "Severity", "evaluate"]


class Severity(StrEnum):
    """How much a finding matters.

    Three levels, not a numeric score. A number would imply a precision the underlying evidence
    does not support, and would invite arithmetic on values that are really categories.
    """

    INFO = "info"
    """Context worth showing, not a fault. Kneeling; arms folded; a sub-optimal camera angle."""

    MINOR = "minor"
    MAJOR = "major"


@dataclasses.dataclass(frozen=True, slots=True)
class Finding:
    """One thing worth telling the user, with the evidence attached."""

    code: str
    """Stable machine-readable identifier. The UI keys off this; ``message`` may be reworded."""

    severity: Severity
    message: str
    metric: str
    """Which measurement produced this, so a reader can check the working."""

    value: float | None
    confidence: float
    """``[0, 1]``. The weakest input's confidence, further reduced by the camera angle.

    Carried on the finding rather than left in the metric because this is what the user's
    interface needs in order to hedge. A finding with no confidence attached is a claim; a finding
    with 0.42 attached is an observation.
    """

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"finding {self.code!r} has confidence {self.confidence}")


def evaluate(metrics: Mapping[str, Metric], thresholds: Thresholds) -> list[Finding]:
    """Every finding the measurements support, most severe first.

    Metrics that abstained are skipped silently *here* — their gaps are assembled by
    :mod:`posture_core.report`, which is the module that owns the report's completeness. A rule
    that tried to emit both would duplicate every abstention.
    """
    view_factor = view.view_confidence_factor(_value(metrics, view.NAME), thresholds)

    findings: list[Finding] = []
    for rule in _RULES:
        findings.extend(rule(metrics, thresholds, view_factor))

    # Most severe first, then by descending confidence: the user's eye goes to the top of the
    # list, so the top of the list should be the thing most worth acting on and least likely to
    # be wrong.
    order = {Severity.MAJOR: 0, Severity.MINOR: 1, Severity.INFO: 2}
    findings.sort(key=lambda finding: (order[finding.severity], -finding.confidence))
    return findings


def _value(metrics: Mapping[str, Metric], name: str) -> float | None:
    metric = metrics.get(name)
    return metric.value if metric is not None and metric.is_ok else None


def _confidence(metrics: Mapping[str, Metric], name: str, factor: float = 1.0) -> float:
    metric = metrics.get(name)
    base = 1.0 if metric is None or metric.confidence is None else metric.confidence
    return max(0.0, min(1.0, base * factor))


def _trunk(
    metrics: Mapping[str, Metric], thresholds: Thresholds, view_factor: float
) -> list[Finding]:
    value = _value(metrics, trunk.NAME)
    if value is None:
        return []
    # Sagittal: the camera angle affects how much the depth estimate can be trusted, so the view
    # factor applies.
    confidence = _confidence(metrics, trunk.NAME, view_factor)

    if value >= thresholds.trunk_slouch_deg:
        return [
            Finding(
                code="trunk_slouch",
                severity=Severity.MAJOR,
                message=(
                    f"Your torso is leaning {value:.0f}° forward. Try bringing your hips back "
                    "into the chair so your back is supported."
                ),
                metric=trunk.NAME,
                value=value,
                confidence=confidence,
            )
        ]
    if value > thresholds.trunk_upright_deg:
        return [
            Finding(
                code="trunk_forward_lean",
                severity=Severity.MINOR,
                message=f"Your torso is leaning {value:.0f}° forward — slightly more than neutral.",
                metric=trunk.NAME,
                value=value,
                confidence=confidence,
            )
        ]
    if value <= thresholds.trunk_recline_deg:
        return [
            Finding(
                code="trunk_recline",
                severity=Severity.MINOR,
                message=(
                    f"You are leaning {abs(value):.0f}° back. That is fine if the chair supports "
                    "your back, and not if you are perched on the edge of the seat."
                ),
                metric=trunk.NAME,
                value=value,
                confidence=confidence,
            )
        ]
    return []


def _head(
    metrics: Mapping[str, Metric], thresholds: Thresholds, view_factor: float
) -> list[Finding]:
    value = _value(metrics, head.NAME)
    if value is None:
        return []
    confidence = _confidence(metrics, head.NAME, view_factor)

    # Smaller is worse — the opposite of every other threshold in the package.
    if value < thresholds.cva_forward_head_deg:
        return [
            Finding(
                code="forward_head",
                severity=Severity.MAJOR,
                message=(
                    f"Your head sits well forward of your shoulders (craniovertebral angle "
                    f"{value:.0f}°, below the {thresholds.cva_forward_head_deg:.0f}° mark). "
                    "Drawing your chin back over your shoulders takes the load off your neck."
                ),
                metric=head.NAME,
                value=value,
                confidence=confidence,
            )
        ]
    if value < thresholds.cva_borderline_deg:
        return [
            Finding(
                code="forward_head_borderline",
                severity=Severity.MINOR,
                message=f"Your head is a little forward of your shoulders ({value:.0f}°).",
                metric=head.NAME,
                value=value,
                confidence=confidence,
            )
        ]
    return []


def _knees(
    metrics: Mapping[str, Metric], thresholds: Thresholds, view_factor: float
) -> list[Finding]:
    del view_factor  # A joint angle in the sagittal plane, but measured across it, not into it.
    value = _value(metrics, knees.KNEE_FLEXION)
    if value is None:
        return []
    confidence = _confidence(metrics, knees.KNEE_FLEXION)

    if value <= thresholds.knee_kneeling_max_deg:
        return [
            Finding(
                code="kneeling",
                severity=Severity.INFO,
                message=f"You appear to be kneeling — your knee is folded to {value:.0f}°.",
                metric=knees.KNEE_FLEXION,
                value=value,
                confidence=confidence,
            )
        ]
    if value < thresholds.knee_seated_min_deg:
        return [
            Finding(
                code="knee_tucked",
                severity=Severity.MINOR,
                message=(
                    f"Your knee is tucked back to {value:.0f}°, which is tighter than a "
                    "comfortable seated angle. Try moving your feet forward."
                ),
                metric=knees.KNEE_FLEXION,
                value=value,
                confidence=confidence,
            )
        ]
    return []


def _feet(
    metrics: Mapping[str, Metric], thresholds: Thresholds, view_factor: float
) -> list[Finding]:
    del view_factor
    value = _value(metrics, feet.HEEL_CONTACT)
    if value is None or value <= thresholds.heel_contact_tolerance_m:
        return []
    return [
        Finding(
            code="feet_unsupported",
            severity=Severity.MINOR,
            message=(
                f"Your heel is about {value * 100:.0f} cm above your toes, which suggests that "
                "foot is not resting on the floor. A footrest or a lower seat would help."
            ),
            metric=feet.HEEL_CONTACT,
            value=value,
            confidence=_confidence(metrics, feet.HEEL_CONTACT),
        )
    ]


def _arms(
    metrics: Mapping[str, Metric], thresholds: Thresholds, view_factor: float
) -> list[Finding]:
    del view_factor
    value = _value(metrics, arms.ARMS_CROSSED)
    if value is None or value >= thresholds.arms_crossed_ratio:
        return []
    return [
        Finding(
            code="arms_folded",
            severity=Severity.INFO,
            message=(
                "Your arms are folded across your chest. That is not a fault in itself, but it "
                "often accompanies a rounded upper back."
            ),
            metric=arms.ARMS_CROSSED,
            value=value,
            confidence=_confidence(metrics, arms.ARMS_CROSSED),
        )
    ]


def _view(
    metrics: Mapping[str, Metric], thresholds: Thresholds, view_factor: float
) -> list[Finding]:
    del view_factor
    value = _value(metrics, view.NAME)
    if value is None or value < thresholds.frontal_view_min_ratio:
        return []
    return [
        Finding(
            code="frontal_view",
            severity=Severity.INFO,
            message=(
                "This photo looks like it was taken from the front. The measurements below still "
                "work, but they rest on a depth estimate that is weakest along the camera's own "
                "axis — a side-on photo would give a firmer answer."
            ),
            metric=view.NAME,
            value=value,
            confidence=_confidence(metrics, view.NAME),
        )
    ]


_RULES: Final = (_trunk, _head, _knees, _feet, _arms, _view)
