"""Analytic stick-figure construction: skeletons built from angles rather than from photographs.

## Why this is shipped code and not a test helper

Two very different consumers need to build the same synthetic poses:

* ``FakePoseBackend`` (OP-19), which is *production* code — it is what lets the whole application
  run with ``POSE_BACKEND=fake``, so the container smoke test and the Playwright suite need no
  model weights and no secrets. That is the single reason CI stays fast.
* the rules-engine tests in Epic C (OP-33), which assert things like
  ``metric(make_pose(trunk_deg=35)).value == approx(35, abs=1.0)``.

If those two built their figures separately they would drift, and the day they drifted every
end-to-end assertion resting on a fake pose would quietly stop meaning anything. One builder, in
the package both already depend on.

It also inverts the usual test-fixture problem. A metric tested against a photograph can only be
checked against a human's guess at the true angle; a metric tested against a figure *constructed*
at 35° has a known-correct answer. The tests become statements about geometry rather than about
someone's eyeballing of a JPEG.

## The coordinate system

Matches what MediaPipe's world landmarks provide, because that is what the real backend emits:
metres, **hip midpoint at the origin**, ``x`` to the image right, ``y`` **down**, ``z`` depth.
Image-space coordinates are an orthographic projection of the same figure — the depth axis is
simply dropped, which is exactly what a camera does to a subject much further away than they are
deep.

Everything is pure: no randomness, no clock, no I/O. The same arguments produce byte-identical
output forever, which is what ``FakePoseBackend``'s stability guarantee rests on.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypeAlias

from posture_core.keypoints import KeypointName, Landmark, PoseFrame

__all__ = [
    "Anthropometry",
    "Facing",
    "View",
    "make_pose",
    "make_pose_frame",
]

Vec3: TypeAlias = tuple[float, float, float]


class Facing(StrEnum):
    """Which way the subject's front points, for a lateral view."""

    RIGHT = "right"
    LEFT = "left"


class View(StrEnum):
    """Camera position relative to the subject.

    The distinction is not cosmetic. In :attr:`LATERAL` the subject's shoulder separation runs
    along the depth axis, so the *image* shows almost no shoulder width and a forward lean is
    plainly visible. In :attr:`FRONTAL` the two swap: shoulders are wide on screen and the lean
    disappears into depth.

    That ratio — shoulder width against torso length, in image space — is precisely what
    ``view_confidence`` (OP-31) measures, and this enum is what lets it be tested. The original
    app *told* users to photograph themselves from the side and then never checked.
    """

    LATERAL = "lateral"
    FRONTAL = "frontal"


@dataclass(frozen=True, slots=True)
class Anthropometry:
    """Segment lengths in metres, roughly a 50th-percentile adult.

    Injected rather than hardcoded so a test can shrink or stretch the figure and assert that an
    angular metric does not move — the scale-invariance property (OP-34) that the original engine
    would fail catastrophically, since its thresholds were raw pixel counts.
    """

    torso: float = 0.50
    """Hip midpoint to shoulder midpoint."""

    neck: float = 0.24
    """Shoulder midpoint to the centre of the head."""

    shoulder_width: float = 0.38
    hip_width: float = 0.30
    head_width: float = 0.16
    upper_arm: float = 0.30
    forearm: float = 0.27
    hand: float = 0.09
    thigh: float = 0.42
    shank: float = 0.42
    foot: float = 0.16
    heel_drop: float = 0.05
    """How far the heel sits below the ankle."""


# Where the hip midpoint lands in the frame, and how large the figure is drawn. Chosen so a
# seated figure fits comfortably at any common aspect ratio; nothing downstream depends on the
# exact numbers, because every metric is either angular or normalised.
_HIP_X_FRACTION: Final = 0.4
_HIP_Y_FRACTION: Final = 0.62
_FRAME_HEIGHTS_PER_METRE: Final = 0.42


def _add(*vectors: Vec3) -> Vec3:
    return (
        sum(v[0] for v in vectors),
        sum(v[1] for v in vectors),
        sum(v[2] for v in vectors),
    )


def _scale(vector: Vec3, factor: float) -> Vec3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


@dataclass(frozen=True, slots=True)
class _Frame:
    """The subject's own axes, from which every segment direction is built."""

    up: Vec3
    forward: Vec3
    left: Vec3

    def from_vertical_down(self, degrees: float) -> Vec3:
        """Unit direction ``degrees`` from straight down, rotating toward :attr:`forward`.

        The natural way to describe a limb: 0° is a leg hanging straight down, 90° is a thigh
        held horizontally forward, as in sitting.
        """
        radians = math.radians(degrees)
        return _add(
            _scale(self.up, -math.cos(radians)),
            _scale(self.forward, math.sin(radians)),
        )

    def from_vertical_up(self, degrees: float) -> Vec3:
        """Unit direction ``degrees`` from straight up, rotating toward :attr:`forward`.

        The natural way to describe the trunk: 0° is upright, positive is leaning forward — which
        is also the sign convention ``trunk_inclination_deg`` (OP-26) reports.
        """
        radians = math.radians(degrees)
        return _add(
            _scale(self.up, math.cos(radians)),
            _scale(self.forward, math.sin(radians)),
        )


def _axes(facing: Facing, view: View) -> _Frame:
    """Build the subject's axes.

    ``up`` is ``(0, -1, 0)`` because the coordinate system is y-**down**, matching MediaPipe.
    ``left = cross(up, forward)`` keeps the basis right-handed, which is what makes a single set of
    segment formulas work for both camera positions: in a lateral view ``left`` comes out along
    the depth axis, in a frontal view it comes out along the image's horizontal axis. No branching
    inside the construction itself.
    """
    up: Vec3 = (0.0, -1.0, 0.0)
    if view is View.FRONTAL:
        # Facing the camera. "Forward" is out of the screen, so a forward lean is a change in
        # depth and is nearly invisible in the projected image — the whole point of the preset.
        forward: Vec3 = (0.0, 0.0, -1.0)
    else:
        forward = (1.0, 0.0, 0.0) if facing is Facing.RIGHT else (-1.0, 0.0, 0.0)
    return _Frame(up=up, forward=forward, left=_cross(up, forward))


# Keyword-only throughout, and there are a lot of them: a stick figure genuinely has this many
# joints, and `make_pose(trunk_deg=35)` at a call site is worth more than a config object that
# would just move the same parameter list somewhere less discoverable.
def make_pose(
    *,
    trunk_deg: float = 0.0,
    neck_deg: float = 0.0,
    thigh_deg: float = 0.0,
    shank_deg: float = 0.0,
    upper_arm_deg: float = 0.0,
    forearm_deg: float = 0.0,
    facing: Facing = Facing.RIGHT,
    view: View = View.LATERAL,
    image_width: int = 640,
    image_height: int = 480,
    visibility: float = 0.95,
    presence: float = 0.98,
    confidence: Mapping[KeypointName, tuple[float, float]] | None = None,
    omit: Iterable[KeypointName] = (),
    body: Anthropometry | None = None,
) -> dict[KeypointName, Landmark]:
    """Construct a complete canonical skeleton from joint angles.

    All angles are degrees in the sagittal plane. Limb angles (``thigh_deg``, ``shank_deg``,
    ``upper_arm_deg``, ``forearm_deg``) are measured from **straight down**; ``trunk_deg`` and
    ``neck_deg`` from **straight up**, positive forward.

    Joint *flexion* is therefore emergent rather than an input — knee flexion is
    ``180 - |thigh_deg - shank_deg|``. That is deliberate: it means the figure is always
    physically consistent, whereas taking flexion as an input would let a caller specify a knee
    angle and a shin direction that contradict each other.

    :param confidence: per-keypoint ``(visibility, presence)`` overrides. The two are independent
        signals — low visibility with high presence is *occluded*, low presence is *out of frame*
        — so a preset can express "the far arm is behind the torso" distinctly from "the legs are
        outside the shot".
    :param omit: keypoints to leave out entirely, for degradation tests. Absence is not the same
        as zero confidence and the two must stay distinguishable.
    """
    body = body if body is not None else Anthropometry()
    axes = _axes(facing, view)
    dropped = frozenset(omit)

    half_shoulder = _scale(axes.left, body.shoulder_width / 2)
    half_hip = _scale(axes.left, body.hip_width / 2)
    half_head = _scale(axes.left, body.head_width / 2)

    # The hip midpoint is the origin — the same convention MediaPipe's world landmarks use.
    hip_mid: Vec3 = (0.0, 0.0, 0.0)
    shoulder_mid = _scale(axes.from_vertical_up(trunk_deg), body.torso)
    head_mid = _add(shoulder_mid, _scale(axes.from_vertical_up(trunk_deg + neck_deg), body.neck))

    points: dict[KeypointName, Vec3] = {}

    def place(name: KeypointName, position: Vec3) -> None:
        if name not in dropped:
            points[name] = position

    # --- torso ------------------------------------------------------------------------------
    place(KeypointName.LEFT_HIP, _add(hip_mid, half_hip))
    place(KeypointName.RIGHT_HIP, _add(hip_mid, _scale(half_hip, -1)))
    place(KeypointName.LEFT_SHOULDER, _add(shoulder_mid, half_shoulder))
    place(KeypointName.RIGHT_SHOULDER, _add(shoulder_mid, _scale(half_shoulder, -1)))
    # NECK is the shoulder midpoint by definition — the same derivation MediaPipeBackend applies,
    # so a fake frame and a real one describe the neck identically (ADR-0002).
    place(KeypointName.NECK, shoulder_mid)

    # --- head -------------------------------------------------------------------------------
    face = axes.forward
    place(KeypointName.LEFT_EAR, _add(head_mid, half_head))
    place(KeypointName.RIGHT_EAR, _add(head_mid, _scale(half_head, -1)))
    nose = _add(head_mid, _scale(face, body.head_width * 0.9))
    place(KeypointName.NOSE, nose)
    for name, lateral, forward_frac, rise in (
        (KeypointName.LEFT_EYE_INNER, 0.15, 0.55, 0.25),
        (KeypointName.LEFT_EYE, 0.30, 0.55, 0.25),
        (KeypointName.LEFT_EYE_OUTER, 0.45, 0.50, 0.25),
        (KeypointName.RIGHT_EYE_INNER, -0.15, 0.55, 0.25),
        (KeypointName.RIGHT_EYE, -0.30, 0.55, 0.25),
        (KeypointName.RIGHT_EYE_OUTER, -0.45, 0.50, 0.25),
        (KeypointName.MOUTH_LEFT, 0.20, 0.80, -0.25),
        (KeypointName.MOUTH_RIGHT, -0.20, 0.80, -0.25),
    ):
        place(
            name,
            _add(
                head_mid,
                _scale(axes.left, body.head_width * lateral),
                _scale(face, body.head_width * forward_frac),
                _scale(axes.up, body.head_width * rise),
            ),
        )

    # --- arms -------------------------------------------------------------------------------
    upper_arm = _scale(axes.from_vertical_down(upper_arm_deg), body.upper_arm)
    forearm = _scale(axes.from_vertical_down(forearm_deg), body.forearm)
    for side, elbow_name, wrist_name, hand_names in (
        (
            1,
            KeypointName.LEFT_ELBOW,
            KeypointName.LEFT_WRIST,
            (KeypointName.LEFT_PINKY, KeypointName.LEFT_INDEX, KeypointName.LEFT_THUMB),
        ),
        (
            -1,
            KeypointName.RIGHT_ELBOW,
            KeypointName.RIGHT_WRIST,
            (KeypointName.RIGHT_PINKY, KeypointName.RIGHT_INDEX, KeypointName.RIGHT_THUMB),
        ),
    ):
        shoulder = _add(shoulder_mid, _scale(half_shoulder, side))
        elbow = _add(shoulder, upper_arm)
        wrist = _add(elbow, forearm)
        place(elbow_name, elbow)
        place(wrist_name, wrist)
        pinky, index, thumb = hand_names
        hand_direction = _scale(axes.from_vertical_down(forearm_deg), body.hand)
        place(pinky, _add(wrist, hand_direction, _scale(axes.left, side * 0.02)))
        place(index, _add(wrist, hand_direction, _scale(axes.left, -side * 0.02)))
        place(thumb, _add(wrist, _scale(hand_direction, 0.6), _scale(face, 0.03)))

    # --- legs -------------------------------------------------------------------------------
    thigh = _scale(axes.from_vertical_down(thigh_deg), body.thigh)
    shank = _scale(axes.from_vertical_down(shank_deg), body.shank)
    for side, knee_name, ankle_name, heel_name, toe_name in (
        (
            1,
            KeypointName.LEFT_KNEE,
            KeypointName.LEFT_ANKLE,
            KeypointName.LEFT_HEEL,
            KeypointName.LEFT_FOOT_INDEX,
        ),
        (
            -1,
            KeypointName.RIGHT_KNEE,
            KeypointName.RIGHT_ANKLE,
            KeypointName.RIGHT_HEEL,
            KeypointName.RIGHT_FOOT_INDEX,
        ),
    ):
        hip = _add(hip_mid, _scale(half_hip, side))
        knee = _add(hip, thigh)
        ankle = _add(knee, shank)
        place(knee_name, knee)
        place(ankle_name, ankle)
        # The foot is modelled flat: heel behind and below the ankle, toe ahead and below. That is
        # enough for `heel_contact` (OP-30) to be a real measurement — the capability the original
        # project listed as a goal and never delivered, because COCO-18 has no foot landmarks.
        place(
            heel_name,
            _add(ankle, _scale(face, -body.foot * 0.25), _scale(axes.up, -body.heel_drop)),
        )
        place(
            toe_name,
            _add(ankle, _scale(face, body.foot), _scale(axes.up, -body.heel_drop)),
        )

    return _project(
        points,
        image_width=image_width,
        image_height=image_height,
        visibility=visibility,
        presence=presence,
        confidence=confidence or {},
    )


def _project(
    points: Mapping[KeypointName, Vec3],
    *,
    image_width: int,
    image_height: int,
    visibility: float,
    presence: float,
    confidence: Mapping[KeypointName, tuple[float, float]],
) -> dict[KeypointName, Landmark]:
    """World metres -> canonical landmarks carrying both coordinate systems.

    Orthographic: the depth axis is dropped rather than divided through. A real camera's
    perspective divide matters when the subject's depth is comparable to their distance from the
    lens, which is not the case for someone photographed at desk range — and modelling it would
    make the figure's *known* angles no longer recoverable from the image, defeating the purpose.
    """
    scale = image_height * _FRAME_HEIGHTS_PER_METRE
    origin_x = image_width * _HIP_X_FRACTION
    origin_y = image_height * _HIP_Y_FRACTION

    landmarks: dict[KeypointName, Landmark] = {}
    for name, (world_x, world_y, world_z) in points.items():
        point_visibility, point_presence = confidence.get(name, (visibility, presence))
        landmarks[name] = Landmark(
            x=(origin_x + world_x * scale) / image_width,
            y=(origin_y + world_y * scale) / image_height,
            visibility=point_visibility,
            presence=point_presence,
            x_world=world_x,
            y_world=world_y,
            z_world=world_z,
        )
    return landmarks


def make_pose_frame(
    *,
    backend: str = "synthetic",
    inference_ms: float = 0.0,
    image_width: int = 640,
    image_height: int = 480,
    **pose_kwargs: object,
) -> PoseFrame:
    """:func:`make_pose`, wrapped in a :class:`~posture_core.PoseFrame`.

    ``inference_ms`` defaults to exactly ``0.0`` rather than being measured. A synthetic frame has
    no inference to time, and a real measurement would make otherwise-identical frames differ
    between runs — which would break the byte-identical guarantee that end-to-end snapshot
    assertions depend on.
    """
    return PoseFrame(
        landmarks=make_pose(image_width=image_width, image_height=image_height, **pose_kwargs),  # type: ignore[arg-type]
        image_width=image_width,
        image_height=image_height,
        backend=backend,
        inference_ms=inference_ms,
    )
