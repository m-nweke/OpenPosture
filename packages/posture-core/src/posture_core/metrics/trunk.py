"""``trunk_inclination_deg`` — how far forward or back the torso leans, in world space.

The project's central measurement, and the direct replacement for the legacy ``checkPosition``.
Three defects disappear here at once:

* **The ear-index inversion (FINDINGS §2.1).** ``API/config`` declared landmark 16 as the left ear
  and the code commented it as the right; the laterality flag ``f`` — which decided whether to
  apply ``degrees = 180 - degrees`` — was keyed off that misreading, so spine classification came
  out backwards for subjects facing one way. Here the sign comes from
  :meth:`~posture_core.resolver.KeypointResolver.forward_axis`, derived from the nose. There is no
  hand-maintained flag to get backwards.
* **The silent false negative (FINDINGS §2.5).** ``checkPosition`` returned ``None`` on failure and
  the caller printed "Straight back position." Here a missing hip produces a gap that says so.
* **Pixel thresholds (FINDINGS §2.6).** The angle is computed from world landmarks in metres, so
  the same posture at twice the camera distance gives the same number. Not normalised — measured
  in a space where scale never entered.

Sign convention: **positive is forward**, negative is reclining, zero is upright. Both directions
matter and are different postures, which is why the underlying primitive is signed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from posture_core.geometry import midpoint, signed_angle_to_vertical
from posture_core.keypoints import KeypointName
from posture_core.metrics._support import abstain, measure, world_points
from posture_core.resolver import FACING_INPUTS, Resolved
from posture_core.status import Metric

if TYPE_CHECKING:
    from posture_core.resolver import KeypointResolver
    from posture_core.thresholds import Thresholds

__all__ = ["NAME", "trunk_inclination_deg"]

NAME: Final = "trunk_inclination_deg"
UNIT: Final = "deg"

REQUIRED: Final = (
    KeypointName.LEFT_HIP,
    KeypointName.RIGHT_HIP,
    KeypointName.LEFT_SHOULDER,
    KeypointName.RIGHT_SHOULDER,
)
"""Both hips and both shoulders, rather than ``NECK`` and a single hip.

Midpoints of a pair are far steadier than either member: a lateral view puts one shoulder behind
the other, and the further one is routinely the less confident of the two. Averaging keeps the
torso axis on the body's midline instead of letting it swing toward whichever side the model saw
best.
"""


def trunk_inclination_deg(resolver: KeypointResolver, thresholds: Thresholds) -> Metric:
    """Signed angle of the hip-midpoint→shoulder-midpoint axis away from vertical."""
    resolution = resolver.require(*REQUIRED)
    if not isinstance(resolution, Resolved):
        return resolution.as_metric(NAME, UNIT)

    points = world_points(NAME, UNIT, resolution)
    if isinstance(points, Metric):
        return points

    forward = resolver.forward_axis()
    if forward is None:
        # Without a facing direction the *magnitude* of the lean is still computable, but its sign
        # is not — and an unsigned trunk angle would report a recline as a slouch. Refusing is the
        # only honest option: half an answer here is worse than none, because the half that is
        # missing is the half that decides the advice.
        return abstain(
            NAME,
            UNIT,
            "could not tell which way you are facing, so a forward lean cannot be told apart "
            "from leaning back",
            # The facing landmarks, not this metric's own. The hips and shoulders are fine — the
            # nose or the neck is what is missing, and a gap that listed four healthy keypoints
            # would point the user at the wrong thing while saying nothing about the real one.
            inputs=FACING_INPUTS,
        )

    hips = midpoint(points[KeypointName.LEFT_HIP], points[KeypointName.RIGHT_HIP])
    shoulders = midpoint(points[KeypointName.LEFT_SHOULDER], points[KeypointName.RIGHT_SHOULDER])

    return measure(
        NAME,
        UNIT,
        resolution,
        compute=lambda: signed_angle_to_vertical(shoulders - hips, forward),
        describe=lambda value: _describe(value, thresholds),
    )


def _describe(value: float, thresholds: Thresholds) -> str:
    if value >= thresholds.trunk_slouch_deg:
        return f"leaning {value:.0f}° forward, a pronounced slouch"
    if value > thresholds.trunk_upright_deg:
        return f"leaning {value:.0f}° forward, slightly hunched"
    if value <= thresholds.trunk_recline_deg:
        return f"leaning {abs(value):.0f}° back"
    return f"upright, within {abs(value):.0f}° of vertical"
