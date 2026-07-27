"""Turning an uploaded file into an array the pose backend will accept.

Three guards live here, and each one replaces a specific thing the original did wrong.

**Size is enforced before decode.** `UploadFile` is spooled to disk by Starlette, so the bytes are
not in memory yet when this runs — but a decoded image is, and a 50 MB PNG of mostly one colour
decompresses to hundreds of megabytes of pixels. Checking the compressed length first is what
keeps an upload from being a memory-exhaustion primitive. The original had no limit at all.

**The media type comes from the decoded content, not from the request.** `Content-Type` is a
client claim and a filename is worse — the original used `file.filename` as a storage key outright
(FINDINGS §5.1). Pillow reports what it actually parsed, and that is what gets recorded and
allowlisted.

**EXIF orientation is applied.** Phone cameras almost always store the sensor's raw orientation
plus a rotation flag rather than rotating the pixels. Decode without honouring the flag and a
portrait photo arrives sideways, at which point every angular metric describes a person lying
down — confidently and wrongly. `fixtures/images/desk_lean_exif.jpeg` exists to keep this honest;
it retains `orientation=6`.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Final

import numpy as np
import structlog
from PIL import Image, ImageOps, UnidentifiedImageError

from openposture_api.storage import CONTENT_TYPE_SUFFIXES

if TYPE_CHECKING:
    from pose_backends.base import ImageBGR

__all__ = [
    "MAX_IMAGE_BYTES",
    "MAX_PIXELS",
    "DecodedImage",
    "ImageTooLargeError",
    "InvalidImageError",
    "UnsupportedImageTypeError",
    "decode_upload",
]

_LOGGER = structlog.get_logger(__name__)

MAX_IMAGE_BYTES: Final = 10 * 1024 * 1024
"""10 MB, matching the plan. Comfortably above a phone photograph and far below anything that
threatens the process."""

MAX_PIXELS: Final = 50_000_000
"""Cap on *decoded* size, which compressed length does not bound.

A decompression bomb is a small file that expands enormously — the classic example is a highly
compressible PNG a few kilobytes long that decodes to gigapixels. 50 megapixels is roughly twice
the largest consumer camera and about 200 MB as RGB, so a legitimate upload never approaches it.

Pillow has its own `MAX_IMAGE_PIXELS` warning at ~89 MP, but it emits a warning rather than
refusing, and a warning does not stop the allocation.
"""

_PILLOW_FORMAT_TO_MEDIA_TYPE: Final[dict[str, str]] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
"""What Pillow calls a format, mapped to what the web calls it.

Keys are Pillow's `Image.format` values. The values are exactly the allowlist the storage layer
publishes, and the check below asserts the two agree — an image the API accepts but storage
refuses to write would be a 500 at the worst possible moment.
"""

if set(_PILLOW_FORMAT_TO_MEDIA_TYPE.values()) != set(CONTENT_TYPE_SUFFIXES):  # pragma: no cover
    raise RuntimeError(
        "the decoder's accepted formats and the storage layer's allowlist have diverged: "
        f"{sorted(_PILLOW_FORMAT_TO_MEDIA_TYPE.values())} vs {sorted(CONTENT_TYPE_SUFFIXES)}"
    )


class InvalidImageError(Exception):
    """The bytes are not an image this service can read."""


class ImageTooLargeError(Exception):
    """Too big to accept, by compressed length or by decoded pixel count."""

    def __init__(self, message: str, *, limit: int, actual: int) -> None:
        super().__init__(message)
        self.limit = limit
        self.actual = actual


class UnsupportedImageTypeError(Exception):
    """A real image, in a format outside the allowlist."""

    def __init__(self, message: str, *, detected: str) -> None:
        super().__init__(message)
        self.detected = detected


class DecodedImage:
    """An upload that survived every check, in the form each consumer wants it.

    Carries both the original bytes and the decoded array on purpose: storage persists exactly
    what the user sent, while the backend needs pixels. Re-encoding for storage would mean the
    stored object is not the file the user uploaded, which quietly ruins it as evidence when a
    result is disputed — and it is the mirror of the legacy `draw()`, which read the same image
    from disk twice rather than passing the one it already had.
    """

    __slots__ = ("array", "content_type", "data", "height", "width")

    def __init__(self, *, data: bytes, array: ImageBGR, content_type: str) -> None:
        self.data = data
        self.array = array
        self.content_type = content_type
        self.height = int(array.shape[0])
        self.width = int(array.shape[1])


def decode_upload(data: bytes) -> DecodedImage:
    """Validate and decode uploaded bytes.

    :raises ImageTooLargeError: over the byte limit, or over the decoded pixel limit.
    :raises UnsupportedImageTypeError: a readable image in a format outside the allowlist.
    :raises InvalidImageError: not decodable as an image at all.
    """
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageTooLargeError(
            f"image is {len(data)} bytes, over the {MAX_IMAGE_BYTES} byte limit",
            limit=MAX_IMAGE_BYTES,
            actual=len(data),
        )

    if not data:
        raise InvalidImageError("the uploaded file is empty")

    try:
        # `Image.open` is lazy: it reads the header and stops. That is what allows the format and
        # dimension checks below to happen *before* any pixel data is allocated.
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format or "unknown"
            media_type = _PILLOW_FORMAT_TO_MEDIA_TYPE.get(image_format)
            if media_type is None:
                raise UnsupportedImageTypeError(
                    f"{image_format} images are not supported. Supported formats: "
                    f"{', '.join(sorted(_PILLOW_FORMAT_TO_MEDIA_TYPE))}.",
                    detected=image_format,
                )

            pixels = image.width * image.height
            if pixels > MAX_PIXELS:
                raise ImageTooLargeError(
                    f"image decodes to {pixels} pixels, over the {MAX_PIXELS} pixel limit",
                    limit=MAX_PIXELS,
                    actual=pixels,
                )

            # Applied to the array handed to the model, and *only* to that array. The bytes
            # persisted by the storage layer are `DecodedImage.data` — the original upload,
            # rotation flag intact — because the stored object should be the file the user
            # actually sent. Re-encoding it here would make the evidence differ from the
            # submission the moment a result is disputed.
            upright = ImageOps.exif_transpose(image) or image
            # `convert` after the transpose and before the array: RGBA and greyscale and palette
            # images all reach the backend as three channels, which is what it expects, and a
            # transparent PNG does not arrive with an alpha channel the model would read as data.
            rgb = upright.convert("RGB")
            array = np.asarray(rgb, dtype=np.uint8)
    except UnidentifiedImageError as exc:
        raise InvalidImageError("the uploaded file could not be decoded as an image") from exc
    except OSError as exc:
        # Pillow raises OSError for truncated files, which are a genuine and common upload
        # failure rather than a server fault.
        #
        # Pillow's own message is deliberately not forwarded. This string reaches the client
        # verbatim through the problem document, and decoder messages carry file paths and
        # library internals that a user can do nothing with. The cause is logged with its
        # traceback by the unhandled-exception path instead.
        _LOGGER.info("image_decode_failed", error_type=type(exc).__name__, error=str(exc))
        raise InvalidImageError(
            "the uploaded image could not be read — it may be truncated or corrupt"
        ) from exc

    # RGB -> BGR. The backend Protocol documents BGR, matching cv2's decode order, and getting it
    # wrong does not crash — it silently degrades detection in a way no assertion would catch.
    # `ascontiguousarray` because the reversed view has a negative stride.
    bgr: ImageBGR = np.ascontiguousarray(array[:, :, ::-1])

    return DecodedImage(data=data, array=bgr, content_type=media_type)
