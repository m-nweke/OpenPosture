"""One contract, two implementations, and the key rules that make the contract safe.

The acceptance criterion for OP-41 is that the suite is written once and parameterized over both
backends. That is not tidiness: a contract asserted against only one implementation is a contract
the other is free to violate, and the entire point of the Protocol is that swapping the backend
changes nothing above it.

`S3Storage` runs against moto's in-memory S3 rather than a hand-written fake. A fake I wrote
would agree with my assumptions about S3 by construction, which is exactly the property that
makes it useless as evidence. moto implements the real API semantics — including the ones this
code depends on, like `DeleteObject` succeeding on a key that is not there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from moto import mock_aws

from openposture_api.config import Settings
from openposture_api.main import create_app
from openposture_api.storage import (
    CONTENT_TYPE_SUFFIXES,
    LocalDiskStorage,
    S3Storage,
    StorageBackend,
    create_storage,
    generate_key,
    get_storage,
    validate_key,
)
from openposture_api.storage.errors import (
    InvalidObjectKeyError,
    ObjectNotFoundError,
    StorageError,
    UnsupportedContentTypeError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

PNG = b"\x89PNG\r\n\x1a\n" + b"pretend this is an image" * 4

_BUCKET = "openposture-test"


@pytest.fixture
def local_storage(tmp_path: Path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "objects")


@pytest.fixture
def s3_storage() -> Iterator[S3Storage]:
    """A real `S3Storage` against moto. No network, no MinIO, no real credentials.

    Constructed the way production constructs it — no `client_factory` — so the boto3 `Config`
    it sets is genuinely exercised. Injecting a ready-made client instead skips that entirely,
    and the first version of this fixture did: the presigned-URL test came back with a SigV2
    signature because `signature_version="s3v4"` was never applied.

    Credentials are explicit dummies rather than ambient ones. moto ignores them, but a developer
    with real AWS credentials in their environment should never have a test pick them up.
    """
    with mock_aws():
        boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        ).create_bucket(Bucket=_BUCKET)
        yield S3Storage(
            _BUCKET,
            region_name="us-east-1",
            access_key="testing",
            secret_key="testing",
        )


@pytest.fixture(params=["local", "s3"])
def storage(request: pytest.FixtureRequest) -> StorageBackend:
    """Every test taking this fixture runs twice, once per implementation."""
    fixture_name = "local_storage" if request.param == "local" else "s3_storage"
    backend: StorageBackend = request.getfixturevalue(fixture_name)
    return backend


class TestContract:
    """Behaviour both implementations must share, asserted against each of them."""

    def test_what_goes_in_comes_back_out(self, storage: StorageBackend) -> None:
        stored = storage.put(PNG, content_type="image/png")

        assert storage.get(stored.key) == PNG

    def test_put_reports_what_it_stored(self, storage: StorageBackend) -> None:
        """Size and content type travel with the key so Epic E's rows do not need a second read."""
        stored = storage.put(PNG, content_type="image/png")

        assert stored.size == len(PNG)
        assert stored.content_type == "image/png"
        assert stored.key.endswith(".png")

    def test_put_never_returns_a_url(self, storage: StorageBackend) -> None:
        """The acceptance criterion. A URL persisted anywhere becomes invalid on a hostname
        change; the key does not."""
        stored = storage.put(PNG, content_type="image/png")

        assert not hasattr(stored, "url")
        assert "://" not in stored.key
        assert not stored.key.startswith("http")

    def test_two_puts_of_identical_bytes_get_different_keys(self, storage: StorageBackend) -> None:
        """Keys are random, not content-derived. Deduplicating by hash would mean one user
        deleting their analysis removes another user's identical upload."""
        first = storage.put(PNG, content_type="image/png")
        second = storage.put(PNG, content_type="image/png")

        assert first.key != second.key

    def test_exists_is_true_after_a_put(self, storage: StorageBackend) -> None:
        stored = storage.put(PNG, content_type="image/png")

        assert storage.exists(stored.key) is True

    def test_exists_is_false_for_a_key_never_written(self, storage: StorageBackend) -> None:
        assert storage.exists("analyses/0123456789abcdef.png") is False

    def test_getting_a_missing_object_raises_not_found(self, storage: StorageBackend) -> None:
        """A distinct exception, because "gone" is an ordinary outcome and "broken" is not."""
        with pytest.raises(ObjectNotFoundError):
            storage.get("analyses/0123456789abcdef.png")

    def test_delete_removes_the_object(self, storage: StorageBackend) -> None:
        stored = storage.put(PNG, content_type="image/png")

        storage.delete(stored.key)

        assert storage.exists(stored.key) is False

    def test_delete_is_idempotent(self, storage: StorageBackend) -> None:
        """S3's `DeleteObject` succeeds on a missing key. If local disk raised instead, a retry
        after a timeout would fail on one backend and succeed on the other."""
        stored = storage.put(PNG, content_type="image/png")

        storage.delete(stored.key)
        storage.delete(stored.key)

    def test_url_construction_is_a_separate_call(self, storage: StorageBackend) -> None:
        stored = storage.put(PNG, content_type="image/png")

        url = storage.url_for(stored.key)

        assert stored.key in url or stored.key.split("/")[-1] in url

    @pytest.mark.parametrize("content_type", sorted(CONTENT_TYPE_SUFFIXES))
    def test_every_allowed_media_type_round_trips(
        self, storage: StorageBackend, content_type: str
    ) -> None:
        stored = storage.put(PNG, content_type=content_type)

        assert storage.get(stored.key) == PNG
        assert stored.key.endswith(CONTENT_TYPE_SUFFIXES[content_type])

    def test_an_unsupported_media_type_is_refused(self, storage: StorageBackend) -> None:
        with pytest.raises(UnsupportedContentTypeError):
            storage.put(b"%PDF-1.4", content_type="application/pdf")

    @pytest.mark.parametrize(
        "hostile",
        [
            "../../etc/passwd",
            "analyses/../../etc/passwd",
            "/etc/passwd",
            "analyses/..%2f..%2fetc%2fpasswd",
            "analyses\\..\\windows\\system32",
            "",
            "analyses//double-slash.png",
            "analyses/trailing/",
        ],
    )
    def test_a_hostile_key_is_refused_by_every_operation(
        self, storage: StorageBackend, hostile: str
    ) -> None:
        """The acceptance criterion, and the legacy vulnerability (FINDINGS §5.1) stated as a
        test. Refusal has to hold on the read paths too, because in Epic E these keys arrive
        from database rows rather than from a request."""
        with pytest.raises(InvalidObjectKeyError):
            storage.get(hostile)

        with pytest.raises(InvalidObjectKeyError):
            storage.delete(hostile)

        with pytest.raises(InvalidObjectKeyError):
            storage.exists(hostile)

    def test_there_is_no_way_to_name_an_object(self, storage: StorageBackend) -> None:
        """The strongest form of the guarantee: `put` has no key parameter at all, so a
        caller-supplied path is not rejected — it is unrepresentable."""
        with pytest.raises(TypeError):
            storage.put(PNG, content_type="image/png", key="../../evil.png")  # type: ignore[call-arg]


class TestKeyGeneration:
    def test_keys_land_under_the_default_prefix(self) -> None:
        assert generate_key("image/jpeg").startswith("analyses/")

    def test_keys_carry_the_suffix_of_their_media_type_not_a_filename(self) -> None:
        """The uploaded filename never reaches this layer, so `photo.jpg.php` cannot influence
        what gets written."""
        assert generate_key("image/jpeg").endswith(".jpg")
        assert generate_key("image/png").endswith(".png")
        assert generate_key("image/webp").endswith(".webp")

    def test_generated_keys_are_opaque(self) -> None:
        key = generate_key("image/png")
        stem = key.removeprefix("analyses/").removesuffix(".png")

        assert len(stem) == 32
        assert stem.isalnum()

    def test_generated_keys_always_validate(self) -> None:
        """The two halves of this module must agree, or `put` would produce keys `get` rejects."""
        for content_type in CONTENT_TYPE_SUFFIXES:
            for _ in range(20):
                validate_key(generate_key(content_type))

    def test_an_unsafe_prefix_is_refused(self) -> None:
        with pytest.raises(InvalidObjectKeyError):
            generate_key("image/png", prefix="../escape")

    def test_jpg_is_not_accepted_as_a_media_type(self) -> None:
        """`image/jpg` is not registered. Accepting the misspelling here would put this
        allowlist out of step with the upload endpoint's."""
        with pytest.raises(UnsupportedContentTypeError):
            generate_key("image/jpg")


class TestLocalDiskSpecifics:
    def test_the_root_is_created_if_absent(self, tmp_path: Path) -> None:
        root = tmp_path / "does" / "not" / "exist"

        assert LocalDiskStorage(root).root.is_dir()

    def test_objects_are_written_inside_the_root(self, tmp_path: Path) -> None:
        storage = LocalDiskStorage(tmp_path / "objects")

        stored = storage.put(PNG, content_type="image/png")

        assert (storage.root / stored.key).is_file()

    def test_no_partial_files_survive_a_successful_write(self, tmp_path: Path) -> None:
        """Written to a temp file and renamed, so a reader never sees a half-written object."""
        storage = LocalDiskStorage(tmp_path / "objects")

        storage.put(PNG, content_type="image/png")

        assert list(storage.root.rglob("*.partial")) == []

    def test_urls_use_the_configured_base_path(self, tmp_path: Path) -> None:
        storage = LocalDiskStorage(tmp_path / "objects", base_url="/media/")
        stored = storage.put(PNG, content_type="image/png")

        assert storage.url_for(stored.key) == f"/media/{stored.key}"

    def test_the_expiry_argument_is_accepted_and_does_nothing(self, tmp_path: Path) -> None:
        """Documented rather than hidden: a filesystem has nothing to sign with, so this URL
        does not expire. It is why S3Storage exists for anything needing real delegation."""
        storage = LocalDiskStorage(tmp_path / "objects")
        stored = storage.put(PNG, content_type="image/png")

        assert storage.url_for(stored.key, expires_in=60) == storage.url_for(stored.key)


class TestS3Specifics:
    def test_the_object_lands_in_the_bucket_with_its_media_type(
        self, s3_storage: S3Storage
    ) -> None:
        """Content type must survive the round trip, or a browser fetching the URL gets a
        download prompt instead of an image."""
        stored = s3_storage.put(PNG, content_type="image/png")

        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        head = client.head_object(Bucket=_BUCKET, Key=stored.key)

        assert head["ContentType"] == "image/png"
        assert head["ContentLength"] == len(PNG)

    def test_urls_are_presigned_and_time_limited(self, s3_storage: S3Storage) -> None:
        stored = s3_storage.put(PNG, content_type="image/png")

        url = s3_storage.url_for(stored.key, expires_in=60)

        assert stored.key in url
        assert "X-Amz-Signature" in url
        assert "X-Amz-Expires=60" in url


class TestS3ErrorTranslation:
    """botocore's error shape must not leak upward.

    A caller should never have to know that the interesting part of a failure lives in
    `exc.response["Error"]["Code"]`, and swapping to local disk must not change what they catch.
    These use `client_factory` — the seam exists for exactly this, since moto has no way to make
    a healthy bucket fail on demand.
    """

    @staticmethod
    def _failing(operation: str, code: str) -> S3Storage:
        error = ClientError({"Error": {"Code": code, "Message": "boom"}}, operation)

        class _Client:
            def __getattr__(self, _name: str) -> object:
                def raise_error(**_kwargs: object) -> None:
                    raise error

                return raise_error

        return S3Storage(_BUCKET, client_factory=_Client)

    def test_a_missing_key_becomes_object_not_found(self) -> None:
        storage = self._failing("GetObject", "NoSuchKey")

        with pytest.raises(ObjectNotFoundError):
            storage.get("analyses/0123456789abcdef.png")

    def test_a_head_404_means_the_object_is_absent_not_broken(self) -> None:
        """`HeadObject` reports 404 with no error name, because a HEAD response has no body to
        put one in. Matching only on `NoSuchKey` would turn "not there" into a 500."""
        storage = self._failing("HeadObject", "404")

        assert storage.exists("analyses/0123456789abcdef.png") is False

    def test_a_real_fault_becomes_a_storage_error_not_a_not_found(self) -> None:
        """Access denied is not absence. Reporting it as absence would hide a broken deployment
        behind a plausible-looking empty result."""
        storage = self._failing("GetObject", "AccessDenied")

        with pytest.raises(StorageError) as caught:
            storage.get("analyses/0123456789abcdef.png")

        assert not isinstance(caught.value, ObjectNotFoundError)

    def test_a_failed_write_becomes_a_storage_error(self) -> None:
        storage = self._failing("PutObject", "InternalError")

        with pytest.raises(StorageError):
            storage.put(PNG, content_type="image/png")

    def test_a_failed_delete_becomes_a_storage_error(self) -> None:
        storage = self._failing("DeleteObject", "AccessDenied")

        with pytest.raises(StorageError):
            storage.delete("analyses/0123456789abcdef.png")

    def test_a_failed_stat_that_is_not_a_404_raises(self) -> None:
        storage = self._failing("HeadObject", "AccessDenied")

        with pytest.raises(StorageError):
            storage.exists("analyses/0123456789abcdef.png")


class TestFactory:
    def test_local_is_the_default(self, tmp_path: Path) -> None:
        settings = Settings(environment="test", json_logs=True, storage_root=tmp_path)

        assert create_storage(settings).name == "local"

    def test_configuration_selects_s3(self) -> None:
        with mock_aws():
            settings = Settings(
                environment="test",
                json_logs=True,
                storage_backend="s3",
                s3_access_key="test",
                s3_secret_key="test",
            )

            assert create_storage(settings).name == "s3"

    def test_both_implementations_satisfy_the_protocol(
        self, local_storage: LocalDiskStorage, s3_storage: S3Storage
    ) -> None:
        assert isinstance(local_storage, StorageBackend)
        assert isinstance(s3_storage, StorageBackend)


class TestDependency:
    def test_the_dependency_hands_out_the_lifespan_storage(self, settings: Settings) -> None:
        app = create_app(settings, load_backend=False)

        @app.get("/storage-name")
        async def storage_name(
            storage: Annotated[StorageBackend, Depends(get_storage)],
        ) -> dict[str, str]:
            return {"name": storage.name}

        with TestClient(app) as client:
            assert client.get("/storage-name").json() == {"name": "local"}

    def test_the_dependency_can_be_overridden(self, settings: Settings, tmp_path: Path) -> None:
        """An endpoint test points storage at a temporary directory and asserts on real files."""
        app = create_app(settings, load_backend=False)
        replacement = LocalDiskStorage(tmp_path / "override")

        @app.get("/storage-root")
        async def storage_root(
            storage: Annotated[StorageBackend, Depends(get_storage)],
        ) -> dict[str, bool]:
            return {"is_override": storage is replacement}

        app.dependency_overrides[get_storage] = lambda: replacement

        with TestClient(app) as client:
            assert client.get("/storage-root").json() == {"is_override": True}

    def test_a_route_run_without_lifespan_gets_a_clear_message(self, settings: Settings) -> None:
        """The failure mode is a test that built an app and never entered the TestClient context
        manager. Without the check it surfaces as `AttributeError: state`, which names nothing."""
        app = create_app(settings, load_backend=False)

        with pytest.raises(StorageError, match="lifespan has not run"):
            get_storage(Request({"type": "http", "app": app, "headers": []}))


class TestKeyLimits:
    def test_an_absurdly_long_key_is_refused(self) -> None:
        """Bounded because keys reach a filesystem, which has its own limits, and an unbounded
        key is a way to provoke an OSError from inside what should be a validation error."""
        with pytest.raises(InvalidObjectKeyError, match="too long"):
            validate_key("analyses/" + "a" * 600 + ".png")


class TestLocalDiskFailures:
    def test_a_symlink_escaping_the_root_is_refused(self, tmp_path: Path) -> None:
        """The second barrier. `validate_key` passes this key — it is textually well-formed —
        and only resolution catches that it lands outside the root."""
        root = tmp_path / "objects"
        storage = LocalDiskStorage(root)
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "escape").symlink_to(outside)

        with pytest.raises(StorageError, match="escapes the storage root"):
            storage.get("escape/secret.png")

    def test_an_unreadable_object_is_a_storage_error_not_a_missing_one(
        self, tmp_path: Path
    ) -> None:
        """A directory where a file should be reads as an OSError, not FileNotFoundError. It must
        not be reported as absence — the object is there, something else is wrong."""
        storage = LocalDiskStorage(tmp_path / "objects")
        key = "analyses/0123456789abcdef0123456789abcdef.png"
        (storage.root / key).mkdir(parents=True)

        with pytest.raises(StorageError) as caught:
            storage.get(key)

        assert not isinstance(caught.value, ObjectNotFoundError)
