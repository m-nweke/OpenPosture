"""``view_confidence`` — is this photograph actually taken from the side?

The original application *told* users "this image must be taken from a side angle" and then
assessed whatever arrived. It had no way to check, and no way to express doubt if it had.

## What is measured

Shoulder separation divided by torso length, **in image space**. Face-on, the shoulders span most
of the subject's width and the ratio approaches their real proportions; side-on, one shoulder is
directly behind the other and the ratio collapses toward zero. Image space is the right space here
precisely *because* it is the projection — the question is what the camera could see, not what the
body is doing.

## Why this downgrades rather than vetoes — a correction to the plan

The plan treats a frontal view as fatal to sagittal metrics, on the reasoning that a slump
photographed head-on projects to nothing. That reasoning is sound for a 2D engine and wrong for
this one: world landmarks are metric 3D, so the facing axis comes out along the view direction and
the full lean *is* recovered. Measured against a synthetic figure built at 30°, the frontal
projection still reports 30.0°.

What actually degrades is **reliability**, not availability. MediaPipe reconstructs depth far less
accurately along the camera axis than across it, so a head-on measurement of forward lean rests on
the model's weakest dimension. So this metric produces a **confidence factor** the report applies
to sagittal findings, rather than a veto that discards them. A lower-confidence finding is more
useful to a user than a missing one, and more honest than a confident one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from posture_core.geometry import MIN_LENGTH, distance, image_vec, midpoint
from posture_core.keypoints import KeypointName
from posture_core.metrics._support import abstain, measure
from posture_core.resolver import Resolved
from posture_core.status import Metric

if TYPE_CHECKING:
    from posture_core.resolver import KeypointResolver
    from posture_core.thresholds import Thresholds

__all__ = ["NAME", "view_confidence", "view_confidence_factor"]

NAME: Final = "view_confidence"
UNIT: Final = "ratio"

REQUIRED: Final = (
    KeypointName.LEFT_SHOULDER,
    KeypointName.RIGHT_SHOULDER,
    KeypointName.LEFT_HIP,
    KeypointName.RIGHT_HIP,
)


def view_confidence(resolver: KeypointResolver, thresholds: Thresholds) -> Metric:
    """Shoulder span over torso length, as the camera saw them."""
    resolution = resolver.require(*REQUIRED)
    if not isinstance(resolution, Resolved):
        return resolution.as_metric(NAME, UNIT)

    frame = resolver.frame
    width, height = frame.image_width, frame.image_height
    points = {
        name: image_vec(landmark, width, height) for name, landmark in resolution.landmarks.items()
    }

    torso = distance(
        midpoint(points[KeypointName.LEFT_HIP], points[KeypointName.RIGHT_HIP]),
        midpoint(points[KeypointName.LEFT_SHOULDER], points[KeypointName.RIGHT_SHOULDER]),
    )
    # `distance` is never negative, so a `<= 0.0` test would only catch the exact-zero case — the
    # one a backend is least likely to produce. A torso a fraction of a pixel long divides the
    # ratio up to an enormous number, which reads as "photographed from the front" against any
    # threshold and then drags every sagittal finding down to the confidence floor. Same threshold
    # `arms_crossed` uses for the same division, and the same one the geometry layer uses for a
    # vector with no direction, because it is the same degeneracy.
    if torso < MIN_LENGTH:
        return abstain(
            NAME,
            UNIT,
            "your shoulders and hips are at the same point in this photo, so the camera angle "
            "cannot be judged",
            inputs=REQUIRED,
        )

    return measure(
        NAME,
        UNIT,
        resolution,
        compute=lambda: (
            distance(points[KeypointName.LEFT_SHOULDER], points[KeypointName.RIGHT_SHOULDER])
            / torso
        ),
        describe=lambda value: _describe(value, thresholds),
    )


def _describe(value: float, thresholds: Thresholds) -> str:
    if value <= thresholds.lateral_view_max_ratio:
        return "photographed from the side, which is the best angle for this assessment"
    if value >= thresholds.frontal_view_min_ratio:
        return (
            "photographed from the front, so forward lean is measurable but less reliable here. "
            "A side-on photo would give a firmer answer"
        )
    return "photographed at an angle between side-on and front-on"


def view_confidence_factor(ratio: float | None, thresholds: Thresholds) -> float:
    """How much to trust a sagittal measurement taken from this angle, in ``[0.4, 1.0]``.

    A linear ramp between the two configured ratios: full confidence side-on, falling to 0.4
    face-on. Linear because there is no evidence for any particular curve — inventing one would
    dress a guess up as a model. The floor is 0.4 rather than 0 because the measurement genuinely
    still works face-on; it is the depth estimate underneath it that weakens.

    ``None`` — the view could not be assessed at all — yields the floor. Assuming the best angle
    when we could not check is precisely the optimism this package exists to remove.
    """
    floor = 0.4
    if ratio is None:
        return floor
    if ratio <= thresholds.lateral_view_max_ratio:
        return 1.0
    if ratio >= thresholds.frontal_view_min_ratio:
        return floor
    span = thresholds.frontal_view_min_ratio - thresholds.lateral_view_max_ratio
    position = (ratio - thresholds.lateral_view_max_ratio) / span
    return 1.0 - position * (1.0 - floor)
