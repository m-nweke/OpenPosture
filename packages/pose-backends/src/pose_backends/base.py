"""The seam between inference and everything else.

One Protocol, three members. That narrowness is the asset: it is what lets a real model, a
deterministic fake, and — if the platform bet in ADR-0002 ever goes bad — an entirely different
inference runtime be swapped for one another without a single line changing in ``posture_core``
or ``apps/api``.

A ``Protocol`` rather than an ABC because conformance is then structural. Nothing has to import
this class or inherit from it to satisfy it, so a test double can be a five-line class defined in
the test that needs it, and mypy still checks it against the real contract. Inheriting from an
ABC would make the fake depend on the package it exists to avoid.
"""

from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from posture_core import PoseFrame

__all__ = ["ImageBGR", "PoseBackend"]

ImageBGR: TypeAlias = NDArray[np.uint8]
"""An ``(H, W, 3)`` uint8 array in **BGR** channel order.

BGR, not RGB, because that is what ``cv2.imread`` returns and OpenCV is what decodes uploads on
the API path. Naming the convention in the type is cheap insurance: a silent channel swap does
not crash, it just quietly degrades detection accuracy in a way no test would obviously catch.
Adapters that need RGB convert internally — that is adapter business, not the caller's.
"""


@runtime_checkable
class PoseBackend(Protocol):
    """What every pose estimator must offer, and nothing more."""

    @property
    def name(self) -> str:
        """Short stable identifier, stamped onto every :class:`~posture_core.PoseFrame`.

        Declared as a read-only property rather than a bare ``name: str`` attribute so that both
        forms conform: an implementation may expose a plain class attribute or a computed
        property. A mutable attribute in the Protocol would reject the property form.
        """
        ...

    def detect(self, image_bgr: ImageBGR) -> PoseFrame | None:
        """Find one person and return their skeleton, or ``None`` if there is nobody there.

        **``None`` is the no-pose answer, not an exception.** An image with no person in it is an
        ordinary outcome of a posture app — the user photographed their desk — and raising for it
        would push every caller into a ``try/except`` around the happy path. Exceptions are
        reserved for the backend being *broken*: a missing model file, an unreadable frame.

        That distinction is what stops ``except Exception`` from reappearing. The legacy engine
        swallowed failures and returned ``None``, and the caller rendered ``None`` as "Straight
        back position" (FINDINGS §2.5) — so "no person" and "inference exploded" were reported to
        the user identically, and both were reported as good posture.
        """
        ...

    def warmup(self) -> None:
        """Run one throwaway inference so the first real request does not pay for initialisation.

        Called once at API startup (OP-40), where the cost is invisible. ``RUNDOWN.md``'s open
        items flagged exactly this on the legacy engine — "a cold load is slow, per-request
        loading would be unusable" — and never resolved it, because the model lived in a module
        global built under a ``__main__`` guard and there was no startup hook to move it to.

        Must be idempotent and must never raise on a healthy backend.
        """
        ...
