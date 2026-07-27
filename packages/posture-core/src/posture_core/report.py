"""``PostureReport`` — the whole assessment, and the only thing the API needs to serialise.

``build_report(frame, thresholds)`` is the top of the rules engine: one pure function, no I/O, no
printing, no globals, no clock. The same frame and the same thresholds produce a byte-identical
report forever, which is what makes golden-snapshot testing possible (OP-35) and what lets the
frontend and the Python engine be compared fixture-by-fixture in CI.

## What a report contains, and why gaps are in it

Findings *and* gaps, side by side. A report saying "you are slouching, and I could not see your
knees" is a more useful and more honest document than one saying only "you are slouching" — and
the silence in the second version is exactly what the inherited engine shipped.

## The version stamps

``schema_version`` describes the *shape* of this document; ``rules_version`` describes the
thresholds that produced it. Both are stored with every analysis (Epic E) because two reports are
only comparable if they were produced by the same rules. Without the stamps, a stored report
becomes uninterpretable the first time a threshold moves: you can no longer tell whether the
user's posture changed or the yardstick did — which quietly destroys the trend view that is the
whole point of keeping history.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from posture_core.metrics import arms, feet, head, knees, trunk, view
from posture_core.resolver import KeypointResolver
from posture_core.rules import Severity, evaluate
from posture_core.status import KeypointStatus, MetricStatus
from posture_core.thresholds import DEFAULT_THRESHOLDS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from posture_core.keypoints import KeypointName, PoseFrame
    from posture_core.rules import Finding
    from posture_core.status import Gap, Metric
    from posture_core.thresholds import Thresholds

__all__ = ["SCHEMA_VERSION", "PostureReport", "build_report"]

SCHEMA_VERSION: Final = "1.0"

# Every metric, in the order a person reads a body. Adding a metric is adding one entry here and
# one module — nothing else in this file knows what any of them do.
METRICS: Final = (
    view.view_confidence,
    trunk.trunk_inclination_deg,
    head.craniovertebral_angle_deg,
    arms.arms_crossed,
    arms.elbow_flexion_deg,
    knees.knee_flexion_deg,
    feet.heel_contact_m,
)

_SEVERITY_WEIGHT: Final = {Severity.MAJOR: 1.0, Severity.MINOR: 0.5, Severity.INFO: 0.0}


@dataclasses.dataclass(frozen=True, slots=True)
class Quality:
    """How much of the body the engine could actually see."""

    assessed: int
    """Metrics that produced a value."""

    total: int
    gaps: Sequence[Gap]
    keypoints: Mapping[KeypointName, KeypointStatus]

    def __post_init__(self) -> None:
        # `frozen=True` freezes the attribute bindings, not the containers bound to them. `Gap`
        # and `PoseFrame` both guard against this; a report's quality section is at least as
        # worth protecting, since it is the record of what the engine could not assess.
        object.__setattr__(self, "gaps", tuple(self.gaps))
        object.__setattr__(self, "keypoints", MappingProxyType(dict(self.keypoints)))

    @property
    def coverage(self) -> float:
        """Fraction of metrics that produced a value, in ``[0, 1]``."""
        return self.assessed / self.total if self.total else 0.0


@dataclasses.dataclass(frozen=True, slots=True)
class PostureReport:
    """One assessment of one photograph."""

    schema_version: str
    rules_version: str
    backend: str
    inference_ms: float
    image_width: int
    image_height: int
    metrics: Mapping[str, Metric]
    findings: Sequence[Finding]
    quality: Quality
    overall_score: float | None
    """``0`` to ``100``, or ``None`` when nothing could be measured.

    ``None`` rather than ``0`` or ``100``. A score of 0 would read as terrible posture and 100 as
    perfect, and both are confident claims about a photograph the engine could not assess. This is
    the same defect as "Straight back position" wearing different clothes, so it gets the same
    treatment: say nothing rather than say something false.

    The scoring itself is deliberately crude — 100 less a fixed penalty per finding, weighted by
    severity — and should be read as a summary rather than a measurement. The findings are the
    output that means something.
    """

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready shape. Ordered for a readable diff, not for parsing.

        Written here rather than in the API layer so that the CLI, the golden fixtures and the
        HTTP response all serialise identically. A second serialiser would drift from this one,
        and the cross-language golden corpus (OP-36) depends on there being exactly one.
        """
        return {
            "schema_version": self.schema_version,
            "rules_version": self.rules_version,
            "backend": self.backend,
            "inference_ms": round(self.inference_ms, 3),
            "image": {"width": self.image_width, "height": self.image_height},
            "overall_score": self.overall_score,
            "findings": [
                {
                    "code": finding.code,
                    "severity": finding.severity.value,
                    "message": finding.message,
                    "metric": finding.metric,
                    "value": _round(finding.value),
                    "confidence": round(finding.confidence, 3),
                }
                for finding in self.findings
            ],
            "metrics": {
                name: {
                    "value": _round(metric.value),
                    "unit": metric.unit,
                    "status": metric.status.value,
                    "detail": metric.detail,
                    "confidence": _round(metric.confidence),
                }
                for name, metric in sorted(self.metrics.items())
            },
            "quality": {
                "assessed": self.quality.assessed,
                "total": self.quality.total,
                "coverage": round(self.quality.coverage, 3),
                "gaps": [
                    {
                        "metric": gap.metric,
                        "status": gap.status.value,
                        "detail": gap.detail,
                        "keypoints": {
                            name.value: status.value
                            for name, status in sorted(
                                gap.keypoints.items(), key=lambda item: item[0].value
                            )
                        },
                    }
                    for gap in self.quality.gaps
                ],
                # Only the keypoints with something wrong. Listing all 34 on every report would
                # quadruple its size to say "fine" thirty times.
                "keypoints": {
                    name.value: status.value
                    for name, status in sorted(
                        self.quality.keypoints.items(), key=lambda item: item[0].value
                    )
                    if status is not KeypointStatus.OK
                },
            },
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def build_report(frame: PoseFrame, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> PostureReport:
    """Run every metric, apply every rule, and assemble the result.

    Pure: no I/O, no printing, no globals, no clock. ``inference_ms`` is carried through from the
    frame rather than measured here, so the same frame always yields the same report.
    """
    resolver = KeypointResolver(frame, thresholds)
    metrics = {
        metric.name: metric for metric in (compute(resolver, thresholds) for compute in METRICS)
    }

    gaps = [
        metric.as_gap({name: resolver.status(name) for name in metric.inputs})
        for metric in metrics.values()
        if metric.status is not MetricStatus.OK
    ]
    findings = evaluate(metrics, thresholds)
    assessed = sum(1 for metric in metrics.values() if metric.is_ok)

    return PostureReport(
        schema_version=SCHEMA_VERSION,
        rules_version=thresholds.version,
        backend=frame.backend,
        inference_ms=frame.inference_ms,
        image_width=frame.image_width,
        image_height=frame.image_height,
        metrics=metrics,
        findings=findings,
        quality=Quality(
            assessed=assessed,
            total=len(metrics),
            gaps=gaps,
            keypoints=resolver.statuses,
        ),
        overall_score=_score(findings, assessed, thresholds),
    )


def _score(findings: Sequence[Finding], assessed: int, thresholds: Thresholds) -> float | None:
    if assessed == 0:
        return None
    penalty = sum(
        thresholds.score_penalty_per_finding * _SEVERITY_WEIGHT[finding.severity]
        for finding in findings
    )
    return max(0.0, min(100.0, 100.0 - penalty))
