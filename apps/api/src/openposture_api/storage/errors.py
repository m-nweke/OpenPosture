"""What storage failures look like, kept distinct from "the object is not there".

The same split the pose backends make between "nobody in the photo" and "the backend is broken":
a missing object is an ordinary outcome a caller routinely handles, and everything else is a
fault. Collapsing the two is how a deleted file and an unreachable bucket end up producing the
same 404 to a user and the same silence in the logs.
"""

from __future__ import annotations

__all__ = [
    "InvalidObjectKeyError",
    "ObjectNotFoundError",
    "StorageError",
    "UnsupportedContentTypeError",
]


class StorageError(RuntimeError):
    """Base for every storage failure."""


class ObjectNotFoundError(StorageError):
    """No object at that key.

    Ordinary: a record outlived its object, or a key was already deleted. Callers are expected to
    catch this one specifically.
    """


class InvalidObjectKeyError(StorageError):
    """A key that this layer refuses to touch.

    Raised for traversal sequences, absolute paths, and anything else that could escape the
    configured root. The original `/upload` handed `file.filename` straight to the bucket
    (FINDINGS §5.1), which is exactly the input this rejects.
    """


class UnsupportedContentTypeError(StorageError):
    """A media type outside the allowlist.

    An allowlist rather than a blocklist, because the set of things that are safe to accept is
    small and knowable while the set of things that are not is neither.
    """
