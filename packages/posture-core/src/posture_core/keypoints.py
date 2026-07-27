"""The canonical skeleton: the vocabulary every other module in the project speaks.

These types live in ``posture_core`` — the package that depends on nothing heavier than numpy —
and *not* in ``pose_backends``, which is where the inference libraries live. That inversion is
deliberate and it is the load-bearing idea of the whole architecture:

    the rules engine defines the contract, and inference adapters conform to it

Put the types next to MediaPipe instead and the dependency arrow flips. ``posture_core`` would
then need an inference stack installed just to describe a shoulder, its test suite would need a
model, and swapping backends would mean editing rules. Defining them here means a backend is a
file you add and a file you delete.

Nothing in this module knows that MediaPipe exists. ``KeypointName`` members are project-owned
names, not MediaPipe indices and not COCO indices; the integer mapping is private to each adapter
(see ``pose_backends/mediapipe_backend.py``). Rules code never sees a magic number — which is
precisely how the legacy engine's ear-index inversion (FINDINGS §2.1) became possible.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping
from enum import StrEnum
from types import MappingProxyType

__all__ = ["KeypointName", "Landmark", "PoseFrame"]


class KeypointName(StrEnum):
    """Every point the canonical skeleton can carry.

    A ``StrEnum`` rather than a plain ``Enum`` so members serialise to readable JSON without a
    custom encoder — ``"left_shoulder"``, not ``"KeypointName.LEFT_SHOULDER"``. The CLI (OP-21),
    the golden fixtures (OP-43) and the API response all get that for free.

    The set is the 33 landmarks MediaPipe produces, plus :attr:`NECK`, which no backend provides
    directly. Members a given backend cannot supply are simply *absent* from a
    :class:`PoseFrame`'s mapping — absence is the honest representation, and it is what lets a
    17-point backend (the ONNX MoveNet escape hatch in ADR-0002) conform to the same schema
    without inventing coordinates it never measured.
    """

    # --- head -------------------------------------------------------------------------------
    NOSE = "nose"
    LEFT_EYE_INNER = "left_eye_inner"
    LEFT_EYE = "left_eye"
    LEFT_EYE_OUTER = "left_eye_outer"
    RIGHT_EYE_INNER = "right_eye_inner"
    RIGHT_EYE = "right_eye"
    RIGHT_EYE_OUTER = "right_eye_outer"
    LEFT_EAR = "left_ear"
    RIGHT_EAR = "right_ear"
    MOUTH_LEFT = "mouth_left"
    MOUTH_RIGHT = "mouth_right"

    # --- upper body -------------------------------------------------------------------------
    LEFT_SHOULDER = "left_shoulder"
    RIGHT_SHOULDER = "right_shoulder"
    LEFT_ELBOW = "left_elbow"
    RIGHT_ELBOW = "right_elbow"
    LEFT_WRIST = "left_wrist"
    RIGHT_WRIST = "right_wrist"
    LEFT_PINKY = "left_pinky"
    RIGHT_PINKY = "right_pinky"
    LEFT_INDEX = "left_index"
    RIGHT_INDEX = "right_index"
    LEFT_THUMB = "left_thumb"
    RIGHT_THUMB = "right_thumb"

    # --- lower body -------------------------------------------------------------------------
    LEFT_HIP = "left_hip"
    RIGHT_HIP = "right_hip"
    LEFT_KNEE = "left_knee"
    RIGHT_KNEE = "right_knee"
    LEFT_ANKLE = "left_ankle"
    RIGHT_ANKLE = "right_ankle"
    LEFT_HEEL = "left_heel"
    RIGHT_HEEL = "right_heel"
    LEFT_FOOT_INDEX = "left_foot_index"
    RIGHT_FOOT_INDEX = "right_foot_index"

    # --- derived ----------------------------------------------------------------------------
    NECK = "neck"
    """Midpoint of the two shoulders. **Derived in the adapter, never in a rule.**

    MediaPipe has no neck landmark, and neither did the legacy engine really: COCO keypoint 1 was
    itself synthesised from the two shoulders. Deriving it once, in the adapter, is what makes
    every backend present the same skeleton — and making the derivation explicit is what exposed
    FINDINGS §2.2, where ``evaluate_neck_posture`` compared the shoulder midpoint's *y* against
    the shoulder midpoint's *y* and could therefore only ever return one answer.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class Landmark:
    """One measured point on the body.

    Two coordinate systems, and the difference between them is the reason this project changed
    models at all (ADR-0002, ADR-0005):

    * :attr:`x` / :attr:`y` are **image space**, normalised to the frame's width and height. Good
      for drawing an overlay; useless for a threshold, because the same posture photographed from
      twice the distance produces half the pixel distances. That is FINDINGS §2.6 — the legacy
      engine's central defect.
    * :attr:`x_world` / :attr:`y_world` / :attr:`z_world` are **metres**, origin at the hip
      midpoint. An angle computed from these is invariant to resolution and subject distance by
      construction, so scale invariance stops being a normalisation problem and becomes a choice
      of coordinate system.

    Normalised coordinates are deliberately **not** range-validated. MediaPipe emits values
    outside ``[0, 1]`` for joints it has extrapolated past the frame edge, and that is real
    information — clamping or rejecting it here would destroy the signal the ``OUT_OF_FRAME``
    keypoint status (OP-25) is built on.
    """

    x: float
    """Horizontal position in image space, normalised by frame width. May fall outside [0, 1]."""

    y: float
    """Vertical position in image space, normalised by frame height, origin top-left."""

    visibility: float
    """[0, 1] — confidence the point is *visible*, i.e. not occluded by the body or an object."""

    presence: float
    """[0, 1] — confidence the point is *in frame at all*.

    Independent of :attr:`visibility`, and the pairing is the point: low visibility with high
    presence means occluded, low presence means out of frame. The legacy engine had no confidence
    signal whatsoever, which is why a failed assessment surfaced as "Straight back position"
    (FINDINGS §2.5) rather than as an honest "I could not see your knees".
    """

    x_world: float | None = None
    """Metres, hip-midpoint origin. ``None`` on backends that produce no metric 3D."""

    y_world: float | None = None
    z_world: float | None = None

    def __post_init__(self) -> None:
        # World coordinates are all-or-nothing. A backend either reconstructs metric 3D or it does
        # not; a half-populated triple could only come from an adapter bug, and letting it through
        # would surface much later as a nonsensical angle rather than as an obviously bad frame.
        world = (self.x_world, self.y_world, self.z_world)
        if any(c is None for c in world) and any(c is not None for c in world):
            raise ValueError(
                "world coordinates must be all present or all absent, "
                f"got x_world={self.x_world!r}, y_world={self.y_world!r}, "
                f"z_world={self.z_world!r}"
            )

    @property
    def has_world(self) -> bool:
        """Whether this landmark carries metric 3D, and so whether world-space rules may use it."""
        return self.x_world is not None


@dataclasses.dataclass(frozen=True, slots=True)
class PoseFrame:
    """One person's skeleton, detected in one image, by one backend.

    This is the entire surface between inference and rules. Everything downstream — metrics,
    findings, the report, the API response, the canvas overlay — is a pure function of a
    ``PoseFrame``, which is what lets the rules engine be tested with hand-built skeletons and no
    model installed (OP-33, OP-40).

    :attr:`backend` and :attr:`inference_ms` travel *with* the data rather than being logged
    beside it, so a stored report can always answer "which engine produced this, and how long did
    it take" — the raw material for the legacy-vs-new comparison in ``docs/evaluation.md``.
    """

    landmarks: Mapping[KeypointName, Landmark]
    """Detected points only.

    A missing key means "this backend did not report this point", never "confidence zero". Callers
    must handle absence; :meth:`get` and ``in`` are the intended way to do it.
    """

    image_width: int
    image_height: int
    backend: str
    """Identifier of the adapter that produced this frame, e.g. ``"mediapipe"`` or ``"fake"``."""

    inference_ms: float
    """Wall-clock time spent inside ``detect()``, for the CLI table and latency diagnostics."""

    def __post_init__(self) -> None:
        # A frozen dataclass freezes the *attribute binding*, not the object bound to it — without
        # this, `frame.landmarks[NECK] = ...` would mutate a "frozen" frame quite happily. Copying
        # into a MappingProxyType makes the mapping genuinely read-only and detaches it from the
        # dict the adapter built, which the adapter is otherwise free to keep mutating.
        object.__setattr__(self, "landmarks", MappingProxyType(dict(self.landmarks)))

        if self.image_width <= 0 or self.image_height <= 0:
            raise ValueError(
                f"image dimensions must be positive, got {self.image_width}x{self.image_height}"
            )

    def get(self, name: KeypointName) -> Landmark | None:
        """The landmark for ``name``, or ``None`` if this backend did not report it."""
        return self.landmarks.get(name)

    def __contains__(self, name: KeypointName) -> bool:
        return name in self.landmarks

    def __iter__(self) -> Iterator[KeypointName]:
        return iter(self.landmarks)

    def __len__(self) -> int:
        return len(self.landmarks)

    @property
    def has_world_landmarks(self) -> bool:
        """Whether every reported landmark carries metric 3D.

        The switch ADR-0005 turns on: ``True`` means angular metrics may be computed in world
        space and are scale-invariant for free; ``False`` means falling back to image space with
        torso-length normalisation, with the accuracy loss that implies. An empty frame is
        ``False`` — no evidence is not evidence of world coordinates.
        """
        return bool(self.landmarks) and all(lm.has_world for lm in self.landmarks.values())
