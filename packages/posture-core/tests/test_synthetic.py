"""Tests for the analytic stick-figure builder.

The builder is the thing the *rest* of the test suite will measure against, so it has to be
correct on its own terms first. A metric asserted to read 35° against a figure that was not
actually built at 35° proves nothing about the metric.

The recovery helpers below deliberately reimplement the trigonometry from scratch rather than
importing anything from Epic C. Checking a builder with the code that will be tested using the
builder is circular; independent arithmetic is the point.
"""

from __future__ import annotations

import math

import pytest

from posture_core import KeypointName, Landmark
from posture_core.synthetic import Anthropometry, Facing, View, make_pose, make_pose_frame

Pose = dict[KeypointName, Landmark]


def world(landmark: Landmark) -> tuple[float, float, float]:
    assert landmark.x_world is not None
    assert landmark.y_world is not None
    assert landmark.z_world is not None
    return landmark.x_world, landmark.y_world, landmark.z_world


def midpoint_world(pose: Pose, a: KeypointName, b: KeypointName) -> tuple[float, float, float]:
    left, right = world(pose[a]), world(pose[b])
    return tuple((left[i] + right[i]) / 2 for i in range(3))  # type: ignore[return-value]


def trunk_angle(pose: Pose) -> float:
    """Signed angle of hip-mid -> shoulder-mid away from straight up, positive forward."""
    hip = midpoint_world(pose, KeypointName.LEFT_HIP, KeypointName.RIGHT_HIP)
    shoulder = midpoint_world(pose, KeypointName.LEFT_SHOULDER, KeypointName.RIGHT_SHOULDER)
    return math.degrees(math.atan2(shoulder[0] - hip[0], -(shoulder[1] - hip[1])))


def joint_angle(pose: Pose, a: KeypointName, vertex: KeypointName, c: KeypointName) -> float:
    origin = world(pose[vertex])
    first = [world(pose[a])[i] - origin[i] for i in range(3)]
    second = [world(pose[c])[i] - origin[i] for i in range(3)]
    dot = sum(p * q for p, q in zip(first, second, strict=True))
    norms = math.dist(first, [0, 0, 0]) * math.dist(second, [0, 0, 0])
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / norms))))


def image_distance(a: Landmark, b: Landmark, width: int = 640, height: int = 480) -> float:
    return math.hypot((a.x - b.x) * width, (a.y - b.y) * height)


# ---------------------------------------------------------------------------------------------
# The figure is what it says it is
# ---------------------------------------------------------------------------------------------


def test_produces_the_complete_canonical_skeleton() -> None:
    assert set(make_pose()) == set(KeypointName)


@pytest.mark.parametrize("requested", [-40.0, -25.0, 0.0, 3.0, 32.0, 60.0])
def test_trunk_angle_is_recoverable_from_the_figure(requested: float) -> None:
    """The property every Epic C metric test will lean on.

    A figure built at 32° must measure 32°, including when negative — reclining is a different
    posture from slumping, and a builder that folded the sign would make it impossible to write a
    test distinguishing them.
    """
    assert trunk_angle(make_pose(trunk_deg=requested)) == pytest.approx(requested, abs=1e-9)


@pytest.mark.parametrize(
    ("thigh", "shank"),
    [(85.0, 5.0), (15.0, 165.0), (0.0, 0.0), (90.0, 90.0)],
)
def test_knee_flexion_emerges_from_the_two_segment_angles(thigh: float, shank: float) -> None:
    """Flexion is derived, not specified — which is what keeps the figure physically consistent.

    Taking flexion as an input would let a caller ask for a 90° knee and a shin pointing in a
    direction that contradicts it, and the builder would have to silently pick a winner.
    """
    pose = make_pose(thigh_deg=thigh, shank_deg=shank)
    expected = 180.0 - abs(thigh - shank)
    measured = joint_angle(
        pose, KeypointName.LEFT_HIP, KeypointName.LEFT_KNEE, KeypointName.LEFT_ANKLE
    )
    assert measured == pytest.approx(expected, abs=1e-6)


def test_hip_midpoint_is_the_world_origin() -> None:
    """MediaPipe's world-landmark convention, so fake and real frames are interchangeable."""
    hip = midpoint_world(make_pose(trunk_deg=20.0), KeypointName.LEFT_HIP, KeypointName.RIGHT_HIP)
    assert hip == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)


def test_neck_is_exactly_the_shoulder_midpoint() -> None:
    """The same derivation `MediaPipeBackend` performs, so the two backends agree on the neck.

    If they disagreed, a rule tuned against fake frames would be tuned against a skeleton the real
    backend never produces.
    """
    pose = make_pose(trunk_deg=18.0)
    shoulders = midpoint_world(pose, KeypointName.LEFT_SHOULDER, KeypointName.RIGHT_SHOULDER)
    assert world(pose[KeypointName.NECK]) == pytest.approx(shoulders, abs=1e-12)


def test_neck_angle_is_additional_to_the_trunk() -> None:
    """Forward head posture stacks on top of whatever the trunk is doing.

    Ear ahead of the shoulders is what a craniovertebral angle measures (OP-27), so the builder
    has to be able to produce it independently of trunk lean.
    """
    upright = make_pose(trunk_deg=0.0, neck_deg=0.0)
    forward_head = make_pose(trunk_deg=0.0, neck_deg=35.0)
    ear = KeypointName.LEFT_EAR
    assert world(forward_head[ear])[0] > world(upright[ear])[0]


def test_facing_left_mirrors_the_figure() -> None:
    right = make_pose(trunk_deg=25.0, facing=Facing.RIGHT)
    left = make_pose(trunk_deg=25.0, facing=Facing.LEFT)
    assert world(right[KeypointName.NOSE])[0] == pytest.approx(-world(left[KeypointName.NOSE])[0])
    # The trunk lean itself is unchanged: leaning forward is leaning forward whichever way you
    # face. An implementation keying lean off raw x would report these as opposite postures —
    # which is exactly the legacy laterality bug (FINDINGS §2.1), reproduced here as a guard.
    assert trunk_angle(left) == pytest.approx(-25.0, abs=1e-9)


# ---------------------------------------------------------------------------------------------
# Camera position
# ---------------------------------------------------------------------------------------------


def test_lateral_view_hides_shoulder_width_and_shows_the_lean() -> None:
    pose = make_pose(trunk_deg=30.0, view=View.LATERAL)
    shoulders = image_distance(pose[KeypointName.LEFT_SHOULDER], pose[KeypointName.RIGHT_SHOULDER])
    assert shoulders == pytest.approx(0.0, abs=1e-9)
    assert trunk_angle(pose) == pytest.approx(30.0)


def test_frontal_view_shows_shoulder_width_and_hides_the_lean() -> None:
    """The failure the original app invited and never guarded against.

    It told users "this image must be taken from a side angle" and then assessed whatever arrived.
    A 30° slump photographed head-on projects to almost no apparent lean, so the app would report
    good posture with complete confidence. `view_confidence` (OP-31) exists to refuse this, and
    this preset is what it gets tested against.
    """
    pose = make_pose(trunk_deg=30.0, view=View.FRONTAL)
    shoulders = image_distance(pose[KeypointName.LEFT_SHOULDER], pose[KeypointName.RIGHT_SHOULDER])
    torso = image_distance(pose[KeypointName.NECK], pose[KeypointName.LEFT_HIP])
    assert shoulders / torso > 0.5

    hip = midpoint_world(pose, KeypointName.LEFT_HIP, KeypointName.RIGHT_HIP)
    neck = world(pose[KeypointName.NECK])
    apparent_lean = math.degrees(math.atan2(neck[0] - hip[0], -(neck[1] - hip[1])))
    assert apparent_lean == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------------------------
# Scale, determinism, degradation
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("factor", [0.5, 1.0, 2.5])
def test_angles_are_invariant_to_body_size(factor: float) -> None:
    """The proof that the redesign fixed the original defect, in miniature.

    The legacy engine compared raw pixel distances against literal thresholds, so the same posture
    at a different distance or on a different-sized person got a different verdict. Angles in world
    space cannot do that. The full Hypothesis version of this property is OP-34; this is the
    builder's own guarantee that it is a fair test bed for it.
    """
    default = Anthropometry()
    scaled = Anthropometry(
        torso=default.torso * factor,
        neck=default.neck * factor,
        shoulder_width=default.shoulder_width * factor,
        hip_width=default.hip_width * factor,
        thigh=default.thigh * factor,
        shank=default.shank * factor,
    )
    pose = make_pose(trunk_deg=27.0, thigh_deg=85.0, shank_deg=5.0, body=scaled)
    assert trunk_angle(pose) == pytest.approx(27.0, abs=1e-9)
    assert joint_angle(
        pose, KeypointName.LEFT_HIP, KeypointName.LEFT_KNEE, KeypointName.LEFT_ANKLE
    ) == pytest.approx(100.0, abs=1e-6)


def test_output_is_byte_identical_across_calls() -> None:
    """No clock, no randomness. Snapshot assertions downstream depend on this absolutely."""
    assert make_pose(trunk_deg=12.0) == make_pose(trunk_deg=12.0)


def test_omitted_keypoints_are_absent_rather_than_zeroed() -> None:
    """Degradation tests need "not reported", which is a different fact from "low confidence"."""
    pose = make_pose(omit=(KeypointName.LEFT_KNEE, KeypointName.RIGHT_KNEE))
    assert KeypointName.LEFT_KNEE not in pose
    assert KeypointName.RIGHT_KNEE not in pose
    assert len(pose) == len(KeypointName) - 2


def test_per_keypoint_confidence_can_express_occlusion_separately_from_absence() -> None:
    """Low visibility with high presence means occluded; low presence means out of frame.

    Two independent signals, and the reason MediaPipe was chosen over MoveNet (ADR-0002). The
    builder has to be able to produce both or the status model (OP-25) has nothing to test on.
    """
    pose = make_pose(confidence={KeypointName.LEFT_WRIST: (0.1, 0.95)})
    wrist = pose[KeypointName.LEFT_WRIST]
    assert wrist.visibility == pytest.approx(0.1)
    assert wrist.presence == pytest.approx(0.95)
    assert pose[KeypointName.RIGHT_WRIST].visibility == pytest.approx(0.95)


def test_image_coordinates_track_the_frame_size() -> None:
    """Normalised coordinates are resolution-independent: the same figure at 4K reads the same."""
    small = make_pose(trunk_deg=15.0, image_width=640, image_height=480)
    large = make_pose(trunk_deg=15.0, image_width=3840, image_height=2880)
    assert small[KeypointName.NOSE].x == pytest.approx(large[KeypointName.NOSE].x)
    assert small[KeypointName.NOSE].y == pytest.approx(large[KeypointName.NOSE].y)


def test_the_figure_fits_inside_the_frame() -> None:
    """Not a correctness requirement, but a landmark outside [0, 1] would read as OUT_OF_FRAME to
    the status model (OP-25) and make every preset look partially clipped."""
    for landmark in make_pose(trunk_deg=32.0, thigh_deg=85.0, shank_deg=5.0).values():
        assert 0.0 <= landmark.x <= 1.0
        assert 0.0 <= landmark.y <= 1.0


# ---------------------------------------------------------------------------------------------
# make_pose_frame
# ---------------------------------------------------------------------------------------------


def test_frame_wraps_the_pose_with_a_constant_zero_latency() -> None:
    """A synthetic frame has no inference to time, and a measured value would differ between runs
    — which is precisely what would break the byte-identical guarantee."""
    frame = make_pose_frame(trunk_deg=10.0)
    assert frame.inference_ms == 0.0
    assert frame.has_world_landmarks is True
    assert make_pose_frame(trunk_deg=10.0) == frame
