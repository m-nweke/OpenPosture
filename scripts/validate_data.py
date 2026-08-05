"""Check the evaluation dataset against evaluation/quality-gates.yml (OP-38).

The dataset is a versioned artifact with its own quality gates, not a folder of images someone
curated once and never checked again — that is how the inherited project ended up with 43MB of
near-duplicate, unlabelled, unlicensed sample images (OP-11). `validate()` is the pure function
that does the checking; `main()` is the thin CLI wrapper CI and `make validate-data` call.

    uv run python scripts/validate_data.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "evaluation" / "manifest.csv"
DEFAULT_GATES = REPO_ROOT / "evaluation" / "quality-gates.yml"
DEFAULT_BASELINE = REPO_ROOT / "evaluation" / "baseline.json"
DEFAULT_IMAGES_DIR = REPO_ROOT / "fixtures" / "images"

# The EXIF tag PIL uses for the GPSInfo IFD. Its presence at all is the leak — the ticket's
# concern is location data attached to photographs of people, not the value underneath it.
_EXIF_GPS_TAG = 34853


def _load_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _load_gates(path: Path) -> dict[str, Any]:
    gates: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return gates


def validate(
    rows: list[dict[str, str]],
    gates: dict[str, Any],
    images_dir: Path,
    baseline: dict[str, Any] | None,
) -> list[str]:
    """Return every violation found. An empty list means the dataset passes."""
    errors: list[str] = []

    errors.extend(_check_manifest_shape(rows, gates["manifest"]))
    errors.extend(_check_images(rows, gates["images"], gates["privacy"], images_dir))
    errors.extend(_check_labels(rows, gates["labels"]))
    errors.extend(_check_redistribution(rows, gates["redistribution"]))
    errors.extend(_check_split_leakage(rows))
    if baseline is not None:
        errors.extend(_check_class_distribution(rows, baseline, gates["class_distribution"]))

    return errors


def _check_manifest_shape(rows: list[dict[str, str]], rules: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = rules["required_columns"]
    if rows:
        missing_columns = [c for c in required if c not in rows[0]]
        if missing_columns:
            errors.append(f"manifest is missing required columns: {missing_columns}")
            return errors  # every other check assumes these columns exist

    seen_ids: dict[str, int] = {}
    seen_hashes: dict[str, int] = {}
    for i, row in enumerate(rows):
        for field in rules["no_blank_fields"]:
            if not row.get(field, "").strip():
                errors.append(f"row {i} ({row.get('id', '?')}): '{field}' is blank")

        if not rules["allow_duplicate_ids"] and row["id"] in seen_ids:
            errors.append(
                f"row {i}: duplicate id '{row['id']}' (first seen at row {seen_ids[row['id']]})"
            )
        seen_ids.setdefault(row["id"], i)

        digest = row["sha256"]
        if not rules["allow_duplicate_hashes"] and digest in seen_hashes:
            errors.append(
                f"row {i} ({row['id']}): duplicate sha256 (matches row {seen_hashes[digest]}) — "
                "the same image counted twice inflates the fixture set without adding evidence"
            )
        seen_hashes.setdefault(digest, i)

    return errors


def _check_images(
    rows: list[dict[str, str]],
    image_rules: dict[str, Any],
    privacy_rules: dict[str, Any],
    images_dir: Path,
) -> list[str]:
    errors: list[str] = []
    allowed_formats = set(image_rules["allowed_formats"])

    for row in rows:
        image_path = images_dir / row["filename"]
        if not image_path.is_file():
            errors.append(f"{row['id']}: {image_path} does not exist (orphaned manifest row)")
            continue

        actual_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
        if actual_hash != row["sha256"]:
            errors.append(
                f"{row['id']}: sha256 mismatch — manifest says {row['sha256']}, "
                f"file is actually {actual_hash}"
            )

        try:
            with Image.open(image_path) as im:
                width, height = im.size
                image_format = im.format
                if privacy_rules.get("reject_gps_exif"):
                    exif = im.getexif()
                    if _EXIF_GPS_TAG in exif:
                        errors.append(
                            f"{row['id']}: carries GPS EXIF data — this is a photograph of a "
                            "person and must not leak where it was taken"
                        )
        except Exception as exc:  # noqa: BLE001 - any decode failure means "corrupt", full stop
            errors.append(f"{row['id']}: {image_path} could not be opened as an image ({exc})")
            continue

        if image_format not in allowed_formats:
            errors.append(f"{row['id']}: format {image_format} not in {sorted(allowed_formats)}")
        if width < image_rules["min_width"] or height < image_rules["min_height"]:
            errors.append(
                f"{row['id']}: {width}x{height} is smaller than the "
                f"{image_rules['min_width']}x{image_rules['min_height']} floor"
            )
        if max(width, height) > image_rules["max_long_edge"]:
            errors.append(
                f"{row['id']}: long edge {max(width, height)}px exceeds "
                f"{image_rules['max_long_edge']}px"
            )

        declared_width, declared_height = int(row["width"]), int(row["height"])
        if (declared_width, declared_height) != (width, height):
            errors.append(
                f"{row['id']}: manifest says {declared_width}x{declared_height}, "
                f"file is actually {width}x{height}"
            )

    return errors


def _check_labels(rows: list[dict[str, str]], label_rules: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed_postures = set(label_rules["allowed_posture_labels"])
    allowed_views = set(label_rules["allowed_views"])
    for row in rows:
        if row["posture_label"] not in allowed_postures:
            errors.append(f"{row['id']}: posture_label '{row['posture_label']}' is not recognised")
        if row["view"] not in allowed_views:
            errors.append(f"{row['id']}: view '{row['view']}' is not recognised")
    return errors


def _check_redistribution(rows: list[dict[str, str]], rules: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    public_licences = set(rules["public_licence_values"])
    blocking_consent = set(rules["consent_values_blocking_public_licence"])
    for row in rows:
        if row["licence"] in public_licences and row["consent"] in blocking_consent:
            errors.append(
                f"{row['id']}: licence '{row['licence']}' claims redistribution rights, but "
                f"consent is '{row['consent']}' — cannot ship undocumented-consent images as public"
            )
    return errors


def _check_split_leakage(rows: list[dict[str, str]]) -> list[str]:
    """No id or hash may appear under more than one `split` value.

    There is only one split today (`fixture`), so this always passes — it exists for when a real
    train/eval split is introduced and someone needs the same fixture in both by mistake to be
    caught immediately rather than discovered as an inflated eval score.
    """
    errors: list[str] = []
    splits_by_hash: dict[str, set[str]] = {}
    for row in rows:
        splits_by_hash.setdefault(row["sha256"], set()).add(row["split"])
    for digest, splits in splits_by_hash.items():
        if len(splits) > 1:
            errors.append(
                f"image with hash {digest} appears in more than one split: {sorted(splits)}"
            )
    return errors


def _label_share(labels: list[str]) -> dict[str, float]:
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    total = len(labels)
    return {label: count / total for label, count in counts.items()}


def _check_class_distribution(
    rows: list[dict[str, str]], baseline: dict[str, Any], rules: dict[str, Any]
) -> list[str]:
    """Compare posture_label share across the WHOLE set on each side, not just the fixtures the
    two sides have in common — an added or removed fixture should be able to move the needle,
    since that is the entire point of tracking distribution over time."""
    baseline_share = _label_share([f["posture_label"] for f in baseline["fixtures"].values()])
    current_share = _label_share([row["posture_label"] for row in rows])
    max_drift = rules["max_share_drift"]

    errors: list[str] = []
    for label in set(baseline_share) | set(current_share):
        before, after = baseline_share.get(label, 0.0), current_share.get(label, 0.0)
        drift = abs(after - before)
        if drift > max_drift:
            errors.append(
                f"class distribution for '{label}' drifted by {drift:.2f} "
                f"(baseline {before:.2f} -> current {after:.2f}), "
                f"exceeding max_share_drift {max_drift}"
            )
    return errors


def main(argv: list[str]) -> int:
    del argv  # no flags yet — every path has a sensible repo-relative default
    rows = _load_manifest(DEFAULT_MANIFEST)
    gates = _load_gates(DEFAULT_GATES)
    baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))

    errors = validate(rows, gates, DEFAULT_IMAGES_DIR, baseline)

    if errors:
        print(f"validate-data: {len(errors)} problem(s) found:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"validate-data: OK ({len(rows)} fixtures checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
