"""``craniovertebral_angle_deg`` — forward head posture, measured the way clinicians measure it.

The craniovertebral angle is the angle at C7 between the **horizontal** and the line from C7 to
the ear. Head balanced over the shoulders gives a large angle; head jutting forward gives a small
one. Below about 50° is the standard cutoff for forward head posture.

**Smaller is worse here**, which is the opposite of every other angular threshold in the project
and the single easiest thing to get backwards.

## What this replaces

``evaluate_neck_posture`` in the legacy engine compared the *y* coordinate of the shoulder
midpoint against the *y* coordinate of COCO keypoint 1 — and COCO keypoint 1 **was** the shoulder
midpoint, synthesised from the two shoulders rather than observed. It compared a point against
itself, so it could only ever return one answer regardless of the photograph (FINDINGS §2.2).
Making the neck's derivation explicit in the adapter (ADR-0002) is what exposed that; measuring an
actual angle to an actual ear is what fixes it.

## A deliberate property

A subject who leans their whole trunk forward while keeping their head in line with it still
scores a reduced angle, because the reference is gravity rather than the torso. That is correct:
forward head posture is defined against the vertical, and the load on the neck depends on where
the head is in space, not on where it is relative to a tilted spine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from posture_core.geometry import angle_between, midpoint
from posture_core.keypoints import KeypointName
from posture_core.metrics._support import abstain, measure, world_points
from posture_core.resolver import Resolved
from posture_core.status import Metric

if TYPE_CHECKING:
    from posture_core.resolver import KeypointResolver
    from posture_core.thresholds import Thresholds

__all__ = ["NAME", "craniovertebral_angle_deg"]

NAME: Final = "craniovertebral_angle_deg"
UNIT: Final = "deg"

REQUIRED: Final = (
    KeypointName.LEFT_EAR,
    KeypointName.RIGHT_EAR,
    KeypointName.LEFT_SHOULDER,
    KeypointName.RIGHT_SHOULDER,
)
"""Both ears and both shoulders.

The ear *midpoint* rather than whichever ear the camera happens to favour: in a lateral view the
far ear is occluded and its position is largely inferred, so taking one side would make the
measurement depend on which way the subject sat. The shoulder midpoint stands in for C7, which is
the same derivation the adapter uses for ``NECK`` (ADR-0002).
"""


def craniovertebral_angle_deg(resolver: KeypointResolver, thresholds: Thresholds) -> Metric:
    resolution = resolver.require(*REQUIRED)
    if not isinstance(resolution, Resolved):
        return resolution.as_metric(NAME, UNIT)

    points = world_points(NAME, UNIT, resolution)
    if isinstance(points, Metric):
        return points

    forward = resolver.forward_axis()
    if forward is None:
        return abstain(
            NAME,
            UNIT,
            "could not tell which way you are facing, so head position relative to your "
            "shoulders cannot be measured",
            inputs=REQUIRED,
        )

    c7 = midpoint(points[KeypointName.LEFT_SHOULDER], points[KeypointName.RIGHT_SHOULDER])
    ear = midpoint(points[KeypointName.LEFT_EAR], points[KeypointName.RIGHT_EAR])

    return measure(
        NAME,
        UNIT,
        resolution,
        # Unsigned: the angle to the horizontal is what the clinical measure is defined as, and
        # values above 90° simply mean the head sits behind the shoulders, which is unusual but
        # not an error.
        compute=lambda: angle_between(ear - c7, forward),
        describe=lambda value: _describe(value, thresholds),
    )


def _describe(value: float, thresholds: Thresholds) -> str:
    if value < thresholds.cva_forward_head_deg:
        return f"head is well forward of your shoulders (craniovertebral angle {value:.0f}°)"
    if value < thresholds.cva_borderline_deg:
        return f"head is slightly forward (craniovertebral angle {value:.0f}°)"
    return f"head is balanced over your shoulders (craniovertebral angle {value:.0f}°)"
