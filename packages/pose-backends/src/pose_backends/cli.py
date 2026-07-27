"""``python -m pose_backends.cli`` — run an image through a backend and print what came out.

The first point in the rebuild where something real is visible: a photograph in, thirty-four
measured landmarks out, with no web stack, no database, no frontend and no API key. That is worth
having on its own, and it is worth having *early* — Epic C is about to spend a dozen tickets
tuning thresholds, and inspecting landmark values by hand is how that work gets done.

Its JSON output is also the new-engine half of the legacy comparison. `docs/archive/
legacy-baseline.json` captured the old engine's verdicts on all eight fixtures before its weights
and its TensorFlow 2.12 environment were removed; that capture cannot be repeated. So the schema
here is versioned and changes deliberately, not incidentally.

Design note: this module is the *only* place in the package that prints. `MediaPipeBackend` and
`FakePoseBackend` return values and raise exceptions; presentation lives here. The legacy engine
mixed the two — `posture_image.py` computed, printed and drew in one function — which is a large
part of why none of it could be called from a web request.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pose_backends.errors import PoseBackendError
from pose_backends.fake import BACKEND_NAME as FAKE_NAME
from pose_backends.fake import PosePreset
from pose_backends.mediapipe_backend import BACKEND_NAME as MEDIAPIPE_NAME
from pose_backends.registry import create_backend
from posture_core import KeypointName

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pose_backends.base import ImageBGR
    from posture_core import Landmark, PoseFrame

__all__ = ["OUTPUT_SCHEMA_VERSION", "main"]

OUTPUT_SCHEMA_VERSION: Final = "1.0"
"""Bumped deliberately when the JSON shape changes.

Consumers exist outside this repository's test suite — the evaluation writeup in Epic H compares
this output against a legacy capture that can never be regenerated. An unversioned format would
make "the numbers changed" and "the format changed" indistinguishable after the fact.
"""

EXIT_OK: Final = 0
EXIT_NO_POSE: Final = 1
"""Distinct from a crash. "Nobody in this photo" is a *result*, and a script batching a directory
of images needs to tell it apart from "the model file is missing"."""
EXIT_ERROR: Final = 2

NOT_REPORTED: Final = "not_reported"
"""Status for a canonical keypoint this backend did not return at all.

Distinct from every :class:`DisplayStatus` value, all of which describe a landmark that *was*
returned. Absence and low confidence are different facts (OP-25).
"""

# Confidence below which a landmark is reported but not trusted. Matches MediaPipe's own default
# detection confidence.
_CONFIDENT: Final = 0.5


class DisplayStatus(StrEnum):
    """How much to believe a landmark, for the table's rightmost column.

    **Provisional, and deliberately shallow.** The real keypoint status model — with the
    OK / LOW_CONFIDENCE / NOT_DETECTED / OUT_OF_FRAME distinction that ends the silent false
    negative — is OP-25, and lives in `posture_core` where the rules can act on it. This is a
    display convenience so the table is readable today; when OP-25 lands, this enum is deleted and
    the column renders the real status instead.

    Written down because the alternative is that a "temporary" duplicate of the status logic
    quietly becomes a second source of truth.
    """

    OK = "ok"
    OCCLUDED = "occluded"
    """Low visibility, high presence: the model believes the point is in frame but cannot see it."""
    OUT_OF_FRAME = "out_of_frame"
    LOW_CONFIDENCE = "low_confidence"


def _display_status(landmark: Landmark) -> DisplayStatus:
    if landmark.presence < _CONFIDENT:
        return DisplayStatus.OUT_OF_FRAME
    if landmark.visibility < _CONFIDENT:
        return DisplayStatus.OCCLUDED
    if landmark.visibility < 0.8:
        return DisplayStatus.LOW_CONFIDENCE
    return DisplayStatus.OK


@dataclass(frozen=True, slots=True)
class _Options:
    image: Path | None
    backend: str
    preset: str
    model_path: Path | None
    as_json: bool


def _parse_args(argv: Sequence[str] | None) -> _Options:
    parser = argparse.ArgumentParser(
        prog="python -m pose_backends.cli",
        description="Run an image through a pose backend and print the landmarks it produced.",
        epilog=(
            "Exit codes: 0 landmarks printed, 1 no pose detected, 2 the backend could not run. "
            "The image argument is optional only for --backend fake, which never reads it."
        ),
    )
    parser.add_argument(
        "image",
        nargs="?",
        type=Path,
        help="path to an image. Omit only with --backend fake.",
    )
    parser.add_argument(
        "--backend",
        default=MEDIAPIPE_NAME,
        choices=[MEDIAPIPE_NAME, FAKE_NAME],
        help=f"which backend to run (default: {MEDIAPIPE_NAME}).",
    )
    parser.add_argument(
        "--preset",
        default=PosePreset.STRAIGHT.value,
        choices=[preset.value for preset in PosePreset],
        help="scenario for --backend fake (default: straight).",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=None,
        help="override the model location. Falls back to $MODEL_PATH, then models/.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit machine-readable JSON instead of a table.",
    )

    parsed = parser.parse_args(argv)
    return _Options(
        image=parsed.image,
        backend=parsed.backend,
        preset=parsed.preset,
        model_path=parsed.model_path,
        as_json=parsed.as_json,
    )


def _load_image(path: Path) -> ImageBGR:
    """Decode with OpenCV, which returns BGR — the channel order the Protocol specifies."""
    if not path.is_file():
        raise PoseBackendError(f"no such image: {path}")

    try:
        import cv2
    except ImportError as exc:
        raise PoseBackendError(
            "reading an image needs OpenCV, which arrives with the mediapipe extra. "
            "Install `pose-backends[mediapipe]`, or use --backend fake with no image."
        ) from exc

    import numpy as np

    decoded = cv2.imread(str(path))
    if decoded is None:
        raise PoseBackendError(f"{path} is not an image OpenCV can decode.")
    return np.asarray(decoded, dtype=np.uint8)


def _blank_image() -> ImageBGR:
    """What the fake backend gets. It ignores the contents entirely."""
    import numpy as np

    return np.zeros((480, 640, 3), dtype=np.uint8)


def _image_for(options: _Options) -> ImageBGR:
    """Decode the image, unless the chosen backend has no use for one.

    ``FakePoseBackend`` ignores pixels by contract, so decoding for it would import OpenCV — and
    OpenCV arrives only with the mediapipe extra. That would break the stated promise that
    ``--backend fake`` runs on the base package alone, and it would break it *only* when a path
    happened to be supplied, which is a miserable thing to debug.

    A path that was given is still checked for existence, so ``no such image`` still fires and the
    argument is not silently ignored.
    """
    if options.image is None:
        return _blank_image()
    if options.backend == FAKE_NAME:
        if not options.image.is_file():
            raise PoseBackendError(f"no such image: {options.image}")
        return _blank_image()
    return _load_image(options.image)


def _as_dict(frame: PoseFrame) -> dict[str, object]:
    """The versioned JSON shape. Keys are ordered for a readable diff, not for parsing."""
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "backend": frame.backend,
        "inference_ms": round(frame.inference_ms, 3),
        "image": {"width": frame.image_width, "height": frame.image_height},
        "landmark_count": len(frame),
        "canonical_count": len(KeypointName),
        # Every canonical keypoint, always, in name order — including the ones this backend did
        # not report, which appear with null coordinates and a `not_reported` status.
        #
        # A key set that varied with what was detected would make two captures of the same image
        # diff noisily, and would make "this backend stopped reporting a left heel" look identical
        # to "this key was never in the format". The table already renders all 34 rows for exactly
        # that reason; the machine-readable form should not be the weaker of the two, since it is
        # the one Epic H compares against the legacy baseline.
        "landmarks": {
            name.value: _landmark_dict(frame.get(name))
            for name in sorted(KeypointName, key=lambda item: item.value)
        },
    }


def _landmark_dict(landmark: Landmark | None) -> dict[str, object]:
    """One landmark's JSON shape, or the null-filled shape for one that was not reported."""
    if landmark is None:
        return {
            "x": None,
            "y": None,
            "x_world": None,
            "y_world": None,
            "z_world": None,
            "visibility": None,
            "presence": None,
            "status": NOT_REPORTED,
        }
    return {
        "x": round(landmark.x, 6),
        "y": round(landmark.y, 6),
        "x_world": None if landmark.x_world is None else round(landmark.x_world, 6),
        "y_world": None if landmark.y_world is None else round(landmark.y_world, 6),
        "z_world": None if landmark.z_world is None else round(landmark.z_world, 6),
        "visibility": round(landmark.visibility, 4),
        "presence": round(landmark.presence, 4),
        "status": _display_status(landmark).value,
    }


_COLUMNS: Final = (
    ("keypoint", 18),
    ("x", 9),
    ("y", 9),
    ("x_world", 10),
    ("y_world", 10),
    ("z_world", 10),
    ("vis", 7),
    ("pres", 7),
    ("status", 14),
)


def _format_table(frame: PoseFrame) -> str:
    """A fixed-width table, hand-rolled.

    No `rich`, no `tabulate`. This package is installed inside the API image, and a dependency
    added for the sake of a developer tool would ship to production for the rest of the project's
    life. Nine columns of `str.ljust` is not worth that.
    """
    header = "".join(title.ljust(width) for title, width in _COLUMNS).rstrip()
    lines = [header, "-" * len(header)]

    for name in sorted(KeypointName, key=lambda item: item.value):
        landmark = frame.get(name)
        if landmark is None:
            # Reported as a row rather than skipped: "this backend did not give us a left heel" is
            # exactly the kind of thing you are running this command to find out.
            lines.append(name.value.ljust(_COLUMNS[0][1]) + "not reported")
            continue

        def number(value: float | None, width: int, places: int = 4) -> str:
            return ("-" if value is None else f"{value:.{places}f}").ljust(width)

        lines.append(
            "".join(
                (
                    name.value.ljust(_COLUMNS[0][1]),
                    number(landmark.x, _COLUMNS[1][1]),
                    number(landmark.y, _COLUMNS[2][1]),
                    number(landmark.x_world, _COLUMNS[3][1]),
                    number(landmark.y_world, _COLUMNS[4][1]),
                    number(landmark.z_world, _COLUMNS[5][1]),
                    number(landmark.visibility, _COLUMNS[6][1], places=2),
                    number(landmark.presence, _COLUMNS[7][1], places=2),
                    _display_status(landmark).value,
                )
            ).rstrip()
        )

    lines.extend(
        (
            "",
            f"backend        {frame.backend}",
            f"image          {frame.image_width}x{frame.image_height}",
            f"inference      {frame.inference_ms:.1f} ms",
            f"landmarks      {len(frame)} of {len(KeypointName)}",
            f"world space    {'yes' if frame.has_world_landmarks else 'no'}",
        )
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns an exit code rather than calling ``sys.exit``, so it is testable."""
    options = _parse_args(argv)

    if options.image is None and options.backend != FAKE_NAME:
        print(f"error: an image is required for --backend {options.backend}.", file=sys.stderr)
        return EXIT_ERROR

    try:
        backend = create_backend(
            options.backend,
            model_path=options.model_path,
            preset=options.preset,
        )
        image = _image_for(options)
        frame = backend.detect(image)
    except PoseBackendError as exc:
        # Only our own exception type. A narrow catch of something we defined still lets a genuine
        # programming error escape with its traceback intact — which is the entire difference
        # between this and the `except Exception` that made the legacy engine impossible to debug.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if frame is None:
        source = options.image if options.image is not None else f"the {options.preset} preset"
        print(f"No pose detected in {source}.", file=sys.stderr)
        return EXIT_NO_POSE

    if options.as_json:
        print(json.dumps(_as_dict(frame), indent=2))
    else:
        print(_format_table(frame))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
