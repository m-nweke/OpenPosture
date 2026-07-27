"""The real inference adapter: MediaPipe Pose Landmarker behind the ``PoseBackend`` Protocol.

This module is where the one heavy, platform-fragile dependency in the project is quarantined.
Everything MediaPipe-specific — the 33 integer landmark indices, the Tasks API, the RGB channel
order, the vendored C++ binding — stops here. Rules code sees canonical named keypoints.

## The two-layer split, and why it is not over-engineering

``MediaPipeBackend`` does the *translation*: timing, colour conversion, index-to-name mapping,
``NECK`` derivation, ``PoseFrame`` construction. ``_TasksDetector`` does the *inference*, and is
the only thing in the file that imports ``mediapipe``.

The seam between them is :class:`RawPoseDetector`. It buys the thing the ticket asks for: the
mapping tests run against a stub detector, with **no model file and no mediapipe installed**, in
required CI. A transposed landmark index is exactly the class of bug that made the legacy engine
wrong (FINDINGS §2.1) and it is invisible without a test — so that test must be cheap enough to
run on every pull request, which means it cannot need 300 MB of wheels and a model download.

Real-model tests exist too, marked ``pytest.mark.model``, and run on demand (OP-21).

## Legacy defects this design forecloses

* **No module-global model.** The legacy engine built its model at import time from a
  cwd-relative config, so neither ``process()`` function was importable and the whole thing only
  ran from inside ``API/``. Here the model is an instance attribute loaded in ``__init__``, and
  the path is a constructor argument.
* **No laterality flag.** MediaPipe returns named LEFT and RIGHT landmarks. The hand-rolled ``f``
  flag that made spine classification backwards for one facing direction has nothing to key off.
* **No cwd dependency.** ``model_path`` is resolved to an absolute path on construction.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias

import numpy as np
from numpy.typing import NDArray

from pose_backends.errors import (
    BackendUnavailableError,
    InvalidImageError,
    ModelLoadError,
    ModelNotFoundError,
)
from posture_core import KeypointName, Landmark, PoseFrame

if TYPE_CHECKING:
    from collections.abc import Callable

    from pose_backends.base import ImageBGR

__all__ = [
    "MEDIAPIPE_INDEX_TO_KEYPOINT",
    "MediaPipeBackend",
    "RawDetection",
    "RawLandmark",
    "RawPoseDetector",
]

ImageRGB: TypeAlias = NDArray[np.uint8]

BACKEND_NAME: Final = "mediapipe"

# The complete MediaPipe Pose Landmarker schema, index -> canonical name. Transcribed from the
# landmark table in docs/adr/0002-mediapipe-pose.md, which also records the legacy COCO-18
# correspondence. NECK (index-less, derived below) is deliberately absent: no backend reports it.
#
# Written out in full rather than generated from the enum's declaration order. Ordering is not a
# contract of an enum, and tying a wire format to it would make an innocuous reordering of the
# members silently remap every landmark.
MEDIAPIPE_INDEX_TO_KEYPOINT: Final[Mapping[int, KeypointName]] = {
    0: KeypointName.NOSE,
    1: KeypointName.LEFT_EYE_INNER,
    2: KeypointName.LEFT_EYE,
    3: KeypointName.LEFT_EYE_OUTER,
    4: KeypointName.RIGHT_EYE_INNER,
    5: KeypointName.RIGHT_EYE,
    6: KeypointName.RIGHT_EYE_OUTER,
    7: KeypointName.LEFT_EAR,
    8: KeypointName.RIGHT_EAR,
    9: KeypointName.MOUTH_LEFT,
    10: KeypointName.MOUTH_RIGHT,
    11: KeypointName.LEFT_SHOULDER,
    12: KeypointName.RIGHT_SHOULDER,
    13: KeypointName.LEFT_ELBOW,
    14: KeypointName.RIGHT_ELBOW,
    15: KeypointName.LEFT_WRIST,
    16: KeypointName.RIGHT_WRIST,
    17: KeypointName.LEFT_PINKY,
    18: KeypointName.RIGHT_PINKY,
    19: KeypointName.LEFT_INDEX,
    20: KeypointName.RIGHT_INDEX,
    21: KeypointName.LEFT_THUMB,
    22: KeypointName.RIGHT_THUMB,
    23: KeypointName.LEFT_HIP,
    24: KeypointName.RIGHT_HIP,
    25: KeypointName.LEFT_KNEE,
    26: KeypointName.RIGHT_KNEE,
    27: KeypointName.LEFT_ANKLE,
    28: KeypointName.RIGHT_ANKLE,
    29: KeypointName.LEFT_HEEL,
    30: KeypointName.RIGHT_HEEL,
    31: KeypointName.LEFT_FOOT_INDEX,
    32: KeypointName.RIGHT_FOOT_INDEX,
}


@dataclass(frozen=True, slots=True)
class RawLandmark:
    """One point as the inference runtime reports it, before any canonical naming.

    Deliberately a plain value object rather than MediaPipe's own type, so that the stub in the
    tests can produce these without importing mediapipe. ``z`` is normalised depth in the image
    result and metres in the world result; the adapter only uses the latter.
    """

    x: float
    y: float
    z: float
    visibility: float
    presence: float


@dataclass(frozen=True, slots=True)
class RawDetection:
    """One detected person: the image-space landmarks, and the world-space ones if available."""

    landmarks: Sequence[RawLandmark]
    world_landmarks: Sequence[RawLandmark] | None = None


class RawPoseDetector(Protocol):
    """The narrow seam that keeps mediapipe out of the mapping tests."""

    def detect(self, image_rgb: ImageRGB) -> RawDetection | None:
        """Landmarks for the most prominent person, or ``None`` if there is nobody."""
        ...

    def close(self) -> None:
        """Release the native inference graph. Must be idempotent."""
        ...


class MediaPipeBackend:
    """Pose estimation via MediaPipe Pose Landmarker, pinned to ``mediapipe==0.10.18``.

    The pin is load-bearing, not incidental: 0.10.18 is the last release publishing a PyPI
    ``linux aarch64`` wheel, and bumping it kills the local Docker demo on Apple Silicon. See
    ADR-0002 for the spike that established this.
    """

    name: Final = BACKEND_NAME

    def __init__(
        self,
        model_path: Path | str,
        *,
        min_pose_detection_confidence: float = 0.5,
        detector_factory: Callable[[Path, float], RawPoseDetector] | None = None,
    ) -> None:
        """Load the model **once**, here, and keep it for the life of the instance.

        The API constructs exactly one of these in its ``lifespan`` hook (OP-40) and reuses it for
        every request. Loading per request would be unusable — which is what ``RUNDOWN.md``'s open
        items flagged about the legacy engine and never resolved.

        ``detector_factory`` is the test seam. Left at ``None`` it builds the real MediaPipe Tasks
        detector; the mapping tests pass a stub and never touch a model file.
        """
        self._model_path = Path(model_path).expanduser().resolve()
        factory = detector_factory if detector_factory is not None else _create_tasks_detector
        self._detector = factory(self._model_path, min_pose_detection_confidence)
        self._closed = False

    @property
    def model_path(self) -> Path:
        """Absolute path to the loaded weights — reported by the CLI and the health endpoint."""
        return self._model_path

    def detect(self, image_bgr: ImageBGR) -> PoseFrame | None:
        _validate_image(image_bgr)
        height, width = image_bgr.shape[:2]

        # BGR -> RGB. cv2 decodes to BGR, MediaPipe expects RGB, and getting this wrong does not
        # crash — it silently degrades detection in a way no assertion would obviously catch.
        # `ascontiguousarray` because the reversed view has a negative stride and the native
        # binding needs a contiguous buffer.
        image_rgb: ImageRGB = np.ascontiguousarray(image_bgr[:, :, ::-1])

        started = time.perf_counter()
        raw = self._detector.detect(image_rgb)
        inference_ms = (time.perf_counter() - started) * 1000.0

        if raw is None or not raw.landmarks:
            # An ordinary outcome — the user photographed their desk. Not an error.
            return None

        return PoseFrame(
            landmarks=self._to_canonical(raw),
            image_width=width,
            image_height=height,
            backend=self.name,
            inference_ms=inference_ms,
        )

    def warmup(self) -> None:
        """Run one throwaway inference at startup, on a frame nobody is waiting for.

        **Measured caveat, 2026-07-26** (Apple M5, ``pose_landmarker_full``, 1280x720): with
        ``mediapipe==0.10.18`` in ``RunningMode.IMAGE`` this saves nothing measurable. The graph is
        built eagerly inside ``create_from_options``, so construction costs ~31 ms and every
        inference afterwards costs ~23 ms — the first one included. The plan assumed a lazily-built
        graph. It is not one.

        Kept anyway, and not as dead code. The guarantee the API needs is "no request ever pays an
        initialisation cost", and that should not silently rest on an implementation detail of one
        pinned library version. One synthetic frame at startup is a trivial price for a contract
        that still holds if a future release defers work — and the ONNX escape hatch in ADR-0002
        would genuinely need it.
        """
        self.detect(np.zeros((256, 256, 3), dtype=np.uint8))

    def close(self) -> None:
        """Release the native graph. Idempotent; safe to call from a lifespan shutdown.

        The guard is here rather than left to the detector. :class:`RawPoseDetector` asks
        implementors for idempotence, but the real one wraps a vendored C++ object whose own
        ``close()`` makes no such promise — and a second call raising during shutdown would turn
        an orderly stop into a stack trace. Tracking the state costs a boolean.
        """
        if self._closed:
            return
        self._closed = True
        self._detector.close()

    # -- translation ---------------------------------------------------------------------------

    def _to_canonical(self, raw: RawDetection) -> dict[KeypointName, Landmark]:
        """MediaPipe's indexed arrays -> the canonical named skeleton."""
        world = raw.world_landmarks
        # A world array of a different length than the image array can only be a runtime change we
        # have not accounted for. Pairing them by index anyway would attach one landmark's metres
        # to another landmark's pixels — wrong answers, no error. Drop world instead.
        if world is not None and len(world) != len(raw.landmarks):
            world = None

        landmarks: dict[KeypointName, Landmark] = {}
        for index, raw_landmark in enumerate(raw.landmarks):
            name = MEDIAPIPE_INDEX_TO_KEYPOINT.get(index)
            if name is None:
                # A future model variant reporting extra points. Ignoring them is correct: the
                # canonical schema is the project's, not MediaPipe's.
                continue
            world_landmark = world[index] if world is not None else None
            landmarks[name] = Landmark(
                x=raw_landmark.x,
                y=raw_landmark.y,
                visibility=raw_landmark.visibility,
                presence=raw_landmark.presence,
                x_world=None if world_landmark is None else world_landmark.x,
                y_world=None if world_landmark is None else world_landmark.y,
                z_world=None if world_landmark is None else world_landmark.z,
            )

        neck = _derive_neck(landmarks)
        if neck is not None:
            landmarks[KeypointName.NECK] = neck
        return landmarks


def _derive_neck(landmarks: Mapping[KeypointName, Landmark]) -> Landmark | None:
    """``NECK`` as the midpoint of the two shoulders — the one real schema change (ADR-0002).

    Derived **here, in the adapter**, so every backend presents the same skeleton and no rule ever
    has to know that MediaPipe lacks a neck landmark. Note this is not an innovation: legacy COCO
    keypoint 1 was itself synthesised from the two shoulders. Making the derivation explicit is
    what exposed FINDINGS §2.2, where ``evaluate_neck_posture`` compared the shoulder midpoint's
    *y* against the shoulder midpoint's *y* — a point against itself, one possible answer.

    Confidence is the **minimum** of the two shoulders, not the mean. A derived point cannot be
    more trustworthy than the least trustworthy thing it was derived from, and averaging would let
    one confidently-seen shoulder launder an unseen one into a plausible-looking neck.
    """
    left = landmarks.get(KeypointName.LEFT_SHOULDER)
    right = landmarks.get(KeypointName.RIGHT_SHOULDER)
    if left is None or right is None:
        return None

    has_world = left.has_world and right.has_world

    def midpoint(a: float | None, b: float | None) -> float | None:
        return None if a is None or b is None else (a + b) / 2.0

    return Landmark(
        x=(left.x + right.x) / 2.0,
        y=(left.y + right.y) / 2.0,
        visibility=min(left.visibility, right.visibility),
        presence=min(left.presence, right.presence),
        x_world=midpoint(left.x_world, right.x_world) if has_world else None,
        y_world=midpoint(left.y_world, right.y_world) if has_world else None,
        z_world=midpoint(left.z_world, right.z_world) if has_world else None,
    )


def _validate_image(image: ImageBGR) -> None:
    """Reject arrays that are not images, loudly, at the call site that produced them."""
    if image.ndim != 3 or image.shape[2] != 3:
        raise InvalidImageError(
            f"expected an (H, W, 3) BGR image, got an array of shape {image.shape}"
        )
    if image.shape[0] == 0 or image.shape[1] == 0:
        raise InvalidImageError(f"image has a zero dimension: {image.shape}")


# ---------------------------------------------------------------------------------------------
# The only part of the file that knows MediaPipe exists.
# ---------------------------------------------------------------------------------------------


def _create_tasks_detector(
    model_path: Path, min_pose_detection_confidence: float
) -> RawPoseDetector:
    """Build the real detector, turning each failure mode into an actionable error.

    The import is function-local by necessity, not by style: ``mediapipe`` is an optional extra
    (ADR-0002), and a module-scope import would make ``import pose_backends`` fail for everyone
    running the fake backend — which is most of CI.
    """
    if not model_path.is_file():
        raise ModelNotFoundError(
            f"pose model not found at {model_path}. Run `make fetch-model`, or set MODEL_PATH "
            "to an existing pose_landmarker .task file."
        )

    try:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
    except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
        raise BackendUnavailableError(
            "mediapipe is not installed. It is an optional extra so that the fake backend can "
            "run without a 300 MB inference stack: install `pose-backends[mediapipe]`, or set "
            "POSE_BACKEND=fake."
        ) from exc

    try:
        options = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=min_pose_detection_confidence,
            # Segmentation masks cost time and memory and nothing in this project draws one.
            output_segmentation_masks=False,
        )
        landmarker = vision.PoseLandmarker.create_from_options(options)
    except (RuntimeError, ValueError, OSError) as exc:
        # The file exists but the runtime rejected it: truncated download, wrong variant, or a
        # corrupted checkout. The checksum pin in OP-20 is what turns most of these into a loud
        # failure at fetch time rather than a puzzling one at startup.
        raise ModelLoadError(
            f"mediapipe could not load the model at {model_path}: {exc}. Verify its SHA256 with "
            "`make fetch-model`."
        ) from exc

    return _TasksDetector(landmarker)


class _TasksDetector:
    """Thin wrapper over a MediaPipe ``PoseLandmarker``, normalising its result shape."""

    def __init__(self, landmarker: object) -> None:
        self._landmarker = landmarker

    def detect(self, image_rgb: ImageRGB) -> RawDetection | None:
        import mediapipe as mp

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        result = self._landmarker.detect(mp_image)  # type: ignore[attr-defined]

        # MediaPipe returns lists-of-lists, one inner list per detected person. `num_poses=1`
        # means at most one, and an empty outer list means nobody was found.
        if not result.pose_landmarks:
            return None

        world = result.pose_world_landmarks[0] if result.pose_world_landmarks else None
        return RawDetection(
            landmarks=[_to_raw(lm) for lm in result.pose_landmarks[0]],
            world_landmarks=None if world is None else [_to_raw(lm) for lm in world],
        )

    def close(self) -> None:
        close = getattr(self._landmarker, "close", None)
        if callable(close):
            close()


def _to_raw(landmark: object) -> RawLandmark:
    """MediaPipe landmark -> :class:`RawLandmark`.

    ``visibility`` and ``presence`` are read defensively because MediaPipe leaves them unset on
    *world* landmarks. Defaulting to 0.0 there is right: the canonical landmark takes its
    confidence from the image-space point, which does carry both.
    """
    return RawLandmark(
        x=float(landmark.x),  # type: ignore[attr-defined]
        y=float(landmark.y),  # type: ignore[attr-defined]
        z=float(landmark.z),  # type: ignore[attr-defined]
        visibility=float(getattr(landmark, "visibility", 0.0) or 0.0),
        presence=float(getattr(landmark, "presence", 0.0) or 0.0),
    )
