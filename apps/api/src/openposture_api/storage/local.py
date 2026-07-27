"""Storage on the filesystem: the path that needs nothing installed and nothing running.

This is what `docker compose up` uses if MinIO is cut (V2-PLAN's cut line #2 says exactly that —
"MinIO → LocalDiskStorage on a named volume; the Protocol already exists"), and it is what the
test suite uses, because a suite that needs a running object store is a suite people skip.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Final

from openposture_api.storage.base import (
    DEFAULT_PREFIX,
    StoredObject,
    generate_key,
    validate_key,
)
from openposture_api.storage.errors import ObjectNotFoundError, StorageError

__all__ = ["BACKEND_NAME", "LocalDiskStorage"]

BACKEND_NAME: Final = "local"


class LocalDiskStorage:
    """Objects as files under one root directory.

    The root is resolved once, at construction, and every path built afterwards is checked to be
    inside it. `validate_key` already rejects traversal, so this is the second of two independent
    barriers — deliberately, because the cost of the first one being wrong is arbitrary file
    read and write.
    """

    name: Final = BACKEND_NAME

    def __init__(self, root: Path | str, *, base_url: str = "/media") -> None:
        """
        :param root: Directory to store objects under. Created if absent.
        :param base_url: Prefix for :meth:`url_for`. Relative by default so it works behind the
            Vite proxy in OP-43 without knowing the deployment's hostname.
        """
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url.rstrip("/")

    @property
    def root(self) -> Path:
        return self._root

    def _path_for(self, key: str) -> Path:
        """Resolve a validated key to an absolute path inside the root, or refuse.

        The `relative_to` check is not redundant with `validate_key`. Symlinks are resolved by
        `resolve()`, so a key pointing at a path that is *textually* inside the root but resolves
        outside it — through a symlinked subdirectory an operator created — is caught here and
        nowhere else.
        """
        validate_key(key)
        candidate = (self._root / key).resolve()
        if not candidate.is_relative_to(self._root):
            raise StorageError(
                f"resolved path for key {key!r} escapes the storage root. This usually means a "
                "symlink inside the root points elsewhere."
            )
        return candidate

    def put(self, data: bytes, *, content_type: str, prefix: str = DEFAULT_PREFIX) -> StoredObject:
        """Write bytes under a generated key.

        Written to a temporary file in the destination directory and then renamed. `replace`
        is atomic within a filesystem, so a reader never sees a partially written object and a
        crash mid-write leaves a stray temp file rather than a truncated one that looks valid.
        """
        key = generate_key(content_type, prefix=prefix)
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        descriptor, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".partial")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            Path(temp_name).replace(path)
        except OSError as exc:
            Path(temp_name).unlink(missing_ok=True)
            raise StorageError(f"could not write object {key!r}: {exc}") from exc

        return StoredObject(key=key, size=len(data), content_type=content_type)

    def get(self, key: str) -> bytes:
        path = self._path_for(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(f"no object stored at {key!r}") from exc
        except OSError as exc:
            raise StorageError(f"could not read object {key!r}: {exc}") from exc

    def delete(self, key: str) -> None:
        """Remove the object if it is there. Idempotent, matching S3's `DeleteObject`."""
        path = self._path_for(key)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"could not delete object {key!r}: {exc}") from exc

    def exists(self, key: str) -> bool:
        return self._path_for(key).is_file()

    def url_for(self, key: str, *, expires_in: int | None = None) -> str:
        """Build a URL under the configured base path.

        **`expires_in` is accepted and ignored, and that is a real difference in guarantee.** A
        filesystem has nothing to sign with, so this URL does not expire and does not authorise
        anything — whatever serves the directory decides who may read it. That is fine for local
        development and for a single-user deployment; it is not fine as a way to hand a
        third party temporary access, and S3Storage is the backend that can actually do that.
        """
        del expires_in
        validate_key(key)
        return f"{self._base_url}/{key}"
