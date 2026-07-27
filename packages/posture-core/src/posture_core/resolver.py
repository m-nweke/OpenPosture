"""Turning a raw :class:`~posture_core.PoseFrame` into landmarks a metric is allowed to use.

Every metric begins the same way: name the keypoints it needs, and either get them — all present,
all confident — or get a structured refusal it can return unchanged. Doing that once, here, is
what keeps each metric module about geometry instead of about defensive checks, and it is what
makes "we abstained, and here is why" the *default* behaviour rather than something each metric
has to remember to do.

The resolver is also where a frame's **orientation** is worked out, because that is interpretation
of the frame rather than a property of any one metric. See :meth:`KeypointResolver.forward_axis`.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, TypeAlias

import numpy as np

from posture_core.geometry import UP, Vector3, norm, world_vec
from posture_core.keypoints import KeypointName
from posture_core.status import Gap, KeypointStatus, Metric, MetricStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from posture_core.keypoints import Landmark, PoseFrame
    from posture_core.thresholds import Thresholds

__all__ = ["KeypointResolver", "Resolution", "Resolved", "Unresolved"]


@dataclasses.dataclass(frozen=True, slots=True)
class Resolved:
    """Every requested keypoint was present and confident."""

    landmarks: Mapping[KeypointName, Landmark]
    confidence: float
    """The weakest input's visibility — see the note on :attr:`~posture_core.status.Metric`."""

    def world(self, name: KeypointName) -> Vector3 | None:
        return world_vec(self.landmarks[name])

    def __getitem__(self, name: KeypointName) -> Landmark:
        return self.landmarks[name]


@dataclasses.dataclass(frozen=True, slots=True)
class Unresolved:
    """At least one requested keypoint is unusable, with the specifics retained.

    Carries enough detail to become a :class:`~posture_core.status.Gap` without the metric having
    to reconstruct anything — which is the point, because a metric that had to explain its own
    absence would be a metric that could get the explanation wrong.
    """

    status: MetricStatus
    problems: Mapping[KeypointName, KeypointStatus]

    def as_metric(self, name: str, unit: str) -> Metric:
        """The empty metric to return from a computation that cannot proceed."""
        return Metric(
            name=name,
            value=None,
            unit=unit,
            status=self.status,
            detail=self._detail(),
            inputs=tuple(self.problems),
        )

    def as_gap(self, metric: str) -> Gap:
        return Gap(
            metric=metric, status=self.status, detail=self._detail(), keypoints=self.problems
        )

    def _detail(self) -> str:
        """Phrased for the person in the photograph, not for a log line.

        The whole value of the status model is that the user hears something actionable, so the
        wording is grouped by remedy: reframe the shot, or improve the view of what is already in
        it.
        """
        missing = sorted(
            name.value.replace("_", " ")
            for name, status in self.problems.items()
            if status in (KeypointStatus.NOT_DETECTED, KeypointStatus.OUT_OF_FRAME)
        )
        unclear = sorted(
            name.value.replace("_", " ")
            for name, status in self.problems.items()
            if status is KeypointStatus.LOW_CONFIDENCE
        )

        parts = []
        if missing:
            parts.append(f"could not see {_join(missing)} in the photo")
        if unclear:
            parts.append(f"{_join(unclear)} {'was' if len(unclear) == 1 else 'were'} unclear")
        return "; ".join(parts) if parts else "required landmarks were unavailable"


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


Resolution: TypeAlias = Resolved | Unresolved


class KeypointResolver:
    """Reads one frame, under one set of thresholds.

    Constructed per report rather than per metric so the per-keypoint status is computed once and
    every metric in the report agrees about which landmarks were usable. Two metrics disagreeing
    about whether the left knee was visible would be a genuinely confusing bug to chase.
    """

    __slots__ = ("_frame", "_statuses", "_thresholds")

    def __init__(self, frame: PoseFrame, thresholds: Thresholds) -> None:
        self._frame = frame
        self._thresholds = thresholds
        self._statuses: dict[KeypointName, KeypointStatus] = {
            name: self._classify(frame.get(name)) for name in KeypointName
        }

    def _classify(self, landmark: Landmark | None) -> KeypointStatus:
        if landmark is None:
            return KeypointStatus.NOT_DETECTED
        # Presence is checked first and separately: "not in the picture" is a stronger and more
        # actionable statement than "hard to see", and a point that is out of frame is also
        # necessarily low-visibility, so the other order would mask it.
        if landmark.presence < self._thresholds.min_presence:
            return KeypointStatus.OUT_OF_FRAME
        if landmark.visibility < self._thresholds.min_visibility:
            return KeypointStatus.LOW_CONFIDENCE
        return KeypointStatus.OK

    @property
    def frame(self) -> PoseFrame:
        return self._frame

    def status(self, name: KeypointName) -> KeypointStatus:
        return self._statuses[name]

    @property
    def statuses(self) -> Mapping[KeypointName, KeypointStatus]:
        """Every keypoint's status, for the report's quality section."""
        return dict(self._statuses)

    def require(self, *names: KeypointName) -> Resolution:
        """All of ``names``, or a structured explanation of why not.

        A metric never inspects landmark confidence itself. It states its inputs and receives
        either usable data or a refusal — so "abstain honestly" is the path of least resistance
        rather than the path someone has to remember.
        """
        problems = {
            name: self._statuses[name]
            for name in names
            if self._statuses[name] is not KeypointStatus.OK
        }
        if problems:
            # A missing keypoint outranks an unclear one: it is the more fundamental obstacle and
            # the one whose remedy — reframe the shot — subsumes the other.
            structural = any(
                status in (KeypointStatus.NOT_DETECTED, KeypointStatus.OUT_OF_FRAME)
                for status in problems.values()
            )
            return Unresolved(
                status=(
                    MetricStatus.INSUFFICIENT_KEYPOINTS
                    if structural
                    else MetricStatus.LOW_CONFIDENCE
                ),
                problems=problems,
            )

        landmarks = {name: self._frame.landmarks[name] for name in names}
        return Resolved(
            landmarks=landmarks,
            confidence=min(landmark.visibility for landmark in landmarks.values()),
        )

    def forward_axis(self) -> Vector3 | None:
        """Which way the subject is facing, as a horizontal unit vector, or ``None``.

        Signed lean is meaningless without this. Leaning 25° toward image-right is a slouch for
        someone facing right and a recline for someone facing left, so the sign has to be taken
        relative to the *subject*, not the frame.

        Derived from the nose relative to the shoulder midpoint, with the vertical component
        removed — the face points forward, and unlike the legacy engine's approach this needs no
        hand-maintained laterality flag keyed off landmark indices. That flag was read from a
        misdocumented config and made spine classification backwards for one facing direction
        (FINDINGS §2.1); there is nothing here to get backwards.

        Facing the camera squarely returns a *valid* axis pointing along the view direction, not
        ``None``. World landmarks are metric 3D, so the sagittal plane is still well defined and
        the lean is still recovered — this docstring previously claimed otherwise, written before
        that was measured. What degrades head-on is the reliability of the depth estimate, which
        ``view_confidence`` (OP-31) turns into reduced confidence rather than an abstention.

        ``None`` means the axis genuinely cannot be derived: no nose or no neck, no world
        coordinates, or a face pointing straight up or down so there is no horizontal component.
        """
        nose = self._frame.get(KeypointName.NOSE)
        neck = self._frame.get(KeypointName.NECK)
        if nose is None or neck is None:
            return None

        nose_world, neck_world = world_vec(nose), world_vec(neck)
        if nose_world is None or neck_world is None:
            return None

        direction = nose_world - neck_world
        horizontal = direction - np.dot(direction, UP) * UP
        length = norm(horizontal)
        if length < 1e-6:
            # The face points straight up or straight down: no usable horizontal facing.
            return None
        return np.asarray(horizontal / length, dtype=np.float64)
