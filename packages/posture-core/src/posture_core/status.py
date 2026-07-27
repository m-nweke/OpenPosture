"""The vocabulary for "I could not assess this" — the correctness fix the whole rebuild turns on.

## The defect being fixed

The inherited engine's `checkPosition` returned `None` whenever anything went wrong: a missing
keypoint, an exception it had swallowed, a geometry it could not evaluate. The caller rendered
`None` as **"Straight back position."** So the system told users their posture was fine precisely
when it had failed to assess them at all (FINDINGS §2.5).

That is not a bug in one function. It is what happens when a codebase has no way to *say*
"unknown" — the only channel available was the same one used for answers, so absence of evidence
became evidence of absence.

## The fix

A metric that could not be computed carries ``value = None`` **and** a :class:`MetricStatus`
explaining why, and produces a :class:`Gap` rather than a :class:`~posture_core.rules.Finding`.
The API can then say "I couldn't assess your knees — try a wider shot" instead of inventing an
answer. Nothing here is clever; it is just an honest type, and it is the cheapest high-value
change in the project.

## No bare `except Exception`

Anywhere. A swallowed exception is how the original produced a `None` it could not explain. Where
a computation can legitimately fail — coincident landmarks give a vector with no direction — the
failure is caught **by its specific type** and converted into a status that names it.
"""

from __future__ import annotations

import dataclasses
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from posture_core.keypoints import KeypointName

__all__ = ["Gap", "KeypointStatus", "Metric", "MetricStatus"]


class KeypointStatus(StrEnum):
    """How usable a single landmark is.

    Four states rather than a boolean, because the remedies differ and the user deserves to be
    told which one applies. "Your knees are out of shot" and "your knees are behind the desk" ask
    for different things from the person holding the camera.
    """

    OK = "ok"
    LOW_CONFIDENCE = "low_confidence"
    """Present and in frame, but the model is unsure. Usable for context, not for measurement."""

    NOT_DETECTED = "not_detected"
    """The backend did not report this keypoint at all — a different fact from low confidence.

    Collapsing the two would make it impossible to tell "this backend has no heel landmark"
    (a schema limitation, as with the 17-point escape hatch in ADR-0002) from "this photo does not
    show a heel" (a framing problem the user can fix).
    """

    OUT_OF_FRAME = "out_of_frame"
    """Low *presence*: the model does not believe the point is in the picture.

    Distinguishable from occlusion only because MediaPipe reports presence and visibility
    separately — one of the three reasons it was chosen (ADR-0002).
    """


class MetricStatus(StrEnum):
    """Whether a metric's value can be believed."""

    OK = "ok"

    INSUFFICIENT_KEYPOINTS = "insufficient_keypoints"
    """A landmark the metric needs was not reported, or was out of frame."""

    LOW_CONFIDENCE = "low_confidence"
    """Every needed landmark exists, but at least one is below the confidence threshold.

    Kept separate from INSUFFICIENT_KEYPOINTS because it is recoverable by better lighting or a
    clearer shot, whereas a missing keypoint usually needs a different framing.
    """

    UNDEFINED_GEOMETRY = "undefined_geometry"
    """The landmarks are all present but describe no answer.

    Not in the original ticket's list, and added because the situation is real: a backend can
    collapse two landmarks onto the same point when a joint is fully occluded, and the angle
    between a vector and nothing is a question with no answer rather than a zero. Folding it into
    INSUFFICIENT_KEYPOINTS would have been a small lie in exactly the place this module exists to
    stop telling small lies.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class Metric:
    """One measurement, or one honest refusal to measure.

    The invariant, enforced below: ``status is OK`` if and only if ``value is not None``. It is
    checked rather than trusted because every downstream consumer — rules, the report, the API
    response, the frontend — branches on it, and a metric that violated it would produce a
    plausible-looking wrong answer at each of them.
    """

    name: str
    value: float | None
    unit: str
    status: MetricStatus
    detail: str
    """Human-readable, and written for the person in the photograph rather than for a log."""

    inputs: tuple[KeypointName, ...] = ()
    """Which landmarks this measurement rests on. Carried so a Gap can name them."""

    confidence: float | None = None
    """The *weakest* input's visibility, not the average.

    A measurement is only as trustworthy as the least trustworthy thing it was computed from.
    Averaging would let one confidently-seen landmark launder an unseen one into a plausible
    number — which is how a metric ends up reported with confidence it has not earned.
    """

    def __post_init__(self) -> None:
        if (self.status is MetricStatus.OK) != (self.value is not None):
            raise ValueError(
                f"metric {self.name!r} has status {self.status} with value {self.value!r}; "
                "a value and an OK status must accompany each other"
            )

    @property
    def is_ok(self) -> bool:
        return self.status is MetricStatus.OK

    def as_gap(self, problems: Mapping[KeypointName, KeypointStatus] | None = None) -> Gap:
        """The :class:`Gap` this metric produces when it could not be computed."""
        if self.is_ok:
            raise ValueError(f"metric {self.name!r} succeeded and has no gap to report")
        return Gap(
            metric=self.name,
            status=self.status,
            detail=self.detail,
            keypoints=dict(problems or {}),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Gap:
    """Something the engine deliberately did not assess, and why.

    Gaps are a **first-class part of the report**, not an error channel. A report with three
    findings and two gaps is a more useful and more honest document than one with three findings
    and silence — and the silence is what the original shipped.
    """

    metric: str
    status: MetricStatus
    detail: str
    keypoints: Mapping[KeypointName, KeypointStatus] = dataclasses.field(default_factory=dict)
    """The specific landmarks responsible, so the advice can be specific too."""

    def __post_init__(self) -> None:
        if self.status is MetricStatus.OK:
            raise ValueError(f"gap for {self.metric!r} cannot have an OK status")
        # Same guard `PoseFrame` applies, and for the same reason: `frozen=True` freezes the
        # attribute binding, not the mapping bound to it, so without this a gap's keypoints stay
        # editable after the gap has been produced. A gap is a statement about what the engine
        # could not assess; it should not be revisable by whoever happens to hold it.
        object.__setattr__(self, "keypoints", MappingProxyType(dict(self.keypoints)))
