"""Choosing a backend at runtime, from configuration rather than from an import.

``POSE_BACKEND=fake`` has to be a *deployment* switch, not a test-only injection point. The
container smoke test starts the real API image and the Playwright suite drives the real frontend;
neither can reach into a Python fixture to swap an object. They set an environment variable, and
this module is what reads it.

The API layer will wrap this in pydantic-settings (OP-39) so the value is validated alongside
everything else it configures. This function stays the single place that knows how a name maps to
a class, so adding a backend means adding one entry here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from pose_backends.base import PoseBackend
from pose_backends.errors import PoseBackendError
from pose_backends.fake import BACKEND_NAME as FAKE_NAME
from pose_backends.fake import FakePoseBackend, PosePreset
from pose_backends.mediapipe_backend import BACKEND_NAME as MEDIAPIPE_NAME
from pose_backends.mediapipe_backend import MediaPipeBackend

__all__ = ["DEFAULT_MODEL_PATH", "create_backend", "default_model_path"]

DEFAULT_MODEL_PATH: Final = Path("models/pose_landmarker_full.task")
"""Where ``make fetch-model`` puts the weights.

**Relative, and therefore resolved against the working directory** — which is the repository root
locally and the image's ``WORKDIR`` in a container. That is a deliberate default rather than a
repeat of the legacy engine's cwd dependence: there, relative paths to weights and config were
buried inside the inference code, so the thing only ran from within ``API/`` and there was no way
to say otherwise. Here it is one documented default with :envvar:`MODEL_PATH` as the override, and
``MediaPipeBackend`` resolves whatever it is given to an absolute path before loading.
"""


def default_model_path() -> Path:
    """The model location, with :envvar:`MODEL_PATH` taking precedence.

    The override exists so a variant can be swapped in without rebuilding: lite at 5.5 MB, full at
    9 MB, heavy at 29 MB. Nothing in the code depends on which one loads — all three emit the same
    33 landmarks with world coordinates — so this is a configuration change, not a code change.
    See ``models/checksums.txt`` for the pins and the tradeoffs.

    It also replaces the legacy arrangement's worst property: weights fetched from a bare Dropbox
    link recorded in a readme, with no checksum and no stated origin, so a dead link made the whole
    project unrunnable and a corrupted copy was indistinguishable from a good one.
    """
    # Stripped, and an empty result treated as unset. A stray space in a Compose env file or a CI
    # variable would otherwise produce `Path("   ")` and a "model not found" error naming a path
    # that is invisible in the message.
    override = (os.environ.get("MODEL_PATH") or "").strip()
    return Path(override) if override else DEFAULT_MODEL_PATH


def create_backend(
    name: str | None = None,
    *,
    model_path: Path | str | None = None,
    preset: PosePreset | str | None = None,
) -> PoseBackend:
    """Build the configured backend.

    :param name: ``"mediapipe"`` or ``"fake"``. Defaults to ``POSE_BACKEND``, then ``"mediapipe"``
        — the real backend is the default so that a missing variable in production yields real
        inference rather than a fabricated skeleton nobody notices is fabricated.
    :param preset: fake-backend scenario; defaults to ``POSE_BACKEND_PRESET``, then ``straight``.
    :raises PoseBackendError: for an unknown name, listing what is available. An unrecognised
        value must fail loudly at startup: silently falling back to the fake would mean shipping
        an application that invents its results.
    """
    resolved = (name or os.environ.get("POSE_BACKEND") or MEDIAPIPE_NAME).strip().lower()

    if resolved == FAKE_NAME:
        chosen = preset if preset is not None else os.environ.get("POSE_BACKEND_PRESET")
        return FakePoseBackend(chosen if chosen is not None else PosePreset.STRAIGHT)

    if resolved == MEDIAPIPE_NAME:
        return MediaPipeBackend(model_path if model_path is not None else default_model_path())

    raise PoseBackendError(
        f"unknown POSE_BACKEND {resolved!r}. Available: {MEDIAPIPE_NAME}, {FAKE_NAME}."
    )
