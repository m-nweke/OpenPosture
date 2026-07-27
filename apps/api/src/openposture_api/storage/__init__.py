"""Object storage behind one Protocol, with configuration choosing the implementation.

    from openposture_api.storage import create_storage

    storage = create_storage(settings)
    stored = storage.put(image_bytes, content_type="image/jpeg")
    ...
    url = storage.url_for(stored.key)

`put` returns a key. Persist the key; build the URL when you need one. See
:mod:`openposture_api.storage.base` for why that separation is load-bearing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

from openposture_api.storage.base import (
    CONTENT_TYPE_SUFFIXES,
    DEFAULT_PREFIX,
    StorageBackend,
    StoredObject,
    generate_key,
    validate_key,
)
from openposture_api.storage.errors import (
    InvalidObjectKeyError,
    ObjectNotFoundError,
    StorageError,
    UnsupportedContentTypeError,
)
from openposture_api.storage.local import BACKEND_NAME as LOCAL_BACKEND
from openposture_api.storage.local import LocalDiskStorage
from openposture_api.storage.s3 import BACKEND_NAME as S3_BACKEND
from openposture_api.storage.s3 import S3Storage

if TYPE_CHECKING:
    from openposture_api.config import Settings

__all__ = [
    "CONTENT_TYPE_SUFFIXES",
    "DEFAULT_PREFIX",
    "LOCAL_BACKEND",
    "S3_BACKEND",
    "InvalidObjectKeyError",
    "LocalDiskStorage",
    "ObjectNotFoundError",
    "S3Storage",
    "StorageBackend",
    "StorageError",
    "StoredObject",
    "UnsupportedContentTypeError",
    "create_storage",
    "generate_key",
    "get_storage",
    "validate_key",
]


def create_storage(settings: Settings) -> StorageBackend:
    """Build the configured storage backend.

    Kept next to the implementations rather than in the app factory, so adding a third backend is
    one entry here and no change anywhere above.
    """
    if settings.storage_backend == S3_BACKEND:
        return S3Storage(
            settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        )
    return LocalDiskStorage(settings.storage_root, base_url=settings.media_base_url)


def get_storage(request: Request) -> StorageBackend:
    """FastAPI dependency: the process's storage backend.

    Built in `lifespan` and held on `app.state`, for the same reasons as the pose backend — no
    module global, one instance, and `app.dependency_overrides[get_storage]` lets an endpoint
    test run against a temporary directory.

    Consuming it needs `StorageBackend` imported at *runtime* in the route's module, not under
    `TYPE_CHECKING`: every module here uses `from __future__ import annotations`, and a name
    FastAPI cannot resolve is silently treated as a query parameter rather than a dependency.
    """
    storage: StorageBackend | None = getattr(request.app.state, "storage", None)
    if storage is None:
        # Only reachable if a route runs without lifespan having executed, which in practice
        # means a test built an app and never entered the TestClient context manager.
        raise StorageError(
            "storage is not configured on this app: lifespan has not run. In a test, enter the "
            "TestClient context manager, or override this dependency."
        )
    return storage
