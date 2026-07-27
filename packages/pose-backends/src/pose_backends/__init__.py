"""Pose inference adapters.

This package quarantines the one heavy, platform-fragile dependency in the project behind a
Protocol, so that swapping inference engines touches nothing in ``posture_core`` or ``apps/api``.

Backends arrive in Epic B:

* ``MediaPipeBackend``  — the real one (OP-18)
* ``FakePoseBackend``   — deterministic, sub-millisecond, no model (OP-19)

The canonical types (``KeypointName``, ``Landmark``, ``PoseFrame``) deliberately live in
``posture_core``, not here. The rules engine defines the contract and adapters conform to it;
that inversion is what keeps the core installable and testable with no inference stack present.
"""

from __future__ import annotations

from pose_backends.base import ImageBGR, PoseBackend
from pose_backends.errors import (
    BackendUnavailableError,
    InvalidImageError,
    ModelLoadError,
    ModelNotFoundError,
    PoseBackendError,
)
from pose_backends.fake import FakePoseBackend, PosePreset
from pose_backends.mediapipe_backend import MediaPipeBackend
from pose_backends.registry import create_backend, default_model_path

__all__ = [
    "BackendUnavailableError",
    "FakePoseBackend",
    "ImageBGR",
    "InvalidImageError",
    "MediaPipeBackend",
    "ModelLoadError",
    "ModelNotFoundError",
    "PoseBackend",
    "PoseBackendError",
    "PosePreset",
    "__version__",
    "create_backend",
    "default_model_path",
]

__version__ = "0.1.0"
