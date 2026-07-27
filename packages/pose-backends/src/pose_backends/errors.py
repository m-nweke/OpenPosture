"""What "the backend is broken" looks like, as opposed to "there is nobody in the photo".

That distinction is the whole reason this module exists. ``detect()`` returns ``None`` for the
ordinary case of an image with no person in it; it raises one of these when the backend itself
cannot do its job. Collapsing the two is precisely the legacy engine's most damaging behaviour —
a swallowed exception and an empty desk both became ``None``, and the caller rendered ``None`` as
"Straight back position" (FINDINGS §2.5).

Every exception here carries a message a human can act on. "Model file not found at
/app/models/x.task — run `make fetch-model`" is a bug report someone can fix; an
``AttributeError`` from four frames inside a vendored C++ binding is not.
"""

from __future__ import annotations

__all__ = [
    "BackendUnavailableError",
    "InvalidImageError",
    "ModelLoadError",
    "ModelNotFoundError",
    "PoseBackendError",
]


class PoseBackendError(RuntimeError):
    """Base for every backend failure.

    One base class so callers that genuinely want to treat all backend breakage alike — the API's
    problem+json handler (OP-39) — can catch this and nothing else. That is the alternative to
    ``except Exception``, which the project bans outright: a narrow catch of a type we defined
    still lets a genuine programming error propagate.
    """


class BackendUnavailableError(PoseBackendError):
    """The inference library is not installed.

    Its own error rather than letting ``ImportError`` escape, because the fix is specific and
    non-obvious: ``mediapipe`` is an *optional* extra (ADR-0002), so that ``pose-backends`` can be
    installed — and ``FakePoseBackend`` used — without a 300 MB inference stack. Someone hitting
    this has almost certainly installed the base package and expected the real backend.
    """


class ModelNotFoundError(PoseBackendError):
    """No model file at the configured path.

    Distinct from :class:`ModelLoadError` because the remedies differ: this one means fetch the
    weights, that one means the weights you fetched are wrong.
    """


class ModelLoadError(PoseBackendError):
    """A file is there, but the inference runtime would not accept it.

    Truncated download, wrong model variant, corrupted checkout. The checksum pin in OP-20 exists
    to turn most of these into a loud failure at fetch time instead of a puzzling one at startup.
    """


class InvalidImageError(PoseBackendError):
    """The array handed to ``detect()`` is not an image this backend can work with.

    A programming error at the call site rather than a bad photo — an empty array, a greyscale
    frame, a channel count that is not 3. Raising is right here: unlike "no person detected", there
    is no sensible result to return and no user-facing story to tell.
    """
