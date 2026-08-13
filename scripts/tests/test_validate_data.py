"""Proves validate_data.py both passes the real dataset and fails loudly on a corrupted copy —
the acceptance criterion OP-38 states explicitly, not just "no exceptions raised"."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from validate_data import (
    DEFAULT_BASELINE,
    DEFAULT_GATES,
    DEFAULT_IMAGES_DIR,
    DEFAULT_MANIFEST,
    _load_gates,
    _load_manifest,
    validate,
)

REAL_ROWS = _load_manifest(DEFAULT_MANIFEST)
GATES = _load_gates(DEFAULT_GATES)
BASELINE = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))


def test_the_real_dataset_passes() -> None:
    assert validate(REAL_ROWS, GATES, DEFAULT_IMAGES_DIR, BASELINE) == []


def test_a_blank_consent_field_fails() -> None:
    rows = [dict(r) for r in REAL_ROWS]
    rows[0]["consent"] = ""
    errors = validate(rows, GATES, DEFAULT_IMAGES_DIR, BASELINE)
    assert any("consent" in e and "blank" in e for e in errors)


def test_a_duplicate_hash_fails() -> None:
    rows = [dict(r) for r in REAL_ROWS]
    rows[1]["sha256"] = rows[0]["sha256"]
    errors = validate(rows, GATES, DEFAULT_IMAGES_DIR, BASELINE)
    assert any("duplicate sha256" in e for e in errors)


def test_a_tampered_declared_dimension_fails() -> None:
    rows = [dict(r) for r in REAL_ROWS]
    rows[0]["width"] = "9999"
    errors = validate(rows, GATES, DEFAULT_IMAGES_DIR, BASELINE)
    assert any("manifest says 9999x1280" in e for e in errors)


def test_a_corrupt_image_file_fails(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    shutil.copytree(DEFAULT_IMAGES_DIR, images_dir)
    (images_dir / REAL_ROWS[0]["filename"]).write_bytes(b"not a real jpeg")

    errors = validate(REAL_ROWS, GATES, images_dir, BASELINE)
    assert any("could not be opened as an image" in e for e in errors)


def test_gps_exif_fails(tmp_path: Path) -> None:
    images_dir = tmp_path / "images"
    shutil.copytree(DEFAULT_IMAGES_DIR, images_dir)

    target = images_dir / REAL_ROWS[0]["filename"]
    with Image.open(target) as im:
        exif = im.getexif()
        exif[0x8825] = {1: "N", 2: (10, 0, 0), 3: "W", 4: (20, 0, 0)}  # GPSInfo IFD
        im.save(target, exif=exif)

    errors = validate(REAL_ROWS, GATES, images_dir, BASELINE)
    assert any("GPS EXIF" in e for e in errors)


def test_a_public_licence_without_consent_fails() -> None:
    rows = [dict(r) for r in REAL_ROWS]
    rows[0]["licence"] = "cc0"
    errors = validate(rows, GATES, DEFAULT_IMAGES_DIR, BASELINE)
    assert any("claims redistribution rights" in e for e in errors)


def test_an_orphaned_manifest_row_fails() -> None:
    rows = [dict(r) for r in REAL_ROWS]
    rows[0]["filename"] = "does-not-exist.jpg"
    errors = validate(rows, GATES, DEFAULT_IMAGES_DIR, BASELINE)
    assert any("does not exist (orphaned manifest row)" in e for e in errors)


@pytest.mark.parametrize("label", ["not-a-real-posture", ""])
def test_an_unrecognised_posture_label_fails(label: str) -> None:
    rows = [dict(r) for r in REAL_ROWS]
    rows[0]["posture_label"] = label
    errors = validate(rows, GATES, DEFAULT_IMAGES_DIR, BASELINE)
    assert any("posture_label" in e for e in errors)
