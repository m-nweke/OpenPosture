"""Vector and angle primitives. The bottom layer of the rules engine.

Every metric in this package is ultimately one of these functions applied to two or three
landmarks. Keeping them here, separately tested, means a metric's own tests are about *posture*
rather than about trigonometry.

## The y-down world, stated once

MediaPipe's world landmarks — and therefore :mod:`posture_core.synthetic` — use metres with the
hip midpoint at the origin, ``x`` to the image right, ``y`` **down**, ``z`` depth. Every "up" in
this project is ``(0, -1, 0)``, and getting that backwards inverts every posture verdict without
raising anything. It is written down once, here, as :data:`UP`, and no other module hardcodes it.

## Degenerate input raises

An angle between a vector and nothing is not zero, not ninety, and not ``None`` — it is a
question with no answer. These functions raise :class:`DegenerateVectorError` for it, and the
metric layer (OP-25) turns that into an explicit ``MetricStatus`` rather than a number. Returning
a plausible default here is exactly how the legacy engine ended up reporting "Straight back
position" for images it had failed to assess.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, TypeAlias

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from posture_core.keypoints import Landmark

__all__ = [
    "GRAVITY",
    "UP",
    "DegenerateVectorError",
    "Vector3",
    "angle_between",
    "distance",
    "image_vec",
    "midpoint",
    "norm",
    "signed_angle_to_vertical",
    "unit",
    "world_vec",
]

Vector3: TypeAlias = NDArray[np.float64]
"""A 3-element float64 array.

An alias, not a class: numpy cannot check the length for us, and wrapping every point in a
validated type would cost an allocation per landmark per metric for a guarantee the builders and
the adapter already provide.
"""

UP: Final[Vector3] = np.array([0.0, -1.0, 0.0])
"""The direction of "up" in a y-down coordinate system. See the module docstring."""

GRAVITY: Final[Vector3] = -UP

# Below this length a vector's direction is numerical noise. 1e-9 m is a nanometre; any real
# anatomical segment is at least ~1e-2 m, so this only ever fires on coincident landmarks.
_MIN_LENGTH: Final = 1e-9


class DegenerateVectorError(ValueError):
    """A direction was requested from a vector that has none.

    Happens when two landmarks coincide — which a real backend can produce, for instance when a
    joint is fully occluded and the model collapses two points onto each other.
    """


def world_vec(landmark: Landmark) -> Vector3 | None:
    """The landmark's position in metres, or ``None`` if this backend has no metric 3D.

    ``None`` rather than an exception because the absence is an ordinary property of a backend
    (ADR-0002 keeps a 2D-only escape hatch), not a failure. Callers branch on it to choose
    world-space or image-space geometry.
    """
    if landmark.x_world is None or landmark.y_world is None or landmark.z_world is None:
        return None
    return np.array([landmark.x_world, landmark.y_world, landmark.z_world])


def image_vec(landmark: Landmark, image_width: int, image_height: int) -> Vector3:
    """The landmark's position in **pixels**, as a 3-vector with ``z = 0``.

    Multiplying the normalised coordinates back up by the frame size matters: an angle computed
    from raw normalised coordinates is measured in a space stretched by the aspect ratio, so a
    45° lean reads as something else on a 16:9 frame. That is a subtle enough error to survive a
    lot of review, which is why image-space work goes through this function and not through
    ``landmark.x`` directly.
    """
    return np.array([landmark.x * image_width, landmark.y * image_height, 0.0])


def norm(vector: Vector3) -> float:
    """Euclidean length. Provided so callers can check for degeneracy without catching."""
    return float(np.linalg.norm(vector))


def unit(vector: Vector3) -> Vector3:
    """The direction of ``vector``, length 1."""
    length = norm(vector)
    if length < _MIN_LENGTH:
        raise DegenerateVectorError(
            f"cannot take the direction of a zero-length vector (length {length:g})"
        )
    return vector / length


def distance(a: Vector3, b: Vector3) -> float:
    """Straight-line distance. Metres in world space, pixels in image space."""
    return float(np.linalg.norm(a - b))


def midpoint(a: Vector3, b: Vector3) -> Vector3:
    return (a + b) / 2.0


def angle_between(a: Vector3, b: Vector3) -> float:
    """Unsigned angle between two vectors, in degrees, always in ``[0, 180]``.

    Computed with ``atan2(|a x b|, a . b)`` rather than ``acos(a.b / |a||b|)``. The acos form loses
    precision badly for nearly-parallel vectors — the dot product approaches 1 where acos's
    derivative is unbounded — and can hand acos an argument fractionally outside ``[-1, 1]``,
    producing a ``nan`` that then propagates silently through a whole report. The atan2 form is
    well-conditioned everywhere and needs no clamping.
    """
    cross = float(np.linalg.norm(np.cross(a, b)))
    dot = float(np.dot(a, b))
    if norm(a) < _MIN_LENGTH or norm(b) < _MIN_LENGTH:
        raise DegenerateVectorError("cannot measure an angle against a zero-length vector")
    return float(np.degrees(np.arctan2(cross, dot)))


def signed_angle_to_vertical(vector: Vector3, forward: Vector3) -> float:
    """Angle of ``vector`` away from :data:`UP`, signed positive toward ``forward``.

    The sign is the whole point. Leaning forward 25° and reclining 25° are different postures with
    different advice attached, and an unsigned measure cannot tell them apart — which is what an
    ``abs()`` in the wrong place silently costs you.

    ``forward`` names the direction that counts as positive, so the caller decides rather than
    this function assuming. For a subject facing image-right it is ``(1, 0, 0)``; for one facing
    left it is ``(-1, 0, 0)``. Deriving it from the subject rather than the frame is what makes
    the result independent of which way they happen to be sitting — the legacy engine's ear-index
    laterality flag was an attempt at this, keyed off a misread config, and it made spine
    classification backwards for one facing direction (FINDINGS §2.1).

    Returns a value in ``(-180, 180]``.
    """
    if norm(vector) < _MIN_LENGTH:
        raise DegenerateVectorError("cannot measure the inclination of a zero-length vector")

    # Project onto the sagittal plane spanned by UP and `forward`, then read the angle off
    # directly. Components outside that plane — lateral sway — are deliberately ignored: this
    # measures forward/backward lean, and folding sideways lean into it would make a sideways
    # bend look like a slouch.
    forward_unit = unit(np.asarray(forward, dtype=np.float64))
    along_up = float(np.dot(vector, UP))
    along_forward = float(np.dot(vector, forward_unit))
    if abs(along_up) < _MIN_LENGTH and abs(along_forward) < _MIN_LENGTH:
        raise DegenerateVectorError(
            "vector has no component in the sagittal plane, so its inclination is undefined"
        )
    return float(np.degrees(np.arctan2(along_forward, along_up)))
