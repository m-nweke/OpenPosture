"""Backend selection from configuration.

`POSE_BACKEND=fake` has to work as a deployment switch and not only as a test injection point:
the container smoke test starts the real image and Playwright drives the real frontend, and
neither can reach into a Python fixture to swap an object.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pose_backends import (
    FakePoseBackend,
    ModelNotFoundError,
    PoseBackendError,
    PosePreset,
    create_backend,
    default_model_path,
)
from pose_backends.registry import DEFAULT_MODEL_PATH


def test_explicit_name_selects_the_fake_backend() -> None:
    backend = create_backend("fake")
    assert isinstance(backend, FakePoseBackend)
    assert backend.preset is PosePreset.STRAIGHT


def test_environment_selects_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSE_BACKEND", "fake")
    assert isinstance(create_backend(), FakePoseBackend)


def test_environment_selects_the_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSE_BACKEND", "fake")
    monkeypatch.setenv("POSE_BACKEND_PRESET", "hunchback")
    backend = create_backend()
    assert isinstance(backend, FakePoseBackend)
    assert backend.preset is PosePreset.HUNCHBACK


@pytest.mark.parametrize("value", ["FAKE", " fake ", "Fake"])
def test_backend_name_is_case_and_whitespace_tolerant(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray newline in a Compose env file should not silently start real inference."""
    monkeypatch.setenv("POSE_BACKEND", value)
    assert isinstance(create_backend(), FakePoseBackend)


def test_the_real_backend_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing variable must yield real inference, never a fabricated skeleton.

    Defaulting to the fake would mean a deployment that forgot to set the variable would serve
    invented posture assessments and look perfectly healthy doing it. Asserted here through the
    model-not-found error, because the real backend is what tries to load weights.
    """
    monkeypatch.delenv("POSE_BACKEND", raising=False)
    with pytest.raises(ModelNotFoundError):
        create_backend(model_path="/nonexistent/model.task")


def test_unknown_backend_fails_loudly_and_lists_the_alternatives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silently falling back would ship an application that invents its results."""
    monkeypatch.setenv("POSE_BACKEND", "movenet")
    with pytest.raises(PoseBackendError, match="mediapipe, fake"):
        create_backend()


def test_model_path_defaults_to_the_fetch_model_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_PATH", raising=False)
    assert default_model_path() == DEFAULT_MODEL_PATH


def test_model_path_environment_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """So a lite or heavy model variant can be swapped in without rebuilding the image (OP-20)."""
    monkeypatch.setenv("MODEL_PATH", "/models/pose_landmarker_heavy.task")
    assert default_model_path() == Path("/models/pose_landmarker_heavy.task")


def test_a_whitespace_model_path_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stray space in a Compose env file must not become a path.

    Unstripped, this yields `Path("   ")` and then a "model not found" error naming a path that
    is invisible in the message — which is a genuinely hard thing to diagnose from a container log.
    """
    monkeypatch.setenv("MODEL_PATH", "   ")
    assert default_model_path() == DEFAULT_MODEL_PATH


def test_an_empty_model_path_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_PATH", "")
    assert default_model_path() == DEFAULT_MODEL_PATH


def test_a_model_path_with_surrounding_whitespace_is_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_PATH", "  /models/pose_landmarker_lite.task  ")
    assert default_model_path() == Path("/models/pose_landmarker_lite.task")
