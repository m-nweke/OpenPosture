"""Test scaffolding: build a resolver around an analytic stick figure in one line.

The figure itself comes from :mod:`posture_core.synthetic`, which is shipped code because
``FakePoseBackend`` depends on it too (OP-19). What lives here is only the *test-facing* wrapper —
frame construction, resolver construction, threshold overrides — so a metric test reads as a
statement about posture:

    assert value_of(trunk_inclination_deg, trunk_deg=35) == approx(35, abs=1.0)

That is the whole ambition. A metric checked against a photograph can only be compared to somebody's
guess at the true angle; a metric checked against a figure *constructed* at 35° has a known-correct
answer, so the test is a statement about geometry rather than about eyeballing a JPEG.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from posture_core import PoseFrame
from posture_core.resolver import KeypointResolver
from posture_core.status import Metric
from posture_core.synthetic import make_pose
from posture_core.thresholds import DEFAULT_THRESHOLDS, Thresholds

if TYPE_CHECKING:
    from collections.abc import Callable

    from posture_core.keypoints import KeypointName, Landmark

__all__ = [
    "flat_resolver",
    "frame",
    "landmarks_of",
    "metric_of",
    "resolver",
    "unclear",
    "value_of",
    "with_thresholds",
    "without",
]

# A seated side-on subject: thigh near horizontal, shank near vertical. The posture the whole
# application is aimed at, and the baseline every metric test departs from.
SEATED: dict[str, float] = {"thigh_deg": 85.0, "shank_deg": 5.0}


def frame(*, image_width: int = 640, image_height: int = 480, **pose_kwargs: Any) -> PoseFrame:
    """A `PoseFrame` around a synthetic figure. Seated unless the caller says otherwise."""
    kwargs: dict[str, Any] = {**SEATED, **pose_kwargs}
    return PoseFrame(
        landmarks=make_pose(image_width=image_width, image_height=image_height, **kwargs),
        image_width=image_width,
        image_height=image_height,
        backend="synthetic",
        inference_ms=0.0,
    )


def resolver(
    *, thresholds: Thresholds = DEFAULT_THRESHOLDS, **pose_kwargs: Any
) -> KeypointResolver:
    return KeypointResolver(frame(**pose_kwargs), thresholds)


def metric_of(
    compute: Callable[[KeypointResolver, Thresholds], Metric],
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    **pose_kwargs: Any,
) -> Metric:
    """Run one metric against a figure described by keyword arguments."""
    return compute(resolver(thresholds=thresholds, **pose_kwargs), thresholds)


def value_of(
    compute: Callable[[KeypointResolver, Thresholds], Metric],
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    **pose_kwargs: Any,
) -> float:
    """The metric's value, asserting it actually produced one.

    Asserting rather than returning ``None`` keeps the failure at the line that expected a number.
    A test that silently compared ``None`` to an expected angle would fail with a confusing
    TypeError several frames away from the thing that went wrong.
    """
    metric = metric_of(compute, thresholds=thresholds, **pose_kwargs)
    assert metric.value is not None, f"{metric.name} abstained: {metric.status} — {metric.detail}"
    return metric.value


def with_thresholds(**overrides: Any) -> Thresholds:
    """Defaults with a few values changed — the pattern every boundary test uses."""
    return dataclasses.replace(DEFAULT_THRESHOLDS, **overrides)


def without(*names: KeypointName) -> dict[str, Any]:
    """Keyword arguments that drop keypoints entirely, for degradation tests."""
    return {"omit": names}


def unclear(
    *names: KeypointName, visibility: float = 0.2, presence: float = 0.95
) -> dict[str, Any]:
    """Keyword arguments that make keypoints occluded — visible-but-unclear, still in frame."""
    return {"confidence": dict.fromkeys(names, (visibility, presence))}


def landmarks_of(**pose_kwargs: Any) -> dict[KeypointName, Landmark]:
    return make_pose(**{**SEATED, **pose_kwargs})


def flat_resolver(
    *, thresholds: Thresholds = DEFAULT_THRESHOLDS, **pose_kwargs: Any
) -> KeypointResolver:
    """A resolver over a frame with **no world coordinates**.

    Stands in for the 2D-only backend ADR-0002 keeps as an escape hatch. Every metric here is
    defined in metric world space, so each one must abstain loudly rather than quietly computing
    an angle in a pixel space stretched by the frame's aspect ratio.
    """
    from posture_core import Landmark as _Landmark

    original = frame(**pose_kwargs)
    flat = {
        name: _Landmark(
            x=landmark.x,
            y=landmark.y,
            visibility=landmark.visibility,
            presence=landmark.presence,
        )
        for name, landmark in original.landmarks.items()
    }
    return KeypointResolver(
        PoseFrame(
            landmarks=flat,
            image_width=original.image_width,
            image_height=original.image_height,
            backend="flat",
            inference_ms=0.0,
        ),
        thresholds,
    )
