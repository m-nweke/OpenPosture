"""``knee_flexion_deg`` — the hip-knee-ankle angle, in world space.

Replaces ``checkKneeling``, which raised ``UnboundLocalError`` on a path where its result variable
was never assigned. The function could not even fail cleanly, let alone report kneeling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from posture_core.geometry import angle_between
from posture_core.keypoints import KeypointName
from posture_core.metrics._support import measure, require_either_side, world_points
from posture_core.status import Metric

if TYPE_CHECKING:
    from posture_core.resolver import KeypointResolver
    from posture_core.thresholds import Thresholds

__all__ = ["KNEE_FLEXION", "knee_flexion_deg"]

KNEE_FLEXION: Final = "knee_flexion_deg"

SIDES: Final = {
    "left": (KeypointName.LEFT_HIP, KeypointName.LEFT_KNEE, KeypointName.LEFT_ANKLE),
    "right": (KeypointName.RIGHT_HIP, KeypointName.RIGHT_KNEE, KeypointName.RIGHT_ANKLE),
}


def knee_flexion_deg(resolver: KeypointResolver, thresholds: Thresholds) -> Metric:
    """The hip-knee-ankle angle on whichever leg the camera actually saw.

    One leg, not both. In the lateral view this application asks users for, the far leg sits behind
    the torso and is reported below the visibility threshold — correctly. Requiring both made every
    one of the eight fixtures abstain. See
    :func:`~posture_core.metrics._support.require_either_side`.
    """
    resolved = require_either_side(resolver, SIDES)
    if not isinstance(resolved, tuple):
        return resolved.as_metric(KNEE_FLEXION, "deg")
    side, resolution = resolved

    points = world_points(KNEE_FLEXION, "deg", resolution)
    if isinstance(points, Metric):
        return points

    hip, knee, ankle = SIDES[side]

    return measure(
        KNEE_FLEXION,
        "deg",
        resolution,
        compute=lambda: angle_between(points[hip] - points[knee], points[ankle] - points[knee]),
        describe=lambda value: _describe(value, side, thresholds),
    )


def _describe(value: float, side: str, thresholds: Thresholds) -> str:
    """Every band names the side, and none of them says "legs" or "knees".

    Only one leg was measured. A description that implies both is not a wording nit — it is the
    metric claiming coverage it does not have. The band ordering here is what
    :meth:`~posture_core.thresholds.Thresholds.__post_init__` validates: kneeling is tested first,
    so a ``knee_kneeling_max_deg`` at or above ``knee_seated_min_deg`` would make the tucked-back
    band unreachable.
    """
    if value <= thresholds.knee_kneeling_max_deg:
        return f"your {side} knee is folded to {value:.0f}°, which looks like kneeling"
    if value < thresholds.knee_seated_min_deg:
        return f"your {side} knee is tucked back sharply, at {value:.0f}°"
    if value <= thresholds.knee_seated_max_deg:
        return f"your {side} knee is at a comfortable seated angle ({value:.0f}°)"
    return f"your {side} leg is extended, with the knee at {value:.0f}°"
