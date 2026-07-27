"""Tests for the geometry primitives.

Deliberately about trigonometry and nothing else. Every metric in the package rests on these five
functions, so proving them here is what lets the metric tests be about posture.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from posture_core.geometry import (
    GRAVITY,
    UP,
    DegenerateVectorError,
    Vector3,
    angle_between,
    distance,
    image_vec,
    midpoint,
    norm,
    signed_angle_to_vertical,
    unit,
    world_vec,
)
from posture_core.keypoints import Landmark

FORWARD = np.array([1.0, 0.0, 0.0])
BACKWARD = np.array([-1.0, 0.0, 0.0])


def vec(x: float, y: float, z: float = 0.0) -> Vector3:
    return np.array([x, y, z])


def test_up_points_up_in_a_y_down_world() -> None:
    """The one constant that inverts every posture verdict in the project if it is wrong.

    MediaPipe world landmarks are y-down, so "up" is negative y. Asserted rather than assumed
    because nothing else would fail loudly: a flipped sign turns every slouch into a recline and
    every recline into a slouch, and both remain plausible numbers.
    """
    assert UP.tolist() == [0.0, -1.0, 0.0]
    assert GRAVITY.tolist() == [0.0, 1.0, 0.0]


# ---------------------------------------------------------------------------------------------
# angle_between
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (vec(1, 0), vec(1, 0), 0.0),
        (vec(1, 0), vec(0, 1), 90.0),
        (vec(1, 0), vec(-1, 0), 180.0),
        (vec(1, 0), vec(1, 1), 45.0),
        (vec(0, 0, 1), vec(0, 1, 0), 90.0),
    ],
)
def test_angle_between_measures_the_unsigned_angle(a: Vector3, b: Vector3, expected: float) -> None:
    assert angle_between(a, b) == pytest.approx(expected, abs=1e-9)


def test_angle_between_is_symmetric() -> None:
    a, b = vec(3, 1, -2), vec(-1, 4, 0.5)
    assert angle_between(a, b) == pytest.approx(angle_between(b, a))


def test_angle_between_is_invariant_to_magnitude() -> None:
    """Only direction matters, so a metre-long segment and a kilometre-long one agree.

    This is the property that makes every angular metric scale-invariant for free — the defect
    the whole rebuild exists to fix (FINDINGS §2.6).
    """
    assert angle_between(vec(1, 0), vec(0, 5000)) == pytest.approx(90.0)


@pytest.mark.parametrize("epsilon", [1e-7, 1e-9, 1e-12])
def test_nearly_parallel_vectors_do_not_produce_nan(epsilon: float) -> None:
    """The reason for atan2 rather than acos.

    `acos(dot / (|a| * |b|))` can be handed an argument fractionally outside [-1, 1] by floating
    point, producing a `nan` that propagates silently through an entire report — a wrong answer
    with no error anywhere. atan2 is well-conditioned here and needs no clamping.
    """
    result = angle_between(vec(1, 0), vec(1, epsilon))
    assert not math.isnan(result)
    assert 0.0 <= result < 1.0


def test_nearly_antiparallel_vectors_do_not_produce_nan() -> None:
    result = angle_between(vec(1, 0), vec(-1, 1e-12))
    assert not math.isnan(result)
    assert result == pytest.approx(180.0, abs=1e-6)


# ---------------------------------------------------------------------------------------------
# signed_angle_to_vertical
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("vector", "forward", "expected"),
    [
        (vec(0, -1), FORWARD, 0.0),  # straight up
        (vec(1, -1), FORWARD, 45.0),  # leaning forward
        (vec(-1, -1), FORWARD, -45.0),  # reclining
        (vec(1, 0), FORWARD, 90.0),  # horizontal, forward
        (vec(0, 1), FORWARD, 180.0),  # upside down
        (vec(1, -1), BACKWARD, -45.0),  # same vector, subject facing the other way
    ],
)
def test_signed_angle_measures_lean_relative_to_the_subjects_facing(
    vector: Vector3, forward: Vector3, expected: float
) -> None:
    assert signed_angle_to_vertical(vector, forward) == pytest.approx(expected, abs=1e-9)


def test_the_sign_separates_slouching_from_reclining() -> None:
    """An `abs()` in the wrong place would collapse two opposite postures into one verdict.

    They carry different advice, so the metric has to be able to tell them apart — and a test that
    only checked magnitude would pass against an implementation that could not.
    """
    forward_lean = signed_angle_to_vertical(vec(0.5, -1), FORWARD)
    backward_lean = signed_angle_to_vertical(vec(-0.5, -1), FORWARD)
    assert forward_lean > 0 > backward_lean
    assert forward_lean == pytest.approx(-backward_lean)


def test_lateral_sway_is_ignored_rather_than_folded_into_the_lean() -> None:
    """Bending sideways is not slouching, and must not read as it.

    The measurement is a projection onto the sagittal plane, so a large `z` component — the
    subject leaning toward or away from the camera in a lateral view — leaves the answer alone.
    """
    upright = signed_angle_to_vertical(vec(0, -1, 0), FORWARD)
    swaying = signed_angle_to_vertical(vec(0, -1, 3), FORWARD)
    assert upright == pytest.approx(swaying)


def test_signed_angle_is_invariant_to_segment_length() -> None:
    assert signed_angle_to_vertical(vec(1, -1), FORWARD) == pytest.approx(
        signed_angle_to_vertical(vec(100, -100), FORWARD)
    )


# ---------------------------------------------------------------------------------------------
# Degeneracy
# ---------------------------------------------------------------------------------------------


def test_a_zero_length_vector_has_no_direction() -> None:
    """Not 0°, not 90°, and not `None` — a question with no answer.

    Real backends produce coincident landmarks when a joint is fully occluded and the model
    collapses two points onto each other. Returning a plausible default here is precisely how the
    legacy engine reported good posture for images it had failed to assess.
    """
    with pytest.raises(DegenerateVectorError):
        unit(vec(0, 0, 0))
    with pytest.raises(DegenerateVectorError):
        angle_between(vec(0, 0, 0), vec(1, 0))
    with pytest.raises(DegenerateVectorError):
        signed_angle_to_vertical(vec(0, 0, 0), FORWARD)


def test_a_vector_entirely_outside_the_sagittal_plane_has_no_inclination() -> None:
    """Purely lateral: no up component and no forward component, so "lean" is undefined."""
    with pytest.raises(DegenerateVectorError):
        signed_angle_to_vertical(vec(0, 0, 1), FORWARD)


# ---------------------------------------------------------------------------------------------
# Conversions and simple helpers
# ---------------------------------------------------------------------------------------------


def test_world_vec_returns_metres_when_the_backend_supplies_them() -> None:
    landmark = Landmark(
        x=0.5, y=0.5, visibility=1.0, presence=1.0, x_world=0.1, y_world=-0.2, z_world=0.3
    )
    assert world_vec(landmark).tolist() == [0.1, -0.2, 0.3]  # type: ignore[union-attr]


def test_world_vec_returns_none_for_a_two_dimensional_backend() -> None:
    """Absence, not zeros. A 2D backend that reported (0, 0, 0) would place every landmark at the
    hip origin, which is a well-formed answer and completely wrong."""
    assert world_vec(Landmark(x=0.5, y=0.5, visibility=1.0, presence=1.0)) is None


def test_image_vec_scales_by_the_frame_size_not_by_the_normalised_value() -> None:
    """Skipping this is a real and subtle bug: an angle computed from raw normalised coordinates
    is measured in a space stretched by the aspect ratio, so a 45° lean reads as 38° on 16:9."""
    landmark = Landmark(x=0.25, y=0.5, visibility=1.0, presence=1.0)
    assert image_vec(landmark, 1920, 1080).tolist() == [480.0, 540.0, 0.0]


def test_distance_and_midpoint() -> None:
    a, b = vec(0, 0), vec(3, 4)
    assert distance(a, b) == pytest.approx(5.0)
    assert midpoint(a, b).tolist() == [1.5, 2.0, 0.0]


def test_unit_preserves_direction_and_normalises_length() -> None:
    result = unit(vec(0, -7))
    assert norm(result) == pytest.approx(1.0)
    assert result.tolist() == [0.0, -1.0, 0.0]


def test_the_figure_builder_uses_this_modules_up_vector() -> None:
    """One definition of "up" in the project, not two.

    `synthetic.py` builds every test figure around a vertical axis, and `geometry.py` measures
    every angle against one. A duplicated constant survives a sign flip in one file and not the
    other, which would invert every posture verdict computed from a synthetic figure while leaving
    both modules internally consistent and silent.
    """
    from posture_core.synthetic import (
        Facing,
        View,
        _axes,
    )

    assert _axes(Facing.RIGHT, View.LATERAL).up == (UP[0], UP[1], UP[2])
