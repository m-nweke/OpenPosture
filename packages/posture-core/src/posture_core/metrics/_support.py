"""Shared plumbing for the metric modules.

Three jobs, all of them about making the honest path the easy one:

* wrap a computation so that a :class:`~posture_core.geometry.DegenerateVectorError` becomes a
  status rather than a traceback — **caught by its specific type**, never as ``except Exception``;
* fetch world coordinates, or abstain with an explanation if the backend has none;
* build the ``Metric`` so no metric module writes the ``value``/``status`` pairing by hand and
  risks getting the invariant wrong.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from posture_core.geometry import DegenerateVectorError, Vector3, world_vec
from posture_core.status import Metric, MetricStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from posture_core.keypoints import KeypointName
    from posture_core.resolver import Resolved

__all__ = ["abstain", "measure", "world_points"]


def abstain(
    name: str,
    unit: str,
    detail: str,
    inputs: tuple[KeypointName, ...] = (),
    status: MetricStatus = MetricStatus.UNDEFINED_GEOMETRY,
) -> Metric:
    """A metric that declines to answer, with a reason a person can act on."""
    return Metric(name=name, value=None, unit=unit, status=status, detail=detail, inputs=inputs)


def world_points(
    name: str, unit: str, resolution: Resolved
) -> Mapping[KeypointName, Vector3] | Metric:
    """Metric-space coordinates for every resolved landmark, or a metric explaining their absence.

    Returning the abstention rather than raising keeps the caller's shape uniform: a metric module
    checks ``isinstance(..., Metric)`` once and is otherwise free of branching.

    A backend with no metric 3D is not a broken backend — ADR-0002 keeps a 2D-only escape hatch —
    but every metric in this package is defined in world space, where scale invariance is free.
    The image-space fallback ADR-0005 describes would only be needed if that hatch were ever taken,
    and OP-16's spike descoped it. So this abstains loudly instead of silently computing angles in
    a space stretched by the frame's aspect ratio.
    """
    points: dict[KeypointName, Vector3] = {}
    for keypoint, landmark in resolution.landmarks.items():
        vector = world_vec(landmark)
        if vector is None:
            return abstain(
                name,
                unit,
                "this backend reports no world coordinates, and every metric here is measured "
                "in metres — see ADR-0005",
                inputs=tuple(resolution.landmarks),
            )
        points[keypoint] = vector
    return points


def measure(
    name: str,
    unit: str,
    resolution: Resolved,
    compute: Callable[[], float],
    describe: Callable[[float], str],
) -> Metric:
    """Run ``compute``, turning an undefined geometry into an explicit status.

    The ``except`` clause names :class:`~posture_core.geometry.DegenerateVectorError` and nothing
    else. A bare ``except Exception`` here would swallow a genuine programming error — an index
    typo, a ``None`` where a vector was expected — and report it to the user as "we could not
    measure this", which is exactly the failure mode this package exists to eliminate. A real bug
    should crash loudly and be fixed.
    """
    try:
        value = compute()
    except DegenerateVectorError as exc:
        return abstain(
            name,
            unit,
            f"the landmarks needed for this measurement overlap, so it has no answer ({exc})",
            inputs=tuple(resolution.landmarks),
        )
    return Metric(
        name=name,
        value=value,
        unit=unit,
        status=MetricStatus.OK,
        detail=describe(value),
        inputs=tuple(resolution.landmarks),
        confidence=resolution.confidence,
    )
