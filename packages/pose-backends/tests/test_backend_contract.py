"""One suite, run against **every** backend, real and fake.

This is what stops `FakePoseBackend` from drifting into a shape the real adapter never produces.
Without it the fake is a free variable: it can grow a keypoint MediaPipe does not report, or lose
world coordinates, or start returning a frame where the real one returns `None` — and every
downstream test resting on the fake would keep passing while asserting something untrue about the
production path.

The real backend's parameter is marked `pytest.mark.model`, so required CI runs the fake leg only
and the on-demand model workflow (OP-21) runs both. That asymmetry is the deliberate trade: cheap
protection on every pull request, full protection when the weights are available.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from pose_backends import FakePoseBackend, PoseBackend, PosePreset
from posture_core import KeypointName

WIDTH, HEIGHT = 640, 480

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "images"
DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "models" / "pose_landmarker_full.task"

# The landmarks every backend must supply for the rules engine to function at all. A backend
# missing any of these cannot produce a trunk inclination, which is the project's core metric.
REQUIRED = frozenset(
    {
        KeypointName.LEFT_SHOULDER,
        KeypointName.RIGHT_SHOULDER,
        KeypointName.LEFT_HIP,
        KeypointName.RIGHT_HIP,
        KeypointName.NECK,
    }
)


@dataclass(frozen=True)
class Case:
    """A backend plus the inputs needed to drive it down both paths."""

    with_person: PoseBackend
    without_person: PoseBackend
    image_with_person: NDArray[np.uint8]
    image_without_person: NDArray[np.uint8]


def _blank() -> NDArray[np.uint8]:
    return np.full((HEIGHT, WIDTH, 3), 128, dtype=np.uint8)


def _fake_case() -> Case:
    return Case(
        with_person=FakePoseBackend(PosePreset.STRAIGHT, image_width=WIDTH, image_height=HEIGHT),
        # The fake selects its scenario by construction, so "no person" is a second instance
        # rather than a second input. That difference is confined to this fixture, which is why
        # the assertions below can be written once for both backends.
        without_person=FakePoseBackend(PosePreset.NO_PERSON),
        image_with_person=_blank(),
        image_without_person=_blank(),
    )


def _mediapipe_case() -> Case:
    from pose_backends import MediaPipeBackend

    model = Path(os.environ.get("MODEL_PATH", DEFAULT_MODEL))
    if not model.is_file():
        pytest.skip(f"no model at {model} — run `make fetch-model`")

    import cv2

    photo = cv2.imread(str(FIXTURES / "straight_armsfolded.jpg"))
    if photo is None:
        pytest.skip("fixture image could not be decoded")

    backend = MediaPipeBackend(model)
    return Case(
        with_person=backend,
        without_person=backend,
        image_with_person=photo,
        image_without_person=_blank(),
    )


@pytest.fixture(
    params=[
        pytest.param(_fake_case, id="fake"),
        pytest.param(_mediapipe_case, id="mediapipe", marks=pytest.mark.model),
    ]
)
def case(request: pytest.FixtureRequest) -> Case:
    factory: object = request.param
    return factory()  # type: ignore[operator, no-any-return]


# ---------------------------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------------------------


def test_conforms_to_the_protocol(case: Case) -> None:
    assert isinstance(case.with_person, PoseBackend)


def test_name_is_stable_and_stamped_onto_the_frame(case: Case) -> None:
    """Provenance travels with the data, so a stored report can say which engine produced it."""
    frame = case.with_person.detect(case.image_with_person)
    assert frame is not None
    assert case.with_person.name
    assert frame.backend == case.with_person.name


def test_reports_only_canonical_keypoints(case: Case) -> None:
    """A backend must not widen the schema. The canonical set is the project's, not the model's."""
    frame = case.with_person.detect(case.image_with_person)
    assert frame is not None
    assert set(frame.landmarks) <= set(KeypointName)


def test_supplies_the_keypoints_the_rules_engine_cannot_work_without(case: Case) -> None:
    frame = case.with_person.detect(case.image_with_person)
    assert frame is not None
    assert set(frame.landmarks) >= REQUIRED


def test_neck_is_the_shoulder_midpoint_on_every_backend(case: Case) -> None:
    """The one derived keypoint, and both backends must derive it the same way.

    If they disagreed, a threshold tuned against fake frames would be tuned against a skeleton the
    real backend never produces — the exact silent drift this suite exists to prevent.
    """
    frame = case.with_person.detect(case.image_with_person)
    assert frame is not None
    left = frame.landmarks[KeypointName.LEFT_SHOULDER]
    right = frame.landmarks[KeypointName.RIGHT_SHOULDER]
    neck = frame.landmarks[KeypointName.NECK]
    assert neck.x == pytest.approx((left.x + right.x) / 2, abs=1e-6)
    assert neck.y == pytest.approx((left.y + right.y) / 2, abs=1e-6)


def test_confidences_are_probabilities(case: Case) -> None:
    frame = case.with_person.detect(case.image_with_person)
    assert frame is not None
    for name, landmark in frame.landmarks.items():
        assert 0.0 <= landmark.visibility <= 1.0, name
        assert 0.0 <= landmark.presence <= 1.0, name


def test_world_coordinates_are_present_for_all_landmarks_or_none(case: Case) -> None:
    """The optionality rule, stated as a frame-level property.

    A frame where some points carry metres and others do not would let a metric mix coordinate
    systems in one calculation — wrong answers, no error. `Landmark` enforces all-or-nothing per
    point; this enforces it across the frame.
    """
    frame = case.with_person.detect(case.image_with_person)
    assert frame is not None
    with_world = [lm.has_world for lm in frame.landmarks.values()]
    assert all(with_world) or not any(with_world)


def test_frame_metadata_has_the_expected_types(case: Case) -> None:
    frame = case.with_person.detect(case.image_with_person)
    assert frame is not None
    assert isinstance(frame.image_width, int) and frame.image_width > 0
    assert isinstance(frame.image_height, int) and frame.image_height > 0
    assert isinstance(frame.inference_ms, float) and frame.inference_ms >= 0.0
    for landmark in frame.landmarks.values():
        assert isinstance(landmark.x, float)
        assert isinstance(landmark.y, float)


def test_returns_none_rather_than_raising_when_there_is_no_person(case: Case) -> None:
    """The behaviour the entire error model rests on, checked identically on both backends."""
    assert case.without_person.detect(case.image_without_person) is None


def test_warmup_is_idempotent_and_never_raises(case: Case) -> None:
    case.with_person.warmup()
    case.with_person.warmup()


def test_repeated_detection_is_stable(case: Case) -> None:
    """The same input twice gives the same skeleton.

    Latency will differ, so coordinates are compared rather than whole frames — a real backend
    that reported different landmarks for an identical image would make every downstream
    assertion flaky, and the fake would hide it.
    """
    first = case.with_person.detect(case.image_with_person)
    second = case.with_person.detect(case.image_with_person)
    assert first is not None and second is not None
    assert set(first.landmarks) == set(second.landmarks)
    for name, landmark in first.landmarks.items():
        assert landmark.x == pytest.approx(second.landmarks[name].x, abs=1e-6), name
        assert landmark.y == pytest.approx(second.landmarks[name].y, abs=1e-6), name
