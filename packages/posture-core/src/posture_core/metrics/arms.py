"""``arms_crossed`` and ``elbow_flexion_deg`` — arm position, normalised by the body measuring it.

## The literal this replaces

The legacy engine decided arms were crossed by comparing a pixel distance against ``±100``. Its
own inline comment admitted the problem: the value *"shall be replaced with a calculation which
can adjust to different sizes of people."* It never was. So the same crossed arms photographed
from twice the distance produced half the pixel separation and the opposite verdict, and a taller
person and a shorter one at the same distance were judged by the same absolute number.

Every quantity here is divided by the subject's own torso length, measured in the same frame. That
cancels camera distance and body size together, because both scale the numerator and the
denominator identically.

## A correction to the plan's formula

`docs/V2-PLAN.md` specifies arms-crossed as ``abs(forearm - upper_arm) / torso < 0.15``. That
compares the two segments of the *same* arm, and with typical adult proportions — upper arm
~0.30 m, forearm ~0.27 m, torso ~0.50 m — it evaluates to 0.06 for everybody, in every posture,
with their arms anywhere at all. It would have reported crossed arms universally.

What actually distinguishes folded arms is that **each wrist sits near the opposite elbow**. That
is what this measures, still normalised by torso length so the fix to the original defect is kept.
Run against the eight fixtures with the real backend, the folded-arms photograph scores 0.53 and
every other one falls between 0.91 and 1.20 — a clean separation, and where the default threshold
comes from. The numbers are recorded on ``Thresholds.arms_crossed_ratio``.

## What the metric reports, and what decides

``arms_crossed`` returns the *evidence* — the normalised wrist-to-opposite-elbow distance — not a
verdict. Comparing it against a threshold is the rules layer's job (OP-32). Keeping measurement
and judgement apart is what lets a threshold be retuned without touching a measurement, and what
lets the report show a user how close to the line they were.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from posture_core.geometry import MIN_LENGTH, angle_between, distance, midpoint
from posture_core.keypoints import KeypointName
from posture_core.metrics._support import abstain, measure, world_points
from posture_core.resolver import Resolved
from posture_core.status import Metric

if TYPE_CHECKING:
    from posture_core.resolver import KeypointResolver
    from posture_core.thresholds import Thresholds

__all__ = ["ARMS_CROSSED", "ELBOW_FLEXION", "arms_crossed", "elbow_flexion_deg"]

ARMS_CROSSED: Final = "arms_crossed"
ELBOW_FLEXION: Final = "elbow_flexion_deg"

_TORSO: Final = (
    KeypointName.LEFT_HIP,
    KeypointName.RIGHT_HIP,
    KeypointName.LEFT_SHOULDER,
    KeypointName.RIGHT_SHOULDER,
)

CROSSED_REQUIRED: Final = (
    *_TORSO,
    KeypointName.LEFT_ELBOW,
    KeypointName.RIGHT_ELBOW,
    KeypointName.LEFT_WRIST,
    KeypointName.RIGHT_WRIST,
)

FLEXION_REQUIRED: Final = (
    KeypointName.LEFT_SHOULDER,
    KeypointName.LEFT_ELBOW,
    KeypointName.LEFT_WRIST,
    KeypointName.RIGHT_SHOULDER,
    KeypointName.RIGHT_ELBOW,
    KeypointName.RIGHT_WRIST,
)


def arms_crossed(resolver: KeypointResolver, thresholds: Thresholds) -> Metric:
    """How close each wrist sits to the opposite elbow, as a fraction of torso length."""
    resolution = resolver.require(*CROSSED_REQUIRED)
    if not isinstance(resolution, Resolved):
        return resolution.as_metric(ARMS_CROSSED, "ratio")

    points = world_points(ARMS_CROSSED, "ratio", resolution)
    if isinstance(points, Metric):
        return points

    torso = distance(
        midpoint(points[KeypointName.LEFT_HIP], points[KeypointName.RIGHT_HIP]),
        midpoint(points[KeypointName.LEFT_SHOULDER], points[KeypointName.RIGHT_SHOULDER]),
    )
    # `distance` is never negative, so a `<= 0.0` test would only catch the exact-zero case. A
    # torso of 1e-12 m divides the ratio up to ~1e11, which compares as "not folded" against any
    # threshold — a confident wrong answer from a measurement that does not exist. Same threshold
    # the geometry layer uses for a vector with no direction, because it is the same degeneracy.
    if torso < MIN_LENGTH:
        return abstain(
            ARMS_CROSSED,
            "ratio",
            "your shoulders and hips are at the same point in this photo, so there is no body "
            "length to measure against",
            inputs=CROSSED_REQUIRED,
        )

    def compute() -> float:
        # The *larger* of the two crossings, so both arms have to be folded in for the ratio to be
        # small. Taking the minimum would let one hand resting near the opposite elbow — a very
        # ordinary way to sit — read as fully folded arms.
        left = distance(points[KeypointName.LEFT_WRIST], points[KeypointName.RIGHT_ELBOW])
        right = distance(points[KeypointName.RIGHT_WRIST], points[KeypointName.LEFT_ELBOW])
        return max(left, right) / torso

    return measure(
        ARMS_CROSSED,
        "ratio",
        resolution,
        compute=compute,
        describe=lambda value: (
            "arms are folded across your chest"
            if value < thresholds.arms_crossed_ratio
            else "arms are not folded"
        ),
    )


def elbow_flexion_deg(resolver: KeypointResolver, thresholds: Thresholds) -> Metric:
    """The angle at the more bent of the two elbows.

    The *more* bent, rather than an average of the two. A bent elbow is the signal — an arm
    propped on a desk or folded across the chest — and averaging it with a straight arm yields a
    number that describes neither of them. The description names the side, so the report never
    implies the measurement applies to both.
    """
    resolution = resolver.require(*FLEXION_REQUIRED)
    if not isinstance(resolution, Resolved):
        return resolution.as_metric(ELBOW_FLEXION, "deg")

    points = world_points(ELBOW_FLEXION, "deg", resolution)
    if isinstance(points, Metric):
        return points

    sides = {
        "left": (KeypointName.LEFT_SHOULDER, KeypointName.LEFT_ELBOW, KeypointName.LEFT_WRIST),
        "right": (
            KeypointName.RIGHT_SHOULDER,
            KeypointName.RIGHT_ELBOW,
            KeypointName.RIGHT_WRIST,
        ),
    }
    measured: dict[str, float] = {}

    def compute() -> float:
        for side, (shoulder, elbow, wrist) in sides.items():
            measured[side] = angle_between(
                points[shoulder] - points[elbow], points[wrist] - points[elbow]
            )
        return min(measured.values())

    return measure(
        ELBOW_FLEXION,
        "deg",
        resolution,
        compute=compute,
        describe=lambda value: _describe_flexion(value, measured, thresholds),
    )


def _describe_flexion(value: float, measured: dict[str, float], thresholds: Thresholds) -> str:
    side = min(measured, key=lambda key: measured[key])
    if value < thresholds.elbow_flexed_deg:
        return f"your {side} elbow is bent to {value:.0f}°"
    return f"both elbows are fairly straight (the {side} is the more bent, at {value:.0f}°)"
