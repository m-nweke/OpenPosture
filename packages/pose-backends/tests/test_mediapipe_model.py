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
    return image  # type: ignore[no-any-return]


# The three seated side-view fixtures the whole project is calibrated against. Named explicitly
# rather than globbed so that a fixture disappearing fails the test instead of shrinking it.
SEATED_FIXTURES = ["hunchback_right.jpg", "reclined_right.jpg", "straight_armsfolded.jpg"]


@pytest.mark.parametrize("fixture", SEATED_FIXTURES)
def test_detects_a_confident_skeleton_on_real_photographs(
    backend: MediaPipeBackend, fixture: str
) -> None:
    """25 of 34 landmarks above the visibility threshold, on a real seated subject.

    The number is a floor, not a target: a side-on seated pose legitimately occludes one arm and
    part of one leg. What it rules out is the failure mode where the model returns a full skeleton
    of low-confidence guesses, which would look like success to any test that only counted keys.
    """
    frame = backend.detect(load(fixture))
    assert frame is not None, f"no pose detected in {fixture}"
    confident = [lm for lm in frame.landmarks.values() if lm.visibility >= VISIBILITY_THRESHOLD]
    assert len(confident) >= 25, f"{fixture}: only {len(confident)} confident landmarks"


def test_real_frames_carry_world_landmarks(backend: MediaPipeBackend) -> None:
    """The entire reason for choosing this model (ADR-0002, ADR-0005).

    Without metric 3D, every angular metric in Epic C goes back to image space with torso-length
    normalisation — the problem the model switch was meant to dissolve. Worth asserting against
    the real runtime, because no stub can tell you the runtime still populates this array.
    """
    frame = backend.detect(load(SEATED_FIXTURES[0]))
    assert frame is not None
    assert frame.has_world_landmarks is True


def test_neck_is_derived_on_real_output(backend: MediaPipeBackend) -> None:
    frame = backend.detect(load(SEATED_FIXTURES[0]))
    assert frame is not None
    assert KeypointName.NECK in frame


def test_returns_none_on_an_image_with_no_person(backend: MediaPipeBackend) -> None:
    """Flat grey: no person, and no exception either."""
    blank = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert backend.detect(blank) is None


def test_warmup_makes_the_first_real_inference_materially_faster(
    backend: MediaPipeBackend,
) -> None:
    """Proves `warmup()` does what it claims, which is the only way to know it is worth calling.

    MediaPipe builds its inference graph lazily, so the first `detect()` on a cold backend pays
    for it. A fresh backend is constructed here rather than reusing the module-scoped one, which
    other tests have already warmed.

    Asserted as a ratio against the *cold* call rather than an absolute millisecond budget:
    absolute latency varies by an order of magnitude between a laptop and a CI runner, and a
    threshold that survives both would be too loose to mean anything.
    """
    cold = MediaPipeBackend(model_path())
    first = _timed(cold, load(SEATED_FIXTURES[0]))
    cold.warmup()
    warmed = min(_timed(cold, load(SEATED_FIXTURES[0])) for _ in range(3))
    assert warmed < first * 0.8, f"cold {first:.1f} ms vs warmed {warmed:.1f} ms"


def _timed(backend: MediaPipeBackend, image: NDArray[np.uint8]) -> float:
    frame: PoseFrame | None = backend.detect(image)
    assert frame is not None
    return frame.inference_ms
