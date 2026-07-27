"""Checks on the model-acquisition contract itself.

Most of these need no model file: they assert that the *pins* are well-formed and that the
Makefile and the Python default agree about where weights live. Those are cheap and run on every
pull request, because a checksum file the tooling cannot parse is a checksum file that silently
verifies nothing — and that failure would only surface the day someone shipped a bad model.

The one test that needs actual weights is marked `pytest.mark.model`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from pose_backends.registry import DEFAULT_MODEL_PATH, default_model_path

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKSUMS = REPO_ROOT / "models" / "checksums.txt"
MAKEFILE = REPO_ROOT / "Makefile"

CHECKSUM_LINE = re.compile(r"^(?P<sha>[0-9a-f]{64})  (?P<name>\S+)$")


def pins() -> dict[str, str]:
    """Parse `checksums.txt` the same way the Makefile's grep does: filename -> sha256."""
    parsed: dict[str, str] = {}
    for line in CHECKSUMS.read_text().splitlines():
        match = CHECKSUM_LINE.match(line)
        if match:
            parsed[match["name"]] = match["sha"]
    return parsed


def test_all_three_variants_are_pinned() -> None:
    """lite, full and heavy. Documented as swappable, so all three must actually be fetchable."""
    assert set(pins()) == {
        "pose_landmarker_lite.task",
        "pose_landmarker_full.task",
        "pose_landmarker_heavy.task",
    }


def test_every_pin_is_a_full_length_sha256() -> None:
    """A truncated hash is the failure this file exists to prevent, so it must not be possible
    to write one here and have the tooling shrug."""
    for name, sha in pins().items():
        assert len(sha) == 64, name


def test_the_python_default_and_the_makefile_target_are_the_same_file() -> None:
    """Two places name the model path, and they must not drift.

    `make fetch-model` writes to `models/pose_landmarker_$(MODEL_VARIANT).task`; the API loads
    `DEFAULT_MODEL_PATH` when `MODEL_PATH` is unset. If those disagreed, fetching the model would
    appear to work and the backend would still report the file missing.
    """
    makefile = MAKEFILE.read_text()
    assert "MODEL_VARIANT ?= full" in makefile
    assert "MODEL_DIR     ?= models" in makefile
    assert Path("models/pose_landmarker_full.task") == DEFAULT_MODEL_PATH


def test_the_makefile_fetches_the_versioned_url_not_latest() -> None:
    """`/1/` and `/latest/` are different files — four bytes apart, different hashes.

    Pinning a checksum against a URL whose contents can be republished is a pin in name only. The
    discrepancy is recorded in checksums.txt; this keeps the Makefile on the right side of it.
    """
    makefile = MAKEFILE.read_text()
    assert "/float16/1/" in makefile
    assert "/float16/latest/" not in makefile


def test_the_makefile_does_not_trust_shasum_check_mode() -> None:
    """`shasum -c` exits 0 on a line it could not parse, so a typo'd pin would "pass".

    Asserted rather than merely commented because the obvious "simplification" during a future
    cleanup is to replace the explicit comparison with `shasum -c`, which would quietly remove
    the guarantee while looking tidier.
    """
    makefile = MAKEFILE.read_text()
    assert "shasum -a 256 -c" not in makefile
    assert "-c $(CHECKSUMS)" not in makefile


def test_model_path_environment_variable_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_PATH", "/opt/models/pose_landmarker_lite.task")
    assert default_model_path() == Path("/opt/models/pose_landmarker_lite.task")


def test_weights_are_not_committed() -> None:
    """The legacy project committed a 209 MB `model.h5`, could not push it, and resorted to a
    Dropbox link. `make fetch-model` plus a pinned hash is the replacement; a `.task` appearing in
    the tree means someone bypassed it."""
    ignored = (REPO_ROOT / ".gitignore").read_text()
    assert "models/*.task" in ignored


@pytest.mark.model
def test_the_model_on_disk_matches_its_pin() -> None:
    """The real integrity check, run by model-validation.yml before anything else (OP-21).

    Reads in chunks rather than slurping: `heavy` is 29 MB and there is no reason to hold it all
    in memory to hash it.
    """
    path = default_model_path()
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.is_file():
        pytest.skip(f"no model at {path} — run `make fetch-model`")

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    expected = pins().get(path.name)
    assert expected is not None, f"{path.name} has no pinned checksum in {CHECKSUMS}"
    assert digest.hexdigest() == expected
