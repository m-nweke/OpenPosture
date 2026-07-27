"""Adapter tests for `MediaPipeBackend`, run against a stub detector.

**No model file, no mediapipe installed, no download.** That is the point: a transposed landmark
index is exactly the class of bug that made the legacy engine wrong (FINDINGS §2.1) and it is
invisible without a test, so the test has to be cheap enough to run on every pull request. The
`detector_factory` seam is what makes that possible.

Real-model tests live in `test_mediapipe_model.py`, marked `pytest.mark.model`, and are
deselected in required CI.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest
from numpy.typing import NDArray

from pose_backends import InvalidImageError, MediaPipeBackend, ModelNotFoundError
from pose_backends.mediapipe_backend import (
    MEDIAPIPE_INDEX_TO_KEYPOINT,
    RawDetection,
    RawLandmark,
)
from posture_core import KeypointName

MODEL = "/nonexistent/pose_landmarker_full.task"


class StubDetector:
    """Stands in for the MediaPipe Tasks graph, and records what it was handed."""

    def __init__(self, detection: RawDetection | None) -> None:
        self.detection = detection
        self.calls: list[NDArray[np.uint8]] = []
        self.closed = 0

    def detect(self, image_rgb: NDArray[np.uint8]) -> RawDetection | None:
        self.calls.append(image_rgb)
        return self.detection

    def close(self) -> None:
        self.closed += 1


def raw(index: int, *, visibility: float = 0.9, presence: float = 0.95) -> RawLandmark:
    """A landmark whose coordinates encode its index, so a mis-mapping is legible in a failure."""
    return RawLandmark(
        x=index / 100.0,
        y=index / 200.0,
        z=index / 400.0,
        visibility=visibility,
        presence=presence,
    )


def full_detection(*, world: bool = True) -> RawDetection:
    landmarks = [raw(i) for i in range(33)]
    world_landmarks = (
        [RawLandmark(x=i, y=-i, z=i / 2, visibility=0.0, presence=0.0) for i in range(33)]
        if world
        else None
    )
    return RawDetection(landmarks=landmarks, world_landmarks=world_landmarks)


def make_backend(detection: RawDetection | None) -> tuple[MediaPipeBackend, StubDetector]:
    stub = StubDetector(detection)
    backend = MediaPipeBackend(MODEL, detector_factory=lambda _path, _conf: stub)
    return backend, stub


def image(width: int = 640, height: int = 480) -> NDArray[np.uint8]:
    return np.zeros((height, width, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------------------------
# The mapping table itself
# ---------------------------------------------------------------------------------------------


def test_mapping_covers_every_mediapipe_index_exactly_once() -> None:
    assert sorted(MEDIAPIPE_INDEX_TO_KEYPOINT) == list(range(33))
    assert len(set(MEDIAPIPE_INDEX_TO_KEYPOINT.values())) == 33


def test_mapping_covers_every_canonical_keypoint_except_the_derived_neck() -> None:
    """NECK is the only canonical name no backend reports; everything else must be reachable.

    An unreachable name would be a keypoint the rules engine can ask for and never receive — a
    metric that silently abstains forever, for a reason nobody would think to look for here.
    """
    mapped = set(MEDIAPIPE_INDEX_TO_KEYPOINT.values())
    assert set(KeypointName) - mapped == {KeypointName.NECK}


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        # The landmarks the rules engine actually uses, pinned individually against ADR-0002's
        # table. Spot-checking a handful is not enough here: the legacy bug was one inverted pair.
        (0, KeypointName.NOSE),
        (7, KeypointName.LEFT_EAR),
        (8, KeypointName.RIGHT_EAR),
        (11, KeypointName.LEFT_SHOULDER),
        (12, KeypointName.RIGHT_SHOULDER),
        (13, KeypointName.LEFT_ELBOW),
        (14, KeypointName.RIGHT_ELBOW),
        (15, KeypointName.LEFT_WRIST),
        (16, KeypointName.RIGHT_WRIST),
        (23, KeypointName.LEFT_HIP),
        (24, KeypointName.RIGHT_HIP),
        (25, KeypointName.LEFT_KNEE),
        (26, KeypointName.RIGHT_KNEE),
        (27, KeypointName.LEFT_ANKLE),
        (28, KeypointName.RIGHT_ANKLE),
        (29, KeypointName.LEFT_HEEL),
        (30, KeypointName.RIGHT_HEEL),
        (31, KeypointName.LEFT_FOOT_INDEX),
        (32, KeypointName.RIGHT_FOOT_INDEX),
    ],
)
def test_index_maps_to_the_keypoint_adr_0002_says_it_does(
    index: int, expected: KeypointName
) -> None:
    assert MEDIAPIPE_INDEX_TO_KEYPOINT[index] is expected


def test_landmarks_are_placed_under_the_name_matching_their_index() -> None:
    """End-to-end version of the table test: a transposition inside `_to_canonical` fails here.

    `raw()` encodes the index into the coordinates, so the assertion checks placement rather than
    merely presence.
    """
    backend, _ = make_backend(full_detection())
    frame = backend.detect(image())
    assert frame is not None
    for index, name in MEDIAPIPE_INDEX_TO_KEYPOINT.items():
        landmark = frame.get(name)
        assert landmark is not None
        assert landmark.x == pytest.approx(index / 100.0)
        assert landmark.y == pytest.approx(index / 200.0)


# ---------------------------------------------------------------------------------------------
# NECK derivation
# ---------------------------------------------------------------------------------------------


def test_neck_is_the_midpoint_of_the_two_shoulders() -> None:
    backend, _ = make_backend(full_detection())
    frame = backend.detect(image())
    assert frame is not None
    left = frame.get(KeypointName.LEFT_SHOULDER)
    right = frame.get(KeypointName.RIGHT_SHOULDER)
    neck = frame.get(KeypointName.NECK)
    assert left is not None and right is not None and neck is not None
    assert neck.x == pytest.approx((left.x + right.x) / 2)
    assert neck.y == pytest.approx((left.y + right.y) / 2)
    assert left.x_world is not None and right.x_world is not None
    assert neck.x_world == pytest.approx((left.x_world + right.x_world) / 2)


def test_neck_takes_the_minimum_confidence_of_its_two_shoulders() -> None:
    """Not the mean.

    A derived point cannot be more trustworthy than the least trustworthy thing it came from.
    Averaging would let one confidently-seen shoulder launder an unseen one into a plausible neck
    — which is how a metric ends up reported with confidence it has not earned.
    """
    landmarks = [raw(i) for i in range(33)]
    landmarks[11] = raw(11, visibility=0.2, presence=0.3)
    landmarks[12] = raw(12, visibility=0.95, presence=0.99)
    backend, _ = make_backend(RawDetection(landmarks=landmarks))

    frame = backend.detect(image())
    assert frame is not None
    neck = frame.get(KeypointName.NECK)
    assert neck is not None
    assert neck.visibility == pytest.approx(0.2)
    assert neck.presence == pytest.approx(0.3)


def test_neck_is_absent_when_a_shoulder_is_missing() -> None:
    """Absent, not guessed. A one-shoulder neck would be an invented measurement."""
    backend, _ = make_backend(RawDetection(landmarks=[raw(i) for i in range(12)]))  # 0..11 only
    frame = backend.detect(image())
    assert frame is not None
    assert KeypointName.LEFT_SHOULDER in frame
    assert KeypointName.RIGHT_SHOULDER not in frame
    assert KeypointName.NECK not in frame


# ---------------------------------------------------------------------------------------------
# Passthrough
# ---------------------------------------------------------------------------------------------


def test_world_landmarks_visibility_and_presence_pass_through() -> None:
    backend, _ = make_backend(full_detection())
    frame = backend.detect(image())
    assert frame is not None
    hip = frame.get(KeypointName.LEFT_HIP)
    assert hip is not None
    assert (hip.x_world, hip.y_world, hip.z_world) == (23.0, -23.0, 11.5)
    assert hip.visibility == pytest.approx(0.9)
    assert hip.presence == pytest.approx(0.95)
    assert frame.has_world_landmarks is True


def test_absent_world_landmarks_leave_the_frame_in_image_space() -> None:
    """The ADR-0005 fallback path, and it must be detectable rather than silently zero-filled."""
    backend, _ = make_backend(full_detection(world=False))
    frame = backend.detect(image())
    assert frame is not None
    assert frame.has_world_landmarks is False
    nose = frame.get(KeypointName.NOSE)
    assert nose is not None and nose.x_world is None


def test_mismatched_world_array_length_drops_world_rather_than_pairing_by_index() -> None:
    """Silently wrong beats loudly wrong is never the trade here.

    Pairing arrays of different lengths by index attaches one landmark's metres to another's
    pixels: no exception, no warning, just angles that are quietly incorrect.
    """
    detection = RawDetection(
        landmarks=[raw(i) for i in range(33)],
        world_landmarks=[raw(i) for i in range(20)],
    )
    backend, _ = make_backend(detection)
    frame = backend.detect(image())
    assert frame is not None
    assert frame.has_world_landmarks is False


def test_unknown_landmark_indices_are_ignored() -> None:
    """A future variant reporting extra points must not widen the canonical schema."""
    detection = RawDetection(landmarks=[raw(i) for i in range(40)])
    backend, _ = make_backend(detection)
    frame = backend.detect(image())
    assert frame is not None
    assert len(frame) == 34  # 33 mapped + derived NECK


def test_frame_is_stamped_with_backend_image_size_and_latency() -> None:
    backend, _ = make_backend(full_detection())
    frame = backend.detect(image(width=1280, height=720))
    assert frame is not None
    assert frame.backend == "mediapipe"
    assert (frame.image_width, frame.image_height) == (1280, 720)
    assert frame.inference_ms >= 0.0


def test_image_is_converted_from_bgr_to_rgb_before_inference() -> None:
    """Getting this wrong does not crash — it just quietly degrades detection.

    cv2 decodes to BGR and MediaPipe expects RGB. The stub records the array it received, so the
    channel order is asserted rather than assumed. Contiguity is checked too: reversing the last
    axis yields a negative-stride view that the native binding cannot accept.
    """
    backend, stub = make_backend(full_detection())
    bgr = np.zeros((4, 4, 3), dtype=np.uint8)
    bgr[:, :, 0] = 10  # blue
    bgr[:, :, 1] = 20  # green
    bgr[:, :, 2] = 30  # red

    backend.detect(bgr)

    received = stub.calls[0]
    assert received[0, 0].tolist() == [30, 20, 10]
    assert received.flags["C_CONTIGUOUS"]


# ---------------------------------------------------------------------------------------------
# Error and degradation paths
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("detection", [None, RawDetection(landmarks=[])])
def test_no_pose_returns_none_without_raising(detection: RawDetection | None) -> None:
    """The single most important line of the contract.

    An image with nobody in it is an ordinary outcome of a posture app. The legacy engine returned
    `None` for this *and* for inference failure, and the caller rendered `None` as "Straight back
    position" (FINDINGS §2.5) — so an empty desk and a crash both told the user their posture was
    fine. Here `None` means one thing and breakage raises.
    """
    backend, _ = make_backend(detection)
    assert backend.detect(image()) is None


def test_missing_model_file_raises_a_clear_error_not_an_obscure_traceback() -> None:
    """Names the path and the remedy. Reached before mediapipe is imported, so it works here."""
    with pytest.raises(ModelNotFoundError, match="make fetch-model"):
        MediaPipeBackend("/nonexistent/definitely-not-a-model.task")


@pytest.mark.parametrize(
    "bad",
    [
        np.zeros((480, 640), dtype=np.uint8),  # greyscale
        np.zeros((480, 640, 4), dtype=np.uint8),  # BGRA
        np.zeros((0, 640, 3), dtype=np.uint8),  # empty
    ],
)
def test_malformed_arrays_raise_rather_than_returning_none(bad: NDArray[np.uint8]) -> None:
    """A bad array is a caller bug, not a bad photo, so it must not look like "no person"."""
    backend, _ = make_backend(full_detection())
    with pytest.raises(InvalidImageError):
        backend.detect(bad)


# ---------------------------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------------------------


def test_model_is_loaded_once_not_per_call() -> None:
    """The defect that made the legacy engine unusable from a web request.

    Its model was a module global built at import time from a cwd-relative config, so it could
    neither be loaded once at startup nor be imported at all. Here the factory runs exactly once,
    in `__init__`.
    """
    factory_calls: list[tuple[object, float]] = []

    def factory(path: object, confidence: float) -> StubDetector:
        factory_calls.append((path, confidence))
        return StubDetector(full_detection())

    backend = MediaPipeBackend(MODEL, detector_factory=factory)
    backend.detect(image())
    backend.detect(image())
    assert len(factory_calls) == 1


def test_warmup_runs_one_inference_and_tolerates_finding_nobody() -> None:
    """A synthetic frame contains no person, so warmup must survive the `None` path."""
    backend, stub = make_backend(None)
    backend.warmup()
    assert len(stub.calls) == 1
    assert stub.calls[0].shape == (256, 256, 3)


def test_close_releases_the_native_graph_and_is_idempotent() -> None:
    backend, stub = make_backend(full_detection())
    backend.close()
    backend.close()
    assert stub.closed == 2


def test_model_path_is_absolute_so_the_backend_does_not_depend_on_cwd() -> None:
    """The legacy code used relative paths for both weights and config, so it only ran from
    inside `API/`. Resolving on construction is the fix, and it is worth a test because the
    failure mode is environment-dependent and would not reproduce for whoever wrote it."""
    backend, _ = make_backend(full_detection())
    assert backend.model_path.is_absolute()


def test_backend_satisfies_the_protocol() -> None:
    from pose_backends import PoseBackend

    backend, _ = make_backend(full_detection())
    assert isinstance(backend, PoseBackend)


def test_raw_landmarks_sequence_type_is_not_mutated_by_the_adapter() -> None:
    """The adapter must not write back into the detector's arrays; a real one reuses them."""
    landmarks: Sequence[RawLandmark] = [raw(i) for i in range(33)]
    backend, _ = make_backend(RawDetection(landmarks=landmarks))
    backend.detect(image())
    assert landmarks[11] == raw(11)
