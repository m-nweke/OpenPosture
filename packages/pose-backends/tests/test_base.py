"""Conformance tests for the `PoseBackend` Protocol.

The Protocol is checked two ways, and both are needed because they catch different things:

* **statically** — the `_assert_conforms` helper below assigns an implementation to a
  `PoseBackend`-typed variable, so mypy --strict verifies signatures, argument types and return
  types. This is the check that catches a `detect` returning `PoseFrame` instead of
  `PoseFrame | None`.
* **at runtime** — `isinstance`, which only sees whether the members exist. Weak on its own, but
  it is what proves `@runtime_checkable` is actually usable for the config-driven backend
  selection in OP-19.

A missing method is caught by both. A wrong *signature* is caught only by mypy, which is why the
static half is not redundant.
"""

from __future__ import annotations

import numpy as np

from pose_backends import ImageBGR, PoseBackend
from posture_core import KeypointName, Landmark, PoseFrame


class MinimalBackend:
    """The smallest thing that satisfies the Protocol.

    Note what it does *not* do: import `PoseBackend`, inherit from it, or register with it. That
    is the whole argument for a Protocol over an ABC — a test double is a local class, and mypy
    still checks it against the real contract.
    """

    name = "minimal"

    def detect(self, image_bgr: ImageBGR) -> PoseFrame | None:
        height, width = image_bgr.shape[:2]
        return PoseFrame(
            landmarks={KeypointName.NOSE: Landmark(x=0.5, y=0.5, visibility=1.0, presence=1.0)},
            image_width=width,
            image_height=height,
            backend=self.name,
            inference_ms=0.0,
        )

    def warmup(self) -> None:
        return None


class ComputedNameBackend:
    """`name` as a property, not an attribute — the reason the Protocol declares it as one.

    Had the Protocol said `name: str`, mypy would reject this class: a read-only property does not
    satisfy a mutable attribute. Both forms are legitimate, so the Protocol accommodates both.
    """

    @property
    def name(self) -> str:
        return "computed"

    def detect(self, image_bgr: ImageBGR) -> PoseFrame | None:
        return None

    def warmup(self) -> None:
        return None


class MissingWarmup:
    name = "incomplete"

    def detect(self, image_bgr: ImageBGR) -> PoseFrame | None:
        return None


def _assert_conforms(backend: PoseBackend) -> str:
    """Static conformance: mypy --strict checks the argument at every call site below."""
    return backend.name


def test_minimal_implementation_conforms_statically_and_at_runtime() -> None:
    backend = MinimalBackend()
    assert _assert_conforms(backend) == "minimal"
    assert isinstance(backend, PoseBackend)


def test_name_may_be_a_property() -> None:
    backend = ComputedNameBackend()
    assert _assert_conforms(backend) == "computed"
    assert isinstance(backend, PoseBackend)


def test_incomplete_implementation_is_rejected_at_runtime() -> None:
    """A backend missing `warmup` must not pass as one — OP-40 calls it unconditionally."""
    assert not isinstance(MissingWarmup(), PoseBackend)


def test_detect_returns_a_frame_stamped_with_the_backend_and_image_size() -> None:
    """Provenance travels with the data rather than being logged beside it."""
    image: ImageBGR = np.zeros((480, 640, 3), dtype=np.uint8)
    frame = MinimalBackend().detect(image)
    assert frame is not None
    assert frame.backend == "minimal"
    assert (frame.image_width, frame.image_height) == (640, 480)


def test_none_is_a_legitimate_detect_result() -> None:
    """No person in the photo is an ordinary outcome, not an error.

    This is the single most important line of the contract. The legacy engine returned `None` for
    *both* "nobody there" and "inference failed", and the caller rendered `None` as "Straight back
    position" (FINDINGS §2.5) — so a crash and a photo of an empty desk both told the user their
    posture was fine. Here `None` means exactly one thing, and breakage raises.
    """
    image: ImageBGR = np.zeros((16, 16, 3), dtype=np.uint8)
    assert ComputedNameBackend().detect(image) is None
