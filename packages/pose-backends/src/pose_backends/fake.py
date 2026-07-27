"""The deterministic backend. Load-bearing infrastructure, not a throwaway test double.

With ``POSE_BACKEND=fake`` the entire application runs with no model weights, no 300 MB inference
stack and no secrets. That is what lets the container smoke test (OP-43) and the Playwright
end-to-end suite (OP-47) run on every pull request in seconds. It is the single reason required
CI stays fast and secret-free, which is why this file gets the same care as the real adapter.

Every preset is built by :mod:`posture_core.synthetic` — the *same* builder the rules-engine tests
use. That is deliberate and it is the point of the module living in ``posture_core``: if the fake
had its own private figure construction, it would drift from the one the metrics are tested
against, and the day it drifted every end-to-end assertion resting on a fake pose would quietly
stop meaning anything.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from posture_core import PoseFrame
from posture_core.synthetic import Facing, View, make_pose_frame

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pose_backends.base import ImageBGR

__all__ = ["FakePoseBackend", "PosePreset"]

BACKEND_NAME: Final = "fake"


class PosePreset(StrEnum):
    """Named situations the rest of the system needs to be able to reproduce on demand."""

    STRAIGHT = "straight"
    HUNCHBACK = "hunchback"
    RECLINED = "reclined"
    KNEELING = "kneeling"
    PARTIAL_OCCLUSION = "partial_occlusion"
    FRONTAL_VIEW = "frontal_view"
    NO_PERSON = "no_person"
    """``detect()`` returns ``None``. The empty-desk path, exercisable without an empty desk."""


# Joint angles per preset, in the convention `posture_core.synthetic` documents: trunk and neck
# from straight up, limbs from straight down, positive forward.
#
# The seated poses share a thigh at 85° (near horizontal) and a shank at 5° (near vertical), which
# is a chair. What distinguishes them is the trunk and neck.
_SEATED: Final[Mapping[str, float]] = {"thigh_deg": 85.0, "shank_deg": 5.0}

_PRESETS: Final[Mapping[PosePreset, dict[str, object]]] = {
    # Upright and neutral: the reference every other preset is a departure from.
    PosePreset.STRAIGHT: {
        **_SEATED,
        "trunk_deg": 3.0,
        "neck_deg": 5.0,
        "upper_arm_deg": 10.0,
        "forearm_deg": 75.0,
    },
    # Slumped forward with the head thrust further forward still — the posture the whole
    # application exists to detect, and the one the legacy engine reported as "Straight back"
    # whenever its assessment failed (FINDINGS §2.5).
    PosePreset.HUNCHBACK: {
        **_SEATED,
        "trunk_deg": 32.0,
        "neck_deg": 30.0,
        "upper_arm_deg": 15.0,
        "forearm_deg": 80.0,
    },
    # Leaning back. Negative trunk angle, which is what makes this a useful test of a *signed*
    # trunk inclination: an implementation taking an absolute value would report this as identical
    # to a forward slump of the same magnitude.
    PosePreset.RECLINED: {
        **_SEATED,
        "trunk_deg": -25.0,
        "neck_deg": 10.0,
        "upper_arm_deg": -5.0,
        "forearm_deg": 60.0,
    },
    # Kneeling: thigh near vertical, shank folded back underneath. Knee flexion is emergent
    # (180 - |thigh - shank|), so this figure is physically consistent by construction.
    PosePreset.KNEELING: {
        "thigh_deg": 15.0,
        "shank_deg": 165.0,
        "trunk_deg": 5.0,
        "neck_deg": 6.0,
        "upper_arm_deg": 8.0,
        "forearm_deg": 20.0,
    },
    # Same seating as STRAIGHT, but the lower body is gone and one arm is behind the torso. The
    # two are different kinds of missing and the frame says so: legs are *omitted* (never
    # reported), the far arm is present with low visibility and high presence (occluded).
    #
    # This preset is how the "couldn't assess your knees, try a wider shot" path gets tested.
    PosePreset.PARTIAL_OCCLUSION: {
        **_SEATED,
        "trunk_deg": 10.0,
        "neck_deg": 12.0,
        "upper_arm_deg": 12.0,
        "forearm_deg": 78.0,
    },
    # Facing the camera. The angles are a pronounced slump, but a frontal projection hides it —
    # which is the point. `view_confidence` (OP-31) must refuse to assess this rather than
    # reporting the near-zero apparent lean as good posture. The original app asked users for a
    # side-on photo and never checked that it got one.
    PosePreset.FRONTAL_VIEW: {
        **_SEATED,
        "trunk_deg": 30.0,
        "neck_deg": 25.0,
        "upper_arm_deg": 10.0,
        "forearm_deg": 75.0,
        "view": View.FRONTAL,
    },
}


def _build(preset: PosePreset, image_width: int, image_height: int) -> PoseFrame | None:
    """One preset -> one frame. Pure, so repeated calls are indistinguishable."""
    if preset is PosePreset.NO_PERSON:
        return None

    kwargs: dict[str, object] = {
        "facing": Facing.RIGHT,
        **_PRESETS[preset],
        "image_width": image_width,
        "image_height": image_height,
        "backend": BACKEND_NAME,
    }

    if preset is PosePreset.PARTIAL_OCCLUSION:
        from posture_core import KeypointName

        kwargs["omit"] = (
            KeypointName.LEFT_KNEE,
            KeypointName.RIGHT_KNEE,
            KeypointName.LEFT_ANKLE,
            KeypointName.RIGHT_ANKLE,
            KeypointName.LEFT_HEEL,
            KeypointName.RIGHT_HEEL,
            KeypointName.LEFT_FOOT_INDEX,
            KeypointName.RIGHT_FOOT_INDEX,
        )
        # Occluded, not out of frame: visibility collapses, presence does not. Expressing the
        # difference is the whole reason the model was chosen (ADR-0002).
        kwargs["confidence"] = {
            KeypointName.LEFT_ELBOW: (0.18, 0.92),
            KeypointName.LEFT_WRIST: (0.12, 0.90),
        }

    return make_pose_frame(**kwargs)  # type: ignore[arg-type]


class FakePoseBackend:
    """A ``PoseBackend`` that returns a canned skeleton and never looks at the image.

    Sub-millisecond because there is nothing to decode and nothing to infer. Byte-identical across
    runs because the figure is analytic and ``inference_ms`` is a constant ``0.0`` rather than a
    measurement — a real timing would make otherwise-identical frames differ between runs and
    break every snapshot assertion downstream.
    """

    name: Final = BACKEND_NAME

    def __init__(
        self,
        preset: PosePreset | str = PosePreset.STRAIGHT,
        *,
        image_width: int = 640,
        image_height: int = 480,
    ) -> None:
        self._preset = PosePreset(preset)
        self._image_width = image_width
        self._image_height = image_height
        # Built once, then shared. Safe precisely because PoseFrame is genuinely immutable — its
        # landmark mapping is a MappingProxyType, not a dict a caller could edit and thereby
        # corrupt every later detection.
        self._frame = _build(self._preset, image_width, image_height)

    @property
    def preset(self) -> PosePreset:
        return self._preset

    def detect(self, image_bgr: ImageBGR) -> PoseFrame | None:
        """Return the preset's frame, ignoring the image entirely.

        Ignoring it is the contract, not a shortcut: callers select the *scenario* by constructing
        the backend, and a fake that inspected pixels would make an end-to-end test's result
        depend on whatever placeholder JPEG someone happened to upload.
        """
        del image_bgr
        return self._frame

    def warmup(self) -> None:
        """Nothing to warm. Present because the Protocol requires it and OP-40 calls it blind."""

    def close(self) -> None:
        """Symmetry with the real backend, so lifespan shutdown needs no special case."""
