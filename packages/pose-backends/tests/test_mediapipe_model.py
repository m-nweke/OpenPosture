"""Real-model tests: actual weights, actual inference, actual fixture photographs.

**Deselected in required CI** with `-m "not model"`. They need a 9 MB download and the full
mediapipe extra, and the pull-request workflow deliberately downloads nothing — the stubbed
adapter tests in `test_mediapipe_backend.py` are what protect every PR. These run on demand in
`model-validation.yml` (OP-21).

Keeping them in the repository rather than deleting them matters: the stub proves the *mapping*
is right, and only these prove the mapping is right about a *real model*. A stub agreeing with
itself is not evidence.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from pose_backends import MediaPipeBackend
from posture_core import KeypointName, PoseFrame

pytestmark = pytest.mark.model

# Landmarks below this are reported but not trusted. Matches MediaPipe's own default detection
# confidence; the real per-metric thresholds are Epic C's business (OP-24), not the adapter's.
VISIBILITY_THRESHOLD = 0.5

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "images"
DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "models" / "pose_landmarker_full.task"


def model_path() -> Path:
    """Where the weights are. `MODEL_PATH` wins, so a variant can be swapped in (OP-20)."""
    override = os.environ.get("MODEL_PATH")
    return Path(override) if override else DEFAULT_MODEL


@pytest.fixture(scope="module")
def backend() -> MediaPipeBackend:
    path = model_path()
    if not path.is_file():
        pytest.skip(f"no model at {path} — run `make fetch-model`")
    return MediaPipeBackend(path)


def load(name: str) -> NDArray[np.uint8]:
    import cv2

    image = cv2.imread(str(FIXTURES / name))
    if image is None:
        pytest.skip(f"fixture {name} could not be decoded")
    # `asarray` rather than a cast: cv2 ships type stubs, so mypy's view of `imread`'s return
    # type depends on whether the optional extra happens to be installed. Narrowing it here keeps
    # the type check identical with and without mediapipe present.
    return np.asarray(image, dtype=np.uint8)


# The seated side-view fixtures, with the confident-landmark floor each one actually clears.
# Named explicitly rather than globbed so that a fixture disappearing fails the test instead of
# quietly shrinking it, and floored individually rather than uniformly because the differences are
# real and worth recording. Measured 2026-07-26 against pose_landmarker_full at 1280x720.
#
# `straight_armsfolded` sits lower on purpose: the subject's arms are folded, so both wrists and
# all six hand points are genuinely occluded. A uniform floor would either fail this fixture for
# being *correctly* uncertain, or be loosened to the point of proving nothing about the others.
SEATED_FIXTURES: list[tuple[str, int]] = [
    ("hunchback_right.jpg", 25),
    ("reclined_right.jpg", 25),
    ("desk_hunch.jpeg", 25),
    ("kneeling_right.jpg", 25),
    ("straight_armsfolded.jpg", 20),
]

PRIMARY_FIXTURE = SEATED_FIXTURES[0][0]


@pytest.mark.parametrize(("fixture", "minimum"), SEATED_FIXTURES)
def test_detects_a_confident_skeleton_on_real_photographs(
    backend: MediaPipeBackend, fixture: str, minimum: int
) -> None:
    """Enough landmarks above the visibility threshold to actually assess a seated subject.

    A floor, not a target: a side-on pose legitimately occludes the far arm and part of the far
    leg. What it rules out is the failure mode where the model returns a full 34-point skeleton of
    low-confidence guesses — which looks like success to any test that only counts keys, and is
    precisely how a system starts reporting posture it never measured.
    """
    frame = backend.detect(load(fixture))
    assert frame is not None, f"no pose detected in {fixture}"
    confident = [lm for lm in frame.landmarks.values() if lm.visibility >= VISIBILITY_THRESHOLD]
    assert len(confident) >= minimum, f"{fixture}: only {len(confident)} confident landmarks"


@pytest.mark.parametrize(("fixture", "_minimum"), SEATED_FIXTURES)
def test_presence_and_visibility_carry_different_information(
    backend: MediaPipeBackend, fixture: str, _minimum: int
) -> None:
    """The claim ADR-0002 rests on, checked against the real model rather than assumed.

    On every fixture all 34 points are confidently *present* while only 20-28 are confidently
    *visible*. The gap is the occluded set — the far arm, the folded wrists — and it exists only
    because the two signals are independent. Collapse them into one score, as MoveNet does, and
    "I can see your wrist" becomes indistinguishable from "your wrist is in this photograph".
    """
    frame = backend.detect(load(fixture))
    assert frame is not None
    present = sum(1 for lm in frame.landmarks.values() if lm.presence >= VISIBILITY_THRESHOLD)
    visible = sum(1 for lm in frame.landmarks.values() if lm.visibility >= VISIBILITY_THRESHOLD)
    assert present == len(frame)
    assert visible < present


def test_real_frames_carry_world_landmarks(backend: MediaPipeBackend) -> None:
    """The entire reason for choosing this model (ADR-0002, ADR-0005).

    Without metric 3D, every angular metric in Epic C goes back to image space with torso-length
    normalisation — the problem the model switch was meant to dissolve. Worth asserting against
    the real runtime, because no stub can tell you the runtime still populates this array.
    """
    frame = backend.detect(load(PRIMARY_FIXTURE))
    assert frame is not None
    assert frame.has_world_landmarks is True


def test_neck_is_derived_on_real_output(backend: MediaPipeBackend) -> None:
    frame = backend.detect(load(PRIMARY_FIXTURE))
    assert frame is not None
    assert KeypointName.NECK in frame


def test_returns_none_on_an_image_with_no_person(backend: MediaPipeBackend) -> None:
    """Flat grey: no person, and no exception either."""
    blank = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert backend.detect(blank) is None


def test_no_initialisation_cost_is_deferred_past_construction(
    backend: MediaPipeBackend,
) -> None:
    """The property that actually matters, which is not the one warmup was written to provide.

    The plan predicted a lazily-built inference graph, so that a cold backend's first `detect()`
    would be much slower than its second and `warmup()` would be what closed the gap. Measured on
    2026-07-26 (Apple M5, `pose_landmarker_full`, 1280x720), that is **not what MediaPipe 0.10.18
    does** in `RunningMode.IMAGE`: construction costs ~31 ms and every inference thereafter costs
    ~23 ms, first one included. There is nothing left to warm.

    So this asserts the real guarantee — a cold backend's first inference is already at steady
    state, meaning no request ever pays an initialisation cost — rather than a speedup that does
    not exist. `warmup()` stays in the Protocol regardless: it costs one synthetic frame at
    startup, it is what a lazier backend would need, and a contract that only holds for the
    current library version is not a contract.

    A fresh backend is constructed rather than reusing the module-scoped one, which other tests
    have already exercised. Compared as a ratio because absolute latency varies by an order of
    magnitude between a laptop and a CI runner.
    """
    image = load(PRIMARY_FIXTURE)
    cold = MediaPipeBackend(model_path())
    first = _timed(cold, image)
    steady = min(_timed(cold, image) for _ in range(3))
    assert first < steady * 2.0, f"first inference {first:.1f} ms vs steady {steady:.1f} ms"


def test_warmup_does_not_disturb_subsequent_detection(backend: MediaPipeBackend) -> None:
    """Whatever warmup does or does not save, it must not change results.

    It runs an inference on a synthetic frame of a different size, and a backend that carried
    state between calls could return something different afterwards. OP-40 calls this at startup
    on the same instance that then serves every request.
    """
    image = load(PRIMARY_FIXTURE)
    before = backend.detect(image)
    backend.warmup()
    after = backend.detect(image)
    assert before is not None and after is not None
    assert set(before.landmarks) == set(after.landmarks)
    for name, landmark in before.landmarks.items():
        assert landmark.x == pytest.approx(after.landmarks[name].x, abs=1e-9), name


def _timed(backend: MediaPipeBackend, image: NDArray[np.uint8]) -> float:
    frame: PoseFrame | None = backend.detect(image)
    assert frame is not None
    return frame.inference_ms
