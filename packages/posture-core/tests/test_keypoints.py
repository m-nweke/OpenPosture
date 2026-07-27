"""Tests for the canonical skeleton types.

These are cheap tests of a cheap module, and worth writing anyway: every metric, every rule and
every API response is built on these three types, so an invariant that leaks here is one that
leaks everywhere. In particular the immutability tests below guard something a reader would
reasonably assume `frozen=True` already gave them, and which it does not.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from posture_core import KeypointName, Landmark, PoseFrame


def make_landmark(**overrides: float | None) -> Landmark:
    defaults: dict[str, float | None] = {"x": 0.5, "y": 0.5, "visibility": 0.9, "presence": 0.9}
    defaults.update(overrides)
    return Landmark(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# KeypointName
# ---------------------------------------------------------------------------------------------


def test_covers_all_33_mediapipe_landmarks_plus_derived_neck() -> None:
    """The count is the contract OP-18 and OP-21 are written against.

    MediaPipe reports 33 points; NECK is derived by the adapter and brings the canonical skeleton
    to 34. The CLI's landmark table and the adapter's "25 or more landmarks detected" acceptance
    criterion both assume this, so pinning the number here makes an accidental addition or
    deletion a test failure rather than a puzzling off-by-one in a downstream assertion.
    """
    assert len(KeypointName) == 34


def test_members_serialise_as_readable_strings() -> None:
    """StrEnum, so JSON needs no custom encoder. The CLI and golden fixtures depend on this.

    `str(member)` rather than `member == "..."`: mypy --strict rejects the direct comparison as
    non-overlapping even though StrEnum makes it true at runtime. Asserting on the rendered string
    is the property that actually matters anyway — it is what lands in JSON.
    """
    assert str(KeypointName.LEFT_SHOULDER) == "left_shoulder"
    assert f"{KeypointName.NECK}" == "neck"
    assert json.dumps([KeypointName.NECK]) == '["neck"]'


def test_values_are_unique() -> None:
    """Two names sharing a value would silently alias — StrEnum resolves the second to the first.

    That is not hypothetical here: the members are hand-written and several differ by one word.
    A duplicated value would make one keypoint permanently unreachable with no error anywhere.
    """
    assert len({member.value for member in KeypointName}) == len(KeypointName)


def test_laterality_is_symmetric() -> None:
    """Every LEFT_* has a RIGHT_* twin and vice versa.

    Worth asserting because the legacy engine's defining bug was a laterality mix-up: `API/config`
    declared 16=left ear, the code commented it the other way round, and the resulting flag made
    spine classification backwards for one facing direction (FINDINGS §2.1). Named landmarks make
    that class of bug impossible — provided the names themselves are complete.
    """
    left = {name.value.removeprefix("left_") for name in KeypointName if name.startswith("left_")}
    right = {
        name.value.removeprefix("right_") for name in KeypointName if name.startswith("right_")
    }
    assert left == right


# ---------------------------------------------------------------------------------------------
# Landmark
# ---------------------------------------------------------------------------------------------


def test_landmark_is_immutable() -> None:
    landmark = make_landmark()
    with pytest.raises(dataclasses.FrozenInstanceError):
        landmark.x = 0.1  # type: ignore[misc]


def test_landmark_uses_slots() -> None:
    """No `__dict__`: a typo'd attribute cannot be silently attached to an instance.

    There is one Landmark per keypoint per frame — 34 per detection — so the memory saving is
    real, but the typo-catching is the reason. Asserting on `__dict__`'s absence rather than
    trying an assignment because on a `frozen=True, slots=True` dataclass an unknown attribute
    raises from the frozen `__setattr__` first, which would make this test pass for the wrong
    reason on a class that had slots removed.
    """
    assert not hasattr(make_landmark(), "__dict__")
    assert Landmark.__slots__ == (
        "x",
        "y",
        "visibility",
        "presence",
        "x_world",
        "y_world",
        "z_world",
    )


def test_world_coordinates_default_to_absent() -> None:
    landmark = make_landmark()
    assert landmark.has_world is False
    assert (landmark.x_world, landmark.y_world, landmark.z_world) == (None, None, None)


def test_world_coordinates_are_recognised_when_present() -> None:
    landmark = make_landmark(x_world=0.1, y_world=-0.4, z_world=0.02)
    assert landmark.has_world is True


@pytest.mark.parametrize(
    "partial",
    [
        {"x_world": 0.1},
        {"x_world": 0.1, "y_world": 0.2},
        {"z_world": 0.3},
        {"y_world": 0.2, "z_world": 0.3},
    ],
)
def test_partial_world_coordinates_are_rejected(partial: dict[str, float]) -> None:
    """All-or-nothing: a backend either reconstructs metric 3D or it does not.

    A half-populated triple can only be an adapter bug, and if it were allowed through it would
    surface much later as a nonsensical angle rather than as an obviously malformed frame.
    """
    with pytest.raises(ValueError, match="all present or all absent"):
        make_landmark(**partial)


@pytest.mark.parametrize("coordinate", [-0.2, 1.4])
def test_out_of_frame_coordinates_are_allowed(coordinate: float) -> None:
    """Deliberately not range-validated.

    MediaPipe extrapolates joints past the frame edge and reports normalised values outside
    [0, 1]. That is real information — it is the evidence the OUT_OF_FRAME keypoint status (OP-25)
    is built on — so clamping or rejecting it here would destroy the signal.
    """
    assert make_landmark(x=coordinate).x == coordinate


# ---------------------------------------------------------------------------------------------
# PoseFrame
# ---------------------------------------------------------------------------------------------


def make_frame(
    landmarks: dict[KeypointName, Landmark] | None = None, **overrides: object
) -> PoseFrame:
    kwargs: dict[str, object] = {
        "landmarks": landmarks if landmarks is not None else {KeypointName.NOSE: make_landmark()},
        "image_width": 640,
        "image_height": 480,
        "backend": "test",
        "inference_ms": 1.5,
    }
    kwargs.update(overrides)
    return PoseFrame(**kwargs)  # type: ignore[arg-type]


def test_frame_is_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_frame().backend = "other"  # type: ignore[misc]


def test_landmark_mapping_cannot_be_mutated_through_the_frame() -> None:
    """The one that `frozen=True` does *not* give you.

    Freezing a dataclass freezes the attribute *binding*, not the object bound to it — so without
    the MappingProxyType wrapper in `__post_init__`, `frame.landmarks[NECK] = ...` would mutate a
    supposedly frozen frame without complaint.
    """
    frame = make_frame()
    with pytest.raises(TypeError):
        frame.landmarks[KeypointName.NECK] = make_landmark()  # type: ignore[index]


def test_frame_is_detached_from_the_dict_it_was_built_from() -> None:
    """An adapter reusing its scratch dict between calls must not retroactively edit past frames."""
    scratch = {KeypointName.NOSE: make_landmark()}
    frame = make_frame(scratch)
    scratch[KeypointName.LEFT_EAR] = make_landmark()
    assert KeypointName.LEFT_EAR not in frame
    assert len(frame) == 1


@pytest.mark.parametrize(("width", "height"), [(0, 480), (640, 0), (-1, 480), (640, -1)])
def test_non_positive_image_dimensions_are_rejected(width: int, height: int) -> None:
    """Guards a divide-by-zero in every image-space normalisation downstream."""
    with pytest.raises(ValueError, match="dimensions must be positive"):
        make_frame(image_width=width, image_height=height)


def test_missing_landmark_reads_as_absent_not_as_zero_confidence() -> None:
    """Absence and low confidence are different facts and must stay distinguishable.

    Collapsing them is how you end up asserting on a keypoint the backend never reported.
    """
    frame = make_frame({KeypointName.NOSE: make_landmark()})
    assert frame.get(KeypointName.LEFT_KNEE) is None
    assert KeypointName.LEFT_KNEE not in frame
    assert frame.get(KeypointName.NOSE) is not None


def test_frame_is_iterable_over_reported_keypoints() -> None:
    frame = make_frame({KeypointName.NOSE: make_landmark(), KeypointName.NECK: make_landmark()})
    assert set(frame) == {KeypointName.NOSE, KeypointName.NECK}
    assert len(frame) == 2


def test_has_world_landmarks_requires_every_reported_point_to_carry_metric_3d() -> None:
    """The ADR-0005 switch: world-space geometry, or image space with torso normalisation.

    A partially-world frame must read as False. Answering True would let a metric silently mix
    metres and normalised pixels in one calculation.
    """
    world = make_landmark(x_world=0.0, y_world=0.0, z_world=0.0)
    assert make_frame({KeypointName.NOSE: world}).has_world_landmarks is True
    assert (
        make_frame(
            {KeypointName.NOSE: world, KeypointName.NECK: make_landmark()}
        ).has_world_landmarks
        is False
    )


def test_empty_frame_has_no_world_landmarks() -> None:
    """`all()` over nothing is True, which would be exactly the wrong answer here."""
    assert make_frame({}).has_world_landmarks is False


@pytest.mark.parametrize("field", ["visibility", "presence"])
@pytest.mark.parametrize("value", [-0.01, 1.01, 5.0])
def test_a_confidence_outside_zero_to_one_is_rejected(field: str, value: float) -> None:
    """Caught at the boundary, where the landmark responsible can still be named.

    `Finding` already refuses an impossible confidence, but that is three layers downstream and the
    rules layer clamps on the way there — so a backend emitting `visibility=5.0` produced clean
    findings and a metrics section that reported a confidence of `5.0` straight to the API. The
    docstrings on both fields have always said `[0, 1]`; nothing enforced it.

    Deliberately unlike `x` and `y`, which are *not* range-checked because MediaPipe extrapolates
    them past the frame edge on purpose and that is real signal.
    """
    defaults = {"x": 0.5, "y": 0.5, "visibility": 0.9, "presence": 0.9}
    with pytest.raises(ValueError, match=f"{field} must be a probability"):
        Landmark(**{**defaults, field: value})


def test_coordinates_outside_the_frame_are_still_allowed() -> None:
    """The other half of the rule above, so the new check cannot creep onto `x` and `y`.

    An extrapolated joint past the frame edge is the signal `OUT_OF_FRAME` is built on. Rejecting
    it here would destroy the distinction the status model exists for.
    """
    assert Landmark(x=-0.4, y=1.8, visibility=0.9, presence=0.2).x == -0.4
