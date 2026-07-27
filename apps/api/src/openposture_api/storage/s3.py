"""Storage in an S3-compatible bucket: MinIO locally, anything S3-shaped in production.

Written against the S3 API rather than against MinIO's own client so that the same code runs
against MinIO in Compose and against real S3 or R2 without a branch. `endpoint_url` is the only
thing that differs, and it is configuration.

Errors are translated at this boundary. `botocore.ClientError` carries the interesting part in a
nested dict under a string key; letting that leak upward would mean every caller learns botocore's
error shape, and swapping the backend would change what callers have to catch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from openposture_api.storage.base import (
    DEFAULT_PREFIX,
    StoredObject,
    generate_key,
    validate_key,
)
from openposture_api.storage.errors import ObjectNotFoundError, StorageError

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["BACKEND_NAME", "DEFAULT_URL_EXPIRY_SECONDS", "S3Storage"]

BACKEND_NAME: Final = "s3"

DEFAULT_URL_EXPIRY_SECONDS: Final = 900
"""Fifteen minutes: long enough to load a page and re-render it, short enough that a URL leaked
into a log or a referrer header is not a lasting grant."""

_NOT_FOUND_CODES: Final = frozenset({"NoSuchKey", "404", "NotFound"})
"""What "this object is not there" looks like across implementations.

`GetObject` says `NoSuchKey`, `HeadObject` says `404` with no name because a HEAD response has no
body to put one in, and MinIO is not always identical to AWS. Matching on a set beats matching on
one string and discovering the difference in production.

**`NoSuchBucket` is deliberately absent.** A missing bucket is a deployment fault — wrong name,
wrong endpoint, never created — and reporting it as ordinary absence would turn a broken
deployment into a plausible-looking 404 on every object. The failure would then look like data
loss rather than misconfiguration, which is the more expensive of the two to diagnose.
"""


class S3Storage:
    """Objects in a bucket, addressed by key.

    The client is built once and reused: boto3 clients are expensive to create and cheap to
    share, and building one per request would repeat the pose backend's original mistake in a
    different layer.
    """

    name: Final = BACKEND_NAME

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region_name: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        url_expiry_seconds: int = DEFAULT_URL_EXPIRY_SECONDS,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        """
        :param endpoint_url: MinIO's address in development; `None` for real AWS.
        :param client_factory: Test seam, matching `MediaPipeBackend.detector_factory`. Left at
            `None` it builds a real boto3 client.
        """
        self._bucket = bucket
        self._url_expiry_seconds = url_expiry_seconds

        if client_factory is not None:
            self._client = client_factory()
        else:
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=Config(
                    # MinIO serves buckets as paths (`host/bucket/key`), not as subdomains.
                    # Virtual-host addressing against a bare hostname or an IP simply does not
                    # resolve, and the symptom is a connection error that looks like MinIO is
                    # down rather than like a client misconfiguration.
                    s3={"addressing_style": "path"},
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )

    @property
    def bucket(self) -> str:
        return self._bucket

    def put(self, data: bytes, *, content_type: str, prefix: str = DEFAULT_PREFIX) -> StoredObject:
        key = generate_key(content_type, prefix=prefix)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(
                f"could not write object {key!r} to {self._bucket!r}: {exc}"
            ) from exc

        return StoredObject(key=key, size=len(data), content_type=content_type)

    def get(self, key: str) -> bytes:
        validate_key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            # `Body` is a `StreamingBody` wrapping a live HTTP response. Reading it does not
            # release the underlying connection back to urllib3's pool — closing it does. Left
            # unclosed, a service fetching many objects leaks connections and file descriptors
            # until the pool is exhausted, and the symptom is a hang rather than an error.
            stream = response["Body"]
            try:
                body: bytes = stream.read()
            finally:
                stream.close()
        except ClientError as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError(f"no object stored at {key!r}") from exc
            raise StorageError(f"could not read object {key!r}: {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"could not read object {key!r}: {exc}") from exc
        return body

    def delete(self, key: str) -> None:
        """Idempotent by virtue of the API: `DeleteObject` succeeds on a key that is not there."""
        validate_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"could not delete object {key!r}: {exc}") from exc

    def exists(self, key: str) -> bool:
        validate_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise StorageError(f"could not stat object {key!r}: {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"could not stat object {key!r}: {exc}") from exc
        return True

    def url_for(self, key: str, *, expires_in: int | None = None) -> str:
        """A presigned URL: time-limited, and carrying its own authorisation.

        Generated locally from the credentials — no network call — so this is cheap enough to do
        per response.
        """
        validate_key(key)
        try:
            url: str = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in if expires_in is not None else self._url_expiry_seconds,
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"could not build a URL for {key!r}: {exc}") from exc
        return url


def _is_not_found(exc: ClientError) -> bool:
    """Whether a botocore error means "no such object" rather than "something went wrong"."""
    error = exc.response.get("Error", {})
    return str(error.get("Code", "")) in _NOT_FOUND_CODES
