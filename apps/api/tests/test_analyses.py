"""The upload endpoint, and every way it can be handed something it should refuse.

The ticket names seven cases: valid upload, oversize, disallowed type, undecodable file, no
person, low confidence, and backend failure. Each is here, plus the one that matters most for
this project's story — an all-gaps report is a 201, not an error.
"""

from __future__ import annotations

import io
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from openposture_api.auth import get_current_user_id
from openposture_api.config import Settings
from openposture_api.db import get_session
from openposture_api.db.models import Analysis
from openposture_api.images import MAX_IMAGE_BYTES
from openposture_api.main import create_app
from openposture_api.pose import get_pose_backend
from openposture_api.schemas import PostureReportModel
from openposture_api.storage import LocalDiskStorage, get_storage
from pose_backends.errors import ModelLoadError
from pose_backends.fake import FakePoseBackend, PosePreset
from posture_core import build_report

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from fastapi import FastAPI

ENDPOINT = "/api/v1/analyses"


def hunchback_report_dict() -> dict[str, Any]:
    """A real report, serialised by the engine's own `to_dict()`.

    Built rather than hand-written, so these assertions are about the actual contract. A literal
    would drift from the engine and the tests would keep passing while saying nothing.
    """
    frame = FakePoseBackend(PosePreset.HUNCHBACK).detect(None)  # type: ignore[arg-type]
    assert frame is not None
    return build_report(frame).to_dict()


def make_image(
    *,
    width: int = 640,
    height: int = 480,
    fmt: str = "JPEG",
    orientation: int | None = None,
) -> bytes:
    """A small real image in the requested format.

    Real bytes rather than a stub, because the point of this layer is that it decodes what it is
    given — a fake would prove nothing about Pillow's behaviour, which is the behaviour under
    test.
    """
    image = Image.new("RGB", (width, height), color=(120, 130, 140))
    # A little structure, so the encoder cannot collapse it to something degenerate.
    for x in range(0, width, 8):
        for y in range(0, height, 8):
            image.putpixel((x, y), (200, 40, 40))

    buffer = io.BytesIO()
    if orientation is not None:
        exif = image.getexif()
        exif[0x0112] = orientation
        image.save(buffer, format=fmt, exif=exif)
    else:
        image.save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDiskStorage:
    return LocalDiskStorage(tmp_path / "objects")


async def _fake_session() -> AsyncIterator[MagicMock]:
    """A mock database session for unit tests.

    Configured for both write paths (add/flush/commit) and read paths (execute). The execute
    mock returns empty results by default — no rows, scalar_one_or_none returns None — which is
    what the list, get, and delete routes see when no data exists.

    `flush` is not a plain no-op, though, and it cannot be. `Base` declares
    `id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)`, and SQLAlchemy
    applies a column default at INSERT time — not at construction. So `analysis.id` is None until
    something flushes, which is exactly why `AnalysisRepository.create` flushes the parent before
    it builds the child rows that reference it. A mock whose flush did nothing would leave `id`
    unset and the route would fail to build its response — so the fake reproduces the one piece
    of real flush behaviour these tests depend on, and nothing else.

    Persistence correctness is covered by the integration suite in `tests/integration/`.
    """
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    execute_result.scalars.return_value.all.return_value = []

    pending: list[Any] = []

    async def _flush() -> None:
        for obj in pending:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    mock = MagicMock()
    mock.add = MagicMock(side_effect=pending.append)
    mock.add_all = MagicMock(side_effect=pending.extend)
    mock.get = AsyncMock(return_value=None)
    mock.flush = AsyncMock(side_effect=_flush)
    mock.commit = AsyncMock()
    mock.close = AsyncMock()
    mock.execute = AsyncMock(return_value=execute_result)
    yield mock


UPLOADER_ID = uuid.uuid4()
"""The authenticated user that `build_client`'s app attributes uploads to."""


@contextmanager
def build_client(
    settings: Settings,
    storage: LocalDiskStorage,
    *,
    preset: PosePreset = PosePreset.STRAIGHT,
    backend: object | None = None,
) -> Iterator[TestClient]:
    """An app whose pose backend, storage, and database session are all substituted.

    `load_backend=False` plus three dependency overrides means this test touches no model file,
    writes nowhere but a temporary directory, and makes no database call — the point of all three
    seams.

    A context manager rather than a bare generator. Called as `next(build_client(...))` the
    generator is never closed, so the `with TestClient(...)` block below never exits: lifespan
    shutdown does not run and the transport is left to the garbage collector.
    """
    app: FastAPI = create_app(settings, load_backend=False)
    app.dependency_overrides[get_pose_backend] = lambda: backend or FakePoseBackend(preset)
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_session] = _fake_session
    # Overridden rather than minting a real token: these tests assert the upload pipeline, and a
    # signed token would make every one of them also a test of JWT decoding. The dependency's own
    # behaviour is covered by `TestAccessTokenDependency` in `test_tenancy.py`, and that it is
    # *wired up* by `TestRouteTable` in the same file — neither of which can pass because a
    # fixture faked it.
    app.dependency_overrides[get_current_user_id] = lambda: UPLOADER_ID
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def client(settings: Settings, storage: LocalDiskStorage) -> Iterator[TestClient]:
    with build_client(settings, storage) as test_client:
        yield test_client


def upload(client: TestClient, data: bytes, *, filename: str = "photo.jpg") -> Any:
    return client.post(ENDPOINT, files={"image": (filename, data, "image/jpeg")})


class TestValidUpload:
    def test_a_photograph_produces_a_report(self, client: TestClient) -> None:
        """The assertion the whole project exists to make true."""
        response = upload(client, make_image())

        assert response.status_code == 201
        body = response.json()
        assert body["pose_detected"] is True
        assert body["report"]["schema_version"]
        assert body["report"]["metrics"]

    def test_the_response_carries_a_database_id(self, client: TestClient) -> None:
        """Every persisted analysis gets a UUID that the client can use to retrieve or delete it."""
        body = upload(client, make_image()).json()

        assert "id" in body
        # A valid UUID — not a sequential integer, not a storage key, not a URL.
        parsed = uuid.UUID(body["id"])
        assert parsed.version == 4

    def test_the_report_carries_real_measured_values(self, client: TestClient) -> None:
        """Not a placeholder, not a constant string — a number describing the pose."""
        body = upload(client, make_image()).json()

        trunk = body["report"]["metrics"]["trunk_inclination_deg"]

        assert trunk["status"] == "ok"
        assert isinstance(trunk["value"], (int, float))

    def test_the_original_bytes_are_stored_and_the_key_returned(
        self, client: TestClient, storage: LocalDiskStorage
    ) -> None:
        """A key, not a URL. See the storage layer's module docstring."""
        data = make_image()

        body = upload(client, data).json()

        assert storage.get(body["object_key"]) == data
        assert "://" not in body["object_key"]

    def test_the_uploaded_filename_never_becomes_the_key(self, client: TestClient) -> None:
        """The legacy `/upload` used `file.filename` directly (FINDINGS §5.1). Here the filename
        is hostile and the key is unaffected, because the filename never reaches storage."""
        body = upload(client, make_image(), filename="../../etc/passwd").json()

        assert body["object_key"].startswith("analyses/")
        assert "passwd" not in body["object_key"]
        assert ".." not in body["object_key"]

    def test_the_declared_content_type_does_not_decide_the_stored_type(
        self, client: TestClient, storage: LocalDiskStorage
    ) -> None:
        """A PNG announced as `image/jpeg` is stored as a PNG. The header is a client claim; the
        decoded content is the fact."""
        png = make_image(fmt="PNG")

        response = client.post(ENDPOINT, files={"image": ("lies.jpg", png, "image/jpeg")})

        assert response.status_code == 201
        assert response.json()["object_key"].endswith(".png")

    @pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
    def test_every_allowed_format_is_accepted(self, client: TestClient, fmt: str) -> None:
        assert upload(client, make_image(fmt=fmt)).status_code == 201


class TestExifOrientation:
    def test_a_rotated_photo_produces_the_same_report_as_its_upright_twin(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """The acceptance criterion, and the single most likely source of confidently wrong
        answers in real use: a phone photo carries a rotation flag rather than rotated pixels,
        and a decoder that ignores it hands the model a person lying down.

        Asserted on the reported image dimensions, because the fake backend ignores pixels — a
        portrait image decoded without the transpose comes back landscape.
        """
        upright = make_image(width=480, height=640)
        # Orientation 6 means "rotate 90° clockwise to display", so the stored pixels are
        # landscape and the displayed image is portrait.
        rotated = make_image(width=640, height=480, orientation=6)

        with build_client(settings, LocalDiskStorage(tmp_path / "a")) as client:
            upright_body = upload(client, upright).json()
            rotated_body = upload(client, rotated).json()

        assert rotated_body["image"] == upright_body["image"]
        assert rotated_body["image"] == {"width": 480, "height": 640}


class TestAbstention:
    def test_no_person_is_a_201_not_an_error(self, settings: Settings, tmp_path: Path) -> None:
        """The user photographed their desk. The request succeeded; the answer is "nobody here"."""
        with build_client(
            settings, LocalDiskStorage(tmp_path / "a"), preset=PosePreset.NO_PERSON
        ) as client:
            response = upload(client, make_image())

        assert response.status_code == 201
        body = response.json()
        assert body["pose_detected"] is False
        assert body["report"] is None
        # Still stored: the image is evidence even when nothing was found in it.
        assert body["object_key"]

    def test_a_partly_occluded_pose_returns_201_with_gaps(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        """Gaps are the output C3 exists to produce. A 4xx here would throw them away and put the
        frontend into a generic error state — the original's silent-default defect in reverse."""
        with build_client(
            settings, LocalDiskStorage(tmp_path / "a"), preset=PosePreset.PARTIAL_OCCLUSION
        ) as client:
            response = upload(client, make_image())

            assert response.status_code == 201
            quality = response.json()["report"]["quality"]
            assert quality["gaps"]
            assert quality["assessed"] < quality["total"]

    def test_gaps_name_the_metric_and_why(self, settings: Settings, tmp_path: Path) -> None:
        """ "Couldn't assess your knees, try a wider shot" needs both halves."""
        with build_client(
            settings, LocalDiskStorage(tmp_path / "a"), preset=PosePreset.PARTIAL_OCCLUSION
        ) as client:
            gaps = upload(client, make_image()).json()["report"]["quality"]["gaps"]

            assert all(gap["metric"] for gap in gaps)
            assert all(gap["status"] for gap in gaps)


class TestRejections:
    def test_an_oversize_upload_is_refused(self, client: TestClient) -> None:
        oversize = b"\xff\xd8\xff\xe0" + b"\x00" * (MAX_IMAGE_BYTES + 1)

        response = upload(client, oversize)

        assert response.status_code == 413
        body = response.json()
        assert body["type"].endswith("/image-too-large")
        assert body["limit"] == MAX_IMAGE_BYTES

    def test_the_size_check_happens_before_decoding(self, client: TestClient) -> None:
        """The payload is not a valid image at all. A 413 rather than a 400 proves the limit was
        applied first — which is the difference between rejecting a 2 GB upload and decoding it."""
        response = upload(client, b"\x00" * (MAX_IMAGE_BYTES + 1))

        assert response.status_code == 413

    def test_a_disallowed_format_is_refused(self, client: TestClient) -> None:
        """A real image, in a format outside the allowlist. GIF decodes fine and is not accepted."""
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), color=(1, 2, 3)).save(buffer, format="GIF")

        response = upload(client, buffer.getvalue())

        assert response.status_code == 415
        body = response.json()
        assert body["type"].endswith("/unsupported-image-type")
        assert body["detected_format"] == "GIF"

    def test_an_undecodable_file_is_refused(self, client: TestClient) -> None:
        response = upload(client, b"this is not an image, it is a sentence")

        assert response.status_code == 400
        assert response.json()["type"].endswith("/invalid-image")

    def test_an_empty_file_is_refused(self, client: TestClient) -> None:
        response = upload(client, b"")

        assert response.status_code == 400

    def test_a_truncated_image_is_refused(self, client: TestClient) -> None:
        """A real upload failure mode, not a malicious one: the connection dropped mid-transfer."""
        response = upload(client, make_image()[:200])

        assert response.status_code == 400

    def test_a_missing_file_field_is_a_validation_error(self, client: TestClient) -> None:
        response = client.post(ENDPOINT)

        assert response.status_code == 422
        assert response.json()["type"].endswith("/validation-error")

    def test_every_rejection_uses_the_problem_envelope(self, client: TestClient) -> None:
        """One error parser for the frontend, whatever went wrong."""
        for payload in (b"", b"nonsense", b"\x00" * (MAX_IMAGE_BYTES + 1)):
            response = upload(client, payload)

            assert response.headers["content-type"].startswith("application/problem+json")
            assert set(response.json()) >= {"type", "title", "status", "detail", "instance"}


class TestBackendFailure:
    def test_a_broken_backend_is_a_502_not_a_500(self, settings: Settings, tmp_path: Path) -> None:
        """502 says the fault is downstream of this service, which is what tells an operator to
        look at the model rather than at the API."""

        class _Broken:
            name = "broken"

            def detect(self, image_bgr: object) -> None:
                raise ModelLoadError("the graph could not be built")

            def warmup(self) -> None:
                return

        with build_client(settings, LocalDiskStorage(tmp_path / "a"), backend=_Broken()) as client:
            response = upload(client, make_image())

        assert response.status_code == 502
        assert response.json()["type"].endswith("/pose-backend-failed")

    def test_the_backend_error_text_does_not_reach_the_client(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        class _Broken:
            name = "broken"

            def detect(self, image_bgr: object) -> None:
                raise ModelLoadError("/srv/secrets/model.task is corrupt")

            def warmup(self) -> None:
                return

        with build_client(settings, LocalDiskStorage(tmp_path / "a"), backend=_Broken()) as client:
            response = upload(client, make_image())

        assert "/srv/secrets" not in response.text

    def test_an_unavailable_backend_is_a_503(self, settings: Settings, tmp_path: Path) -> None:
        """No backend override: the app started without loading one, which is what a missing model
        file looks like in production.

        Authenticated, because the 401 would otherwise arrive first and this test would pass
        without ever reaching the backend. That ordering is the right one — an anonymous caller
        should not be able to probe which of a service's dependencies are down — but it means the
        503 can only be observed from behind a valid session.
        """
        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_storage] = lambda: LocalDiskStorage(tmp_path / "a")
        app.dependency_overrides[get_current_user_id] = lambda: UPLOADER_ID

        with TestClient(app, raise_server_exceptions=False) as client:
            response = upload(client, make_image())

        assert response.status_code == 503
        assert response.json()["type"].endswith("/pose-backend-unavailable")


class TestContractStrictness:
    """The response models exist to make schema drift impossible to ship quietly.

    Both halves are asserted, because both Pydantic defaults are permissive: unknown fields are
    ignored, and types are coerced unless asked otherwise. A model carrying neither guard would
    let a renamed, added or retyped field through while the OpenAPI document — and therefore the
    frontend's generated types — went on describing the old shape.
    """

    def test_an_added_field_is_refused(self) -> None:
        """A key appearing in `to_dict()` must not be silently dropped on its way out."""
        payload = hunchback_report_dict()
        payload["experimental_score"] = 42

        with pytest.raises(ValidationError, match="experimental_score"):
            PostureReportModel.model_validate(payload, strict=True)

    def test_a_renamed_field_is_refused(self) -> None:
        payload = hunchback_report_dict()
        payload["overall"] = payload.pop("overall_score")

        with pytest.raises(ValidationError):
            PostureReportModel.model_validate(payload, strict=True)

    def test_a_number_that_became_a_string_is_refused(self) -> None:
        """The case non-strict validation hides: `"27.0"` parses, so the model would accept a
        response the declared schema says is a number."""
        payload = hunchback_report_dict()
        payload["inference_ms"] = "27.0"

        with pytest.raises(ValidationError, match="inference_ms"):
            PostureReportModel.model_validate(payload, strict=True)

    def test_the_engine_s_real_output_still_validates(self) -> None:
        """The guard has to admit what the engine actually produces, or it is just an outage."""
        model = PostureReportModel.model_validate(hunchback_report_dict(), strict=True)

        assert model.metrics["trunk_inclination_deg"].value == 32.0


class TestReadAndDeleteEndpoints:
    """E6: GET /analyses, GET /analyses/{id}, DELETE /analyses/{id}.

    All three require authentication. The tests override `get_current_user_id` with a fixed UUID
    and inject a mock session so no database is needed — behaviour correctness is in the
    integration suite; these tests cover the HTTP contract.
    """

    _USER_ID = uuid.uuid4()

    def test_list_requires_authentication(self, settings: Settings, tmp_path: Any) -> None:
        """Without an auth override the placeholder raises 401."""
        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _fake_session
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/analyses")
        assert response.status_code == 401

    def test_get_requires_authentication(self, settings: Settings) -> None:
        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _fake_session
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(f"/api/v1/analyses/{uuid.uuid4()}")
        assert response.status_code == 401

    def test_delete_requires_authentication(self, settings: Settings) -> None:
        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _fake_session
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.delete(f"/api/v1/analyses/{uuid.uuid4()}")
        assert response.status_code == 401

    def test_get_returns_404_for_unknown_id(self, settings: Settings) -> None:
        """404 whether not found or not owned — existence is not leaked."""
        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _fake_session
        app.dependency_overrides[get_current_user_id] = lambda: self._USER_ID
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(f"/api/v1/analyses/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_delete_returns_404_for_unknown_id(self, settings: Settings) -> None:
        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _fake_session
        app.dependency_overrides[get_current_user_id] = lambda: self._USER_ID
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.delete(f"/api/v1/analyses/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_list_returns_empty_page_for_new_user(self, settings: Settings) -> None:
        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _fake_session
        app.dependency_overrides[get_current_user_id] = lambda: self._USER_ID
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/analyses")
        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["next_cursor"] is None

    def test_list_rejects_malformed_cursor(self, settings: Settings) -> None:
        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _fake_session
        app.dependency_overrides[get_current_user_id] = lambda: self._USER_ID
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/analyses?cursor=not-a-valid-cursor")
        assert response.status_code == 400

    def test_delete_removes_the_stored_object(
        self, settings: Settings, storage: LocalDiskStorage
    ) -> None:
        """Deleting the rows must also destroy the image.

        The row is the only thing that knows the object key, so a delete that stops at the
        database leaves an image nothing can ever reach — and, for photographs of the user's
        body, leaves it after they asked for it to be gone.
        """
        stored = storage.put(b"not-really-a-jpeg", content_type="image/jpeg")
        assert storage.exists(stored.key)

        async def _session_holding_one_row() -> AsyncIterator[MagicMock]:
            result = MagicMock()
            # What `DELETE ... RETURNING object_key` yields for a row that matched.
            result.scalar_one_or_none.return_value = stored.key
            mock = MagicMock()
            mock.execute = AsyncMock(return_value=result)
            mock.flush = AsyncMock()
            mock.commit = AsyncMock()
            mock.close = AsyncMock()
            yield mock

        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _session_holding_one_row
        app.dependency_overrides[get_storage] = lambda: storage
        app.dependency_overrides[get_current_user_id] = lambda: self._USER_ID

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.delete(f"/api/v1/analyses/{uuid.uuid4()}")

        assert response.status_code == 204
        assert not storage.exists(stored.key), "the object outlived the row that named it"


class TestListThumbnails:
    """E10: the history list's `image_url`, built from storage, never read out of a row."""

    _USER_ID = uuid.uuid4()

    def test_list_items_carry_a_storage_built_image_url(
        self, settings: Settings, storage: LocalDiskStorage
    ) -> None:
        stored = storage.put(b"not-really-a-jpeg", content_type="image/jpeg")
        row = Analysis(
            id=uuid.uuid4(),
            created_at=datetime.now(UTC),
            object_key=stored.key,
            pose_detected=True,
            overall_score=72.0,
        )

        async def _session_holding_one_row() -> Any:
            result = MagicMock()
            result.scalars.return_value.all.return_value = [row]
            mock = MagicMock()
            mock.execute = AsyncMock(return_value=result)
            yield mock

        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _session_holding_one_row
        app.dependency_overrides[get_storage] = lambda: storage
        app.dependency_overrides[get_current_user_id] = lambda: self._USER_ID

        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/analyses")

        assert response.status_code == 200
        item = response.json()["items"][0]
        # Same construction the storage layer would do for any other caller, not a value copied
        # out of the row — there is no `image_url` column for it to have come from.
        assert item["image_url"] == storage.url_for(stored.key)
        assert item["object_key"] == stored.key


class TestTrunkInclinationTrendEndpoint:
    """E10: GET /analyses/metrics/trunk-inclination — the history sparkline's data source."""

    _USER_ID = uuid.uuid4()

    def test_requires_authentication(self, settings: Settings) -> None:
        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _fake_session
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/analyses/metrics/trunk-inclination")
        assert response.status_code == 401

    def test_returns_an_empty_series_for_a_new_user(self, settings: Settings) -> None:
        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _fake_session
        app.dependency_overrides[get_current_user_id] = lambda: self._USER_ID
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/analyses/metrics/trunk-inclination")
        assert response.status_code == 200
        assert response.json() == {"points": []}

    def test_a_gap_is_null_not_zero(self, settings: Settings) -> None:
        """A row with no measured value must not become a plotted `0`.

        The original engine's silent-`None`-to-"Straight back" defect, in chart form — this is
        the response-schema half of the guarantee the repo test covers at the query level.
        """

        async def _session_with_one_gap() -> Any:
            # `.created_at` etc., not tuple indices — the repository reads these the same way
            # SQLAlchemy's own `Row` objects support attribute access for labelled columns.
            row = SimpleNamespace(
                created_at=datetime.now(UTC),
                rules_version="1.0.0",
                value=None,
                status="insufficient_keypoints",
            )
            result = MagicMock()
            result.all.return_value = [row]
            mock = MagicMock()
            mock.execute = AsyncMock(return_value=result)
            yield mock

        app = create_app(settings, load_backend=False)
        app.dependency_overrides[get_session] = _session_with_one_gap
        app.dependency_overrides[get_current_user_id] = lambda: self._USER_ID
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/analyses/metrics/trunk-inclination")

        assert response.status_code == 200
        point = response.json()["points"][0]
        assert point["value"] is None
        assert point["status"] == "insufficient_keypoints"
