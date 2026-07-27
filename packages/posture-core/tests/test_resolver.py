"""Tests for the keypoint resolver and the status model.

This is the layer that decides whether a metric is allowed to produce a number at all, so its
tests are the ones that pin down the project's central correctness claim: the engine says
"I could not assess this" instead of quietly saying "you look fine".
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from posture_core import KeypointName, Landmark, PoseFrame
from posture_core.resolver import KeypointResolver, Resolved, Unresolved
from posture_core.status import Gap, KeypointStatus, Metric, MetricStatus
from posture_core.synthetic import Facing, View, make_pose
from posture_core.thresholds import DEFAULT_THRESHOLDS

K = KeypointName


def frame_from(landmarks: dict[KeypointName, Landmark]) -> PoseFrame:
    return PoseFrame(
        landmarks=landmarks,
        image_width=640,
        image_height=480,
        backend="test",
        inference_ms=0.0,
    )


def resolver_for(**pose_kwargs: object) -> KeypointResolver:
    return KeypointResolver(frame_from(make_pose(**pose_kwargs)), DEFAULT_THRESHOLDS)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# Keypoint classification
# ---------------------------------------------------------------------------------------------


def test_a_confident_landmark_is_ok() -> None:
    assert resolver_for().status(K.LEFT_HIP) is KeypointStatus.OK


def test_an_unreported_landmark_is_not_detected_rather_than_low_confidence() -> None:
    """Two different facts with two different remedies.

    Collapsing them makes it impossible to distinguish "this backend has no heel landmark" — a
    schema limitation, as with the 17-point escape hatch in ADR-0002 — from "this photo does not
    show a heel", which the person holding the camera can fix.
    """
    resolver = resolver_for(omit=(K.LEFT_HEEL,))
    assert resolver.status(K.LEFT_HEEL) is KeypointStatus.NOT_DETECTED


def test_low_visibility_with_high_presence_reads_as_low_confidence() -> None:
    """Occluded: the model believes the point is in the picture but cannot see it clearly."""
    resolver = resolver_for(confidence={K.LEFT_WRIST: (0.2, 0.95)})
    assert resolver.status(K.LEFT_WRIST) is KeypointStatus.LOW_CONFIDENCE


def test_low_presence_reads_as_out_of_frame_even_when_visibility_is_high() -> None:
    """Presence is checked first, and the order matters.

    A point outside the picture is necessarily also hard to see, so checking visibility first
    would mask the stronger and more actionable statement. "Your knees are out of shot" tells the
    user to step back; "your knees are unclear" tells them to turn on a light.
    """
    resolver = resolver_for(confidence={K.LEFT_ANKLE: (0.99, 0.1)})
    assert resolver.status(K.LEFT_ANKLE) is KeypointStatus.OUT_OF_FRAME


def test_thresholds_are_injected_not_hardcoded() -> None:
    landmarks = make_pose(confidence={K.LEFT_WRIST: (0.6, 0.9)})
    lenient = KeypointResolver(frame_from(landmarks), DEFAULT_THRESHOLDS)
    strict = KeypointResolver(
        frame_from(landmarks), dataclasses.replace(DEFAULT_THRESHOLDS, min_visibility=0.8)
    )
    assert lenient.status(K.LEFT_WRIST) is KeypointStatus.OK
    assert strict.status(K.LEFT_WRIST) is KeypointStatus.LOW_CONFIDENCE


def test_every_canonical_keypoint_has_a_status() -> None:
    """Including the ones the frame never contained, which is what makes the report's quality
    section able to enumerate what was missing rather than only what was present."""
    assert set(resolver_for(omit=(K.LEFT_HEEL, K.RIGHT_HEEL)).statuses) == set(KeypointName)


# ---------------------------------------------------------------------------------------------
# require()
# ---------------------------------------------------------------------------------------------


def test_require_returns_the_landmarks_when_all_are_usable() -> None:
    resolution = resolver_for().require(K.LEFT_HIP, K.RIGHT_HIP, K.NECK)
    assert isinstance(resolution, Resolved)
    assert set(resolution.landmarks) == {K.LEFT_HIP, K.RIGHT_HIP, K.NECK}
    assert resolution.world(K.NECK) is not None


def test_confidence_is_the_weakest_input_not_the_average() -> None:
    """A measurement is only as good as the least trustworthy thing it rests on.

    Averaging would let one confidently-seen landmark launder a barely-seen one into a number that
    looks well-supported — which is precisely how a metric comes to be reported with confidence it
    has not earned.
    """
    resolver = KeypointResolver(
        frame_from(make_pose(confidence={K.LEFT_HIP: (0.55, 0.99)})), DEFAULT_THRESHOLDS
    )
    resolution = resolver.require(K.LEFT_HIP, K.RIGHT_HIP)
    assert isinstance(resolution, Resolved)
    assert resolution.confidence == pytest.approx(0.55)


def test_a_missing_keypoint_makes_the_metric_insufficient() -> None:
    resolution = resolver_for(omit=(K.LEFT_KNEE,)).require(K.LEFT_HIP, K.LEFT_KNEE, K.LEFT_ANKLE)
    assert isinstance(resolution, Unresolved)
    assert resolution.status is MetricStatus.INSUFFICIENT_KEYPOINTS
    assert resolution.problems == {K.LEFT_KNEE: KeypointStatus.NOT_DETECTED}


def test_an_unclear_keypoint_makes_the_metric_low_confidence() -> None:
    """Kept separate from a missing one because it is recoverable by better light, not reframing."""
    resolution = resolver_for(confidence={K.LEFT_KNEE: (0.2, 0.95)}).require(
        K.LEFT_HIP, K.LEFT_KNEE
    )
    assert isinstance(resolution, Unresolved)
    assert resolution.status is MetricStatus.LOW_CONFIDENCE


def test_a_missing_keypoint_outranks_an_unclear_one() -> None:
    """Reframing the shot subsumes improving the view of what is already in it, so the more
    fundamental obstacle is the one reported."""
    resolution = resolver_for(omit=(K.LEFT_ANKLE,), confidence={K.LEFT_KNEE: (0.2, 0.95)}).require(
        K.LEFT_HIP, K.LEFT_KNEE, K.LEFT_ANKLE
    )
    assert isinstance(resolution, Unresolved)
    assert resolution.status is MetricStatus.INSUFFICIENT_KEYPOINTS


def test_only_the_problem_keypoints_are_reported() -> None:
    """A gap that listed every input would bury the one thing the user can act on."""
    resolution = resolver_for(omit=(K.LEFT_KNEE,)).require(K.LEFT_HIP, K.LEFT_KNEE, K.LEFT_ANKLE)
    assert isinstance(resolution, Unresolved)
    assert set(resolution.problems) == {K.LEFT_KNEE}


def test_the_explanation_names_the_landmarks_in_plain_language() -> None:
    """Written for the person in the photograph, not for a log line.

    The entire value of the status model is that the user hears something actionable. A gap saying
    `INSUFFICIENT_KEYPOINTS: KeypointName.LEFT_KNEE` would be technically honest and useless.
    """
    resolution = resolver_for(omit=(K.LEFT_KNEE, K.RIGHT_KNEE)).require(K.LEFT_KNEE, K.RIGHT_KNEE)
    assert isinstance(resolution, Unresolved)
    gap = resolution.as_gap("knee_flexion_deg")
    assert "could not see left knee and right knee" in gap.detail


def test_unresolved_converts_to_an_empty_metric_carrying_its_reason() -> None:
    resolution = resolver_for(omit=(K.LEFT_KNEE,)).require(K.LEFT_KNEE)
    assert isinstance(resolution, Unresolved)
    metric = resolution.as_metric("knee_flexion_deg", "deg")
    assert metric.value is None
    assert metric.status is MetricStatus.INSUFFICIENT_KEYPOINTS
    assert metric.is_ok is False


# ---------------------------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------------------------


def test_forward_axis_follows_the_subject_not_the_frame() -> None:
    """The replacement for the legacy laterality flag.

    That flag was read from a misdocumented config and made spine classification backwards for one
    facing direction. Deriving facing from the nose leaves nothing to get backwards.
    """
    facing_right = resolver_for(facing=Facing.RIGHT).forward_axis()
    facing_left = resolver_for(facing=Facing.LEFT).forward_axis()
    assert facing_right is not None and facing_left is not None
    assert facing_right[0] == pytest.approx(1.0, abs=1e-6)
    assert facing_left[0] == pytest.approx(-1.0, abs=1e-6)


def test_forward_axis_is_horizontal_and_unit_length() -> None:
    axis = resolver_for(trunk_deg=35.0).forward_axis()
    assert axis is not None
    assert float(np.linalg.norm(axis)) == pytest.approx(1.0)
    assert axis[1] == pytest.approx(0.0, abs=1e-9)


def test_forward_axis_survives_a_deep_slouch() -> None:
    """A slouching subject's face tilts downward, and the vertical component is removed rather
    than allowed to shrink the horizontal one to nothing."""
    axis = resolver_for(trunk_deg=45.0, neck_deg=40.0).forward_axis()
    assert axis is not None
    assert axis[0] == pytest.approx(1.0, abs=1e-6)


def test_forward_axis_points_at_the_camera_in_a_frontal_view() -> None:
    """A frontal view yields a valid axis along the view direction.

    Only that. An earlier version of this docstring claimed sagittal metrics must abstain here,
    which contradicts `test_trunk.py`: world landmarks are metric 3D, so the full lean is
    recovered from a head-on photo. Reduced reliability is `view_confidence`'s business (OP-31),
    and it downgrades confidence rather than abstaining.
    """
    axis = resolver_for(view=View.FRONTAL).forward_axis()
    assert axis is not None
    assert axis[2] == pytest.approx(-1.0, abs=1e-6)


def test_forward_axis_is_none_without_a_nose_or_a_neck() -> None:
    assert resolver_for(omit=(K.NOSE,)).forward_axis() is None
    assert resolver_for(omit=(K.NECK,)).forward_axis() is None


def test_forward_axis_is_none_when_the_face_points_straight_up() -> None:
    """No horizontal component means no usable facing, and a normalised near-zero vector would be
    numerical noise pointing in an arbitrary direction — which would then silently set the sign of
    every lean measurement."""
    landmarks = dict(make_pose())
    neck = landmarks[K.NECK]
    landmarks[K.NOSE] = Landmark(
        x=neck.x,
        y=neck.y - 0.1,
        visibility=0.9,
        presence=0.9,
        x_world=neck.x_world,
        y_world=(neck.y_world or 0.0) - 0.2,
        z_world=neck.z_world,
    )
    assert KeypointResolver(frame_from(landmarks), DEFAULT_THRESHOLDS).forward_axis() is None


def test_resolved_exposes_landmarks_by_subscript_and_the_frame_is_readable() -> None:
    """Both are conveniences the metric modules use on every call, so both are worth pinning."""
    resolver = resolver_for()
    resolution = resolver.require(K.LEFT_HIP)
    assert isinstance(resolution, Resolved)
    assert resolution[K.LEFT_HIP] is resolver.frame.landmarks[K.LEFT_HIP]


def test_a_single_unclear_landmark_is_described_in_the_singular() -> None:
    """Grammar, because the string is shown to a person. "left wrist were unclear" reads as a bug
    in the product even when the assessment behind it is correct."""
    resolution = resolver_for(confidence={K.LEFT_WRIST: (0.2, 0.95)}).require(K.LEFT_WRIST)
    assert isinstance(resolution, Unresolved)
    assert "left wrist was unclear" in resolution.as_gap("elbow_flexion_deg").detail


def test_both_kinds_of_problem_are_reported_together() -> None:
    resolution = resolver_for(omit=(K.LEFT_ANKLE,), confidence={K.LEFT_KNEE: (0.2, 0.95)}).require(
        K.LEFT_KNEE, K.LEFT_ANKLE
    )
    assert isinstance(resolution, Unresolved)
    detail = resolution.as_gap("knee_flexion_deg").detail
    assert "could not see left ankle" in detail
    assert "left knee was unclear" in detail


def test_forward_axis_is_none_without_world_landmarks() -> None:
    flat = {
        name: Landmark(x=landmark.x, y=landmark.y, visibility=0.9, presence=0.9)
        for name, landmark in make_pose().items()
    }
    assert KeypointResolver(frame_from(flat), DEFAULT_THRESHOLDS).forward_axis() is None


# ---------------------------------------------------------------------------------------------
# Metric and Gap invariants
# ---------------------------------------------------------------------------------------------


def test_a_metric_cannot_be_ok_without_a_value() -> None:
    """The invariant every downstream consumer branches on.

    Rules, report, API response and frontend all ask "is this OK?" and then read the value. A
    metric that answered yes and carried `None` would produce a different plausible wrong answer
    at each of them.
    """
    with pytest.raises(ValueError, match="must accompany each other"):
        Metric(name="x", value=None, unit="deg", status=MetricStatus.OK, detail="")


def test_a_metric_cannot_carry_a_value_it_says_it_could_not_measure() -> None:
    with pytest.raises(ValueError, match="must accompany each other"):
        Metric(
            name="x",
            value=12.0,
            unit="deg",
            status=MetricStatus.INSUFFICIENT_KEYPOINTS,
            detail="",
        )


def test_a_successful_metric_has_no_gap() -> None:
    metric = Metric(name="x", value=1.0, unit="deg", status=MetricStatus.OK, detail="")
    with pytest.raises(ValueError, match="no gap to report"):
        metric.as_gap()


def test_a_gap_cannot_claim_everything_was_fine() -> None:
    with pytest.raises(ValueError, match="cannot have an OK status"):
        Gap(metric="x", status=MetricStatus.OK, detail="")


def test_a_failed_metric_converts_itself_into_a_gap() -> None:
    """The other direction of the same conversion: a metric that abstained knows how to say so.

    Both directions exist because the report is assembled from metrics (OP-32) while the API's
    user-facing quality section is assembled from gaps, and neither should have to reconstruct the
    other's reasoning.
    """
    metric = Metric(
        name="knee_flexion_deg",
        value=None,
        unit="deg",
        status=MetricStatus.INSUFFICIENT_KEYPOINTS,
        detail="could not see left knee in the photo",
        inputs=(K.LEFT_KNEE,),
    )
    gap = metric.as_gap({K.LEFT_KNEE: KeypointStatus.NOT_DETECTED})
    assert gap.metric == "knee_flexion_deg"
    assert gap.status is MetricStatus.INSUFFICIENT_KEYPOINTS
    assert gap.keypoints == {K.LEFT_KNEE: KeypointStatus.NOT_DETECTED}


def test_a_gaps_keypoints_cannot_be_edited_after_it_is_produced() -> None:
    """The same trap `frozen=True` leaves open on `PoseFrame`.

    Freezing a dataclass freezes the attribute binding, not the mapping bound to it. A gap is a
    statement about what the engine could not assess, and it should not be revisable by whoever
    happens to be holding it.
    """
    gap = Gap(
        metric="knee_flexion_deg",
        status=MetricStatus.INSUFFICIENT_KEYPOINTS,
        detail="could not see left knee in the photo",
        keypoints={K.LEFT_KNEE: KeypointStatus.NOT_DETECTED},
    )
    with pytest.raises(TypeError):
        gap.keypoints[K.RIGHT_KNEE] = KeypointStatus.NOT_DETECTED  # type: ignore[index]


def test_a_gap_is_detached_from_the_mapping_it_was_built_from() -> None:
    source = {K.LEFT_KNEE: KeypointStatus.NOT_DETECTED}
    gap = Gap(metric="x", status=MetricStatus.INSUFFICIENT_KEYPOINTS, detail="", keypoints=source)
    source[K.RIGHT_KNEE] = KeypointStatus.NOT_DETECTED
    assert set(gap.keypoints) == {K.LEFT_KNEE}
