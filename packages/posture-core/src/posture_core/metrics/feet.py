"""``heel_contact_m`` — whether the feet are actually resting on the floor.

**A capability the original project listed as a goal and never delivered.** The 18-point COCO
schema it used has no foot landmarks at all, so its feet check was a tautology: it returned "on
the floor" for a fixture whose subject's feet visibly dangle (FINDINGS §2.3). MediaPipe reports
``LEFT_HEEL(29)``, ``RIGHT_HEEL(30)``, ``LEFT_FOOT_INDEX(31)`` and ``RIGHT_FOOT_INDEX(32)``, which
is what makes an actual measurement possible — the third of the three reasons the model was chosen
(ADR-0002).

The measurement is **how far the heel sits above its own toe**, in metres. A foot resting flat has
heel and toe at nearly the same height; an unsupported foot plantarflexes, dropping the toes and
lifting the heel. Relative height rather than absolute floor position, because there is no floor in
the coordinate system: world landmarks are hip-origin and nothing in the frame says where the
ground is.

## The margin is thin, and that is worth knowing

Measured across the eight fixtures with the real backend, the dangling-feet photograph scores
0.063 m and the seated ones fall between 0.01 m and 0.07 m. The signal is real and it points the
right way, but the default 0.05 m tolerance sits close to the spread of the supported cases rather
than comfortably outside it. Treat this metric as the least settled in the package until the
evaluation set in Epic H gives it a proper sample.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from posture_core.keypoints import KeypointName
from posture_core.metrics._support import measure, require_either_side, world_points
from posture_core.resolver import Resolved
from posture_core.status import Metric

if TYPE_CHECKING:
    from posture_core.resolver import KeypointResolver
    from posture_core.thresholds import Thresholds

__all__ = ["HEEL_CONTACT", "heel_contact_m"]

HEEL_CONTACT: Final = "heel_contact_m"

SIDES: Final = {
    "left": (KeypointName.LEFT_HEEL, KeypointName.LEFT_FOOT_INDEX),
    "right": (KeypointName.RIGHT_HEEL, KeypointName.RIGHT_FOOT_INDEX),
}


def heel_contact_m(resolver: KeypointResolver, thresholds: Thresholds) -> Metric:
    """How far the most-raised visible heel sits above its own toe, in metres.

    Positive means the heel is higher than the toe — a hanging foot. Around zero means flat.
    Negative, toe higher than heel, happens when someone rests back on their heel, and is reported
    as such rather than clamped to zero: clamping would turn a real and unusual foot position into
    a report of a perfectly ordinary one.

    **When both feet are usable, the larger rise wins — not the more confident foot.** The
    confidence-based choice made by :func:`require_either_side` is right for a *joint angle*, where
    either side answers the same question and the better-seen one answers it more reliably. It is
    wrong here: one unsupported foot is worth reporting, and picking by confidence would let a
    well-seen planted foot mask a dangling one. Confidence still decides when only one side
    resolves, which is the common case in a lateral view.
    """
    resolved = require_either_side(resolver, SIDES)
    if not isinstance(resolved, tuple):
        return resolved.as_metric(HEEL_CONTACT, "m")

    # At least one side is usable; take every side that is, so the worst foot is the one reported.
    usable = {
        name: candidate
        for name, keys in SIDES.items()
        if isinstance(candidate := resolver.require(*keys), Resolved)
    }
    # Rank only the sides that actually produced a number. `_rise` returns None for a foot with no
    # world coordinates, and None must not compete: folding it into the ranking as -inf would also
    # sink a *flat* foot, whose rise is 0.0 and therefore falsy. Two ways that goes wrong — a flat
    # foot losing to a tipped-back one whose rise is negative but truthy, and a measurable foot
    # tying with an unmeasurable one and then abstaining the whole metric on the coin toss.
    measurable = {
        name: rise
        for name, candidate in usable.items()
        if (rise := _rise(name, candidate)) is not None
    }
    if measurable:
        # `max` keeps the first of equal values, and SIDES is ordered left then right, so two feet
        # at the same height always report the left one rather than varying run to run.
        side = max(measurable, key=lambda name: measurable[name])
        resolution = usable[side]
    else:
        # No side has world coordinates at all. Carry on with the confidence-picked side so the
        # abstention below is the ordinary 2D-backend one rather than a special case here.
        side, resolution = resolved

    points = world_points(HEEL_CONTACT, "m", resolution)
    if isinstance(points, Metric):
        return points

    heel, toe = SIDES[side]

    # y is DOWN in world space, so "heel above toe" means heel_y < toe_y and the rise is
    # toe_y - heel_y. Getting this sign backwards would report every planted foot as dangling and
    # every dangling one as planted, with no error anywhere.
    return measure(
        HEEL_CONTACT,
        "m",
        resolution,
        compute=lambda: float(points[toe][1] - points[heel][1]),
        describe=lambda value: _describe(value, side, thresholds),
    )


def _rise(side: str, resolution: Resolved) -> float | None:
    """Heel height above toe for one already-resolved side, or ``None`` without world coordinates.

    Used only to pick between two usable feet. The chosen side is then measured through the normal
    path, so the value in the report comes from one place.
    """
    heel, toe = SIDES[side]
    heel_world, toe_world = resolution.world(heel), resolution.world(toe)
    if heel_world is None or toe_world is None:
        return None
    return float(toe_world[1] - heel_world[1])


def _describe(value: float, side: str, thresholds: Thresholds) -> str:
    if value > thresholds.heel_contact_tolerance_m:
        return (
            f"your {side} heel is {value * 100:.0f} cm above your toes, so that foot is not "
            "resting on the floor"
        )
    if value < -thresholds.heel_contact_tolerance_m:
        # The docstring promises this is reported rather than clamped, so it needs its own
        # sentence. Folding it into "flat and supported" would describe a foot tipped back on its
        # heel as an ordinary one, which is the sort of quiet inaccuracy this package exists to
        # avoid — and it is the posture of someone bracing against a chair with their toes up.
        return (
            f"your {side} toes are {abs(value) * 100:.0f} cm above your heel, so that foot is "
            "tipped back rather than flat"
        )
    return f"your {side} foot is flat and supported"
