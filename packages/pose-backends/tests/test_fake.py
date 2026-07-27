"""Tests for `FakePoseBackend` and its presets.

Treated as production code, because it is: with `POSE_BACKEND=fake` the whole application runs
backend-free, so a wrong preset does not fail a test — it silently changes what the container
smoke test and the Playwright suite are asserting about.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from numpy.typing import NDArray

from pose_backends import FakePoseBackend, PoseBackend, PosePreset
from posture_core import KeypointName, PoseFrame

DETECTABLE = [preset for preset in PosePreset if preset is not PosePreset.NO_PERSON]


def image(width: int = 640, height: int = 480) -> NDArray[np.uint8]:
    return np.zeros((height, width, 3), dtype=np.uint8)


def trunk_lean(frame: PoseFrame) -> float:
    """Signed lean of hip-mid -> shoulder-mid from vertical, positive forward, in world space."""
    hips = [frame.landmarks[k] for k in (KeypointName.LEFT_HIP, KeypointName.RIGHT_HIP)]
    neck = frame.landmarks[KeypointName.NECK]
    hip_x = sum(h.x_world or 0.0 for h in hips) / 2
    hip_y = sum(h.y_world or 0.0 for h in hips) / 2
    return math.degrees(math.atan2((neck.x_world or 0.0) - hip_x, -((neck.y_world or 0.0) - hip_y)))


def knee_flexion(frame: PoseFrame) -> float:
    hip = frame.landmarks[KeypointName.LEFT_HIP]
    knee = frame.landmarks[KeypointName.LEFT_KNEE]
    ankle = frame.landmarks[KeypointName.LEFT_ANKLE]
    first = [(hip.x_world or 0) - (knee.x_world or 0), (hip.y_world or 0) - (knee.y_world or 0)]
    second = [
        (ankle.x_world or 0) - (knee.x_world or 0),
        (ankle.y_world or 0) - (knee.y_world or 0),
    ]
    dot = first[0] * second[0] + first[1] * second[1]
    norms = math.dist(first, [0, 0]) * math.dist(second, [0, 0])
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / norms))))


# ---------------------------------------------------------------------------------------------
# Every preset is a usable frame
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("preset", DETECTABLE)
def test_every_preset_returns_a_complete_frame(preset: PosePreset) -> None:
    frame = FakePoseBackend(preset).detect(image())
    assert frame is not None
    assert frame.backend == "fake"
    assert KeypointName.NECK in frame
    assert frame.has_world_landmarks is True


def test_no_person_preset_returns_none() -> None:
    """The empty-desk path, exercisable without an empty desk.

    Every caller downstream has to handle `None`, and this is what makes that branch reachable in
    a test that runs in milliseconds with no model.
    """
    assert FakePoseBackend(PosePreset.NO_PERSON).detect(image()) is None


@pytest.mark.parametrize("preset", DETECTABLE)
def test_preset_output_is_byte_identical_across_runs_and_instances(preset: PosePreset) -> None:
    """End-to-end snapshot assertions depend on this absolutely.

    Two separately-constructed backends must agree, not just two calls on one — otherwise a
    process restart between a golden-file capture and its comparison would produce a diff.
    """
    first = FakePoseBackend(preset).detect(image())
    second = FakePoseBackend(preset).detect(image())
    assert first == second
    assert first is not None and first.inference_ms == 0.0


def test_preset_may_be_named_by_string() -> None:
    """`POSE_BACKEND_PRESET=hunchback` arrives as a string, not as an enum member."""
    assert FakePoseBackend("hunchback").preset is PosePreset.HUNCHBACK


def test_unknown_preset_name_fails_loudly() -> None:
    with pytest.raises(ValueError, match="slouchy"):
        FakePoseBackend("slouchy")


# ---------------------------------------------------------------------------------------------
# The presets describe the postures they are named after
# ---------------------------------------------------------------------------------------------


def test_hunchback_leans_further_forward_than_straight() -> None:
    """If this ordering were wrong, every rule tuned against these fixtures would be tuned
    backwards, and nothing else in the suite would notice."""
    straight = FakePoseBackend(PosePreset.STRAIGHT).detect(image())
    hunched = FakePoseBackend(PosePreset.HUNCHBACK).detect(image())
    assert straight is not None and hunched is not None
    assert trunk_lean(hunched) > trunk_lean(straight) + 20


def test_reclined_leans_backwards_giving_a_negative_trunk_angle() -> None:
    """The preset that catches an unsigned implementation.

    A metric taking `abs()` of the lean would score reclining identically to slumping. Only a
    fixture with a genuinely negative angle can fail such an implementation.
    """
    reclined = FakePoseBackend(PosePreset.RECLINED).detect(image())
    assert reclined is not None
    assert trunk_lean(reclined) < 0


def test_kneeling_has_a_sharply_flexed_knee_and_seated_presets_do_not() -> None:
    kneeling = FakePoseBackend(PosePreset.KNEELING).detect(image())
    seated = FakePoseBackend(PosePreset.STRAIGHT).detect(image())
    assert kneeling is not None and seated is not None
    assert knee_flexion(kneeling) < 45
    assert 80 < knee_flexion(seated) < 120


def test_frontal_view_shows_broad_shoulders_where_lateral_presets_show_none() -> None:
    """What `view_confidence` (OP-31) keys off, in image space.

    The original app asked for a side-on photo and assessed whatever it got. This preset is the
    photo it should refuse.
    """

    def shoulder_span(frame: PoseFrame) -> float:
        left = frame.landmarks[KeypointName.LEFT_SHOULDER]
        right = frame.landmarks[KeypointName.RIGHT_SHOULDER]
        return abs(left.x - right.x)

    frontal = FakePoseBackend(PosePreset.FRONTAL_VIEW).detect(image())
    lateral = FakePoseBackend(PosePreset.STRAIGHT).detect(image())
    assert frontal is not None and lateral is not None
    assert shoulder_span(frontal) > 0.1
    assert shoulder_span(lateral) == pytest.approx(0.0, abs=1e-9)


def test_partial_occlusion_distinguishes_missing_from_merely_unseen() -> None:
    """Two different kinds of "we cannot assess this", and they must not collapse into one.

    The legs are *omitted* — never reported, so the frame does not contain them at all. The far
    arm is *present* with low visibility and high presence, which is what occluded looks like.
    Being able to express both is why MediaPipe was chosen over MoveNet (ADR-0002), and this
    preset is how the "couldn't assess your knees, try a wider shot" path gets tested without
    finding a photograph of someone whose knees are out of shot.
    """
    frame = FakePoseBackend(PosePreset.PARTIAL_OCCLUSION).detect(image())
    assert frame is not None

    assert KeypointName.LEFT_KNEE not in frame
    assert KeypointName.RIGHT_ANKLE not in frame

    elbow = frame.landmarks[KeypointName.LEFT_ELBOW]
    assert elbow.visibility < 0.3
    assert elbow.presence > 0.8

    # The upper body is still fully assessable, which is the point: partial occlusion must
    # degrade the report, not void it.
    assert KeypointName.NECK in frame
    assert frame.landmarks[KeypointName.RIGHT_SHOULDER].visibility > 0.8


# ---------------------------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------------------------


def test_the_image_is_ignored_entirely() -> None:
    """The contract, not a shortcut.

    Callers select the scenario by constructing the backend. A fake that inspected pixels would
    make an end-to-end test's outcome depend on whichever placeholder JPEG someone uploaded.
    """
    backend = FakePoseBackend(PosePreset.STRAIGHT)
    noise = np.full((480, 640, 3), 255, dtype=np.uint8)
    assert backend.detect(noise) == backend.detect(image())


def test_frame_size_is_configurable() -> None:
    frame = FakePoseBackend(PosePreset.STRAIGHT, image_width=1920, image_height=1080).detect(
        image(1920, 1080)
    )
    assert frame is not None
    assert (frame.image_width, frame.image_height) == (1920, 1080)


def test_warmup_and_close_are_no_ops_that_do_not_raise() -> None:
    """OP-40's lifespan hook calls both blind, so neither may need a special case for the fake."""
    backend = FakePoseBackend()
    backend.warmup()
    backend.warmup()
    backend.close()


def test_satisfies_the_protocol() -> None:
    assert isinstance(FakePoseBackend(), PoseBackend)
