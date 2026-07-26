"""Capture the inherited engine's verdicts on the curated fixtures (OP-11).

Run ONCE, before API/env and model.h5 are deleted. Produces docs/archive/legacy-baseline.json,
which Epic H (OP-115) compares the new engine against.

This harness deliberately does not fix the engine. It works around exactly two defects that
make it non-runnable as a library, both documented in docs/FINDINGS.md:

  * FINDINGS 3.3 - process() reads a module-global `model` only ever assigned inside
    `if __name__ == '__main__'`, so importing it raises NameError. We inject the global.
  * FINDINGS 3.4 - config_reader() casts scale_search with map(), a one-shot iterator, so
    reusing one params dict yields an empty multiplier and an IndexError. We re-read per image.

Everything else - the single-scale/divide-by-4 heatmap mismatch, the inverted ear indices, the
tautological feet check - is left exactly as-is. The point is to record what the old engine
actually said, not what it should have said.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEGACY = REPO / "docs" / "archive" / "legacy-openpose"
FIXTURES = REPO / "fixtures" / "images"
WEIGHTS = REPO / "API" / "model" / "keras" / "model.h5"
OUT = REPO / "docs" / "archive" / "legacy-baseline.json"

# config_reader() does ConfigObj('config') with a relative path, and posture_image does
# cv2.imread on whatever it is handed. Run from the legacy directory so 'config' resolves.
os.chdir(LEGACY)
sys.path.insert(0, str(LEGACY))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import posture_image as legacy  # noqa: E402
from config_reader import config_reader  # noqa: E402
from model import get_testing_model  # noqa: E402

# FINDINGS 2.5: checkPosition returns None on failure and __main__ maps that to
# "Straight back position". Reproduce that mapping faithfully so the baseline records the
# silent false negative rather than hiding it.
POSITION_LABEL = {1: "Hunchback position", -1: "Reclined back position", 0: "Straight back position"}


def _capture(fn, *args):
    """Call a legacy check function, capturing what it prints and what it returns.

    These functions report results via bare print() (FINDINGS 3.6), so stdout *is* the result
    for three of the five metrics.
    """
    buf = io.StringIO()
    err = None
    val = None
    try:
        with contextlib.redirect_stdout(buf):
            val = fn(*args)
    except Exception as exc:  # noqa: BLE001 - recording crashes is the point
        err = f"{type(exc).__name__}: {exc}"
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    return {"printed": lines, "returned": val, "error": err}


def extract_peaks(image_path: str, params, model_params, model):
    """The peak-extraction half of legacy process(), verbatim, minus the draw()/print side effects."""
    ori = cv2.imread(image_path)
    if ori is None:
        raise FileNotFoundError(image_path)
    multiplier = [x * model_params["boxsize"] / ori.shape[0] for x in params["scale_search"]]
    heatmap_avg = np.zeros((ori.shape[0], ori.shape[1], 19))

    # NOTE: range(1) with division by len(multiplier)=4 is FINDINGS 3.1, the quarter-magnitude
    # heatmap bug. Preserved deliberately.
    for m in range(1):
        scale = multiplier[m]
        img = cv2.resize(ori, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        padded, pad = legacy.util.padRightDownCorner(img, model_params["stride"], model_params["padValue"])
        inp = np.transpose(np.float32(padded[:, :, :, np.newaxis]), (3, 0, 1, 2))
        out = model.predict(inp, verbose=0)
        hm = np.squeeze(out[1])
        hm = cv2.resize(hm, (0, 0), fx=model_params["stride"], fy=model_params["stride"], interpolation=cv2.INTER_CUBIC)
        hm = hm[: padded.shape[0] - pad[2], : padded.shape[1] - pad[3], :]
        hm = cv2.resize(hm, (ori.shape[1], ori.shape[0]), interpolation=cv2.INTER_CUBIC)
        heatmap_avg = heatmap_avg + hm / len(multiplier)

    from scipy.ndimage import gaussian_filter

    all_peaks = []
    counter = 0
    for part in range(18):
        map_ori = heatmap_avg[:, :, part]
        mp = gaussian_filter(map_ori, sigma=3)
        left, right, up, down = (np.zeros(mp.shape) for _ in range(4))
        left[1:, :] = mp[:-1, :]
        right[:-1, :] = mp[1:, :]
        up[:, 1:] = mp[:, :-1]
        down[:, :-1] = mp[:, 1:]
        binary = np.logical_and.reduce((mp >= left, mp >= right, mp >= up, mp >= down, mp > params["thre1"]))
        peaks = list(zip(np.nonzero(binary)[1], np.nonzero(binary)[0]))
        scored = [p + (map_ori[p[1], p[0]],) for p in peaks]
        ids = range(counter, counter + len(peaks))
        all_peaks.append([scored[i] + (ids[i],) for i in range(len(ids))])
        counter += len(peaks)
    return all_peaks, ori.shape


PART_STR = ["nose", "neck", "Rsho", "Relb", "Rwri", "Lsho", "Lelb", "Lwri", "Rhip", "Rkne",
            "Rank", "Lhip", "Lkne", "Lank", "Leye", "Reye", "Lear", "Rear"]


def main() -> int:
    if not WEIGHTS.exists():
        print(f"FATAL: weights missing at {WEIGHTS}", file=sys.stderr)
        return 1
    images = sorted(FIXTURES.glob("*.jpg")) + sorted(FIXTURES.glob("*.jpeg"))
    if not images:
        print(f"FATAL: no fixtures in {FIXTURES}", file=sys.stderr)
        return 1

    print(f"Loading legacy model ({WEIGHTS.stat().st_size / 1e6:.0f} MB)...", flush=True)
    t0 = time.time()
    model = get_testing_model()
    model.load_weights(str(WEIGHTS))
    legacy.model = model  # FINDINGS 3.3
    load_s = time.time() - t0
    print(f"  loaded in {load_s:.1f}s, {model.count_params():,} params", flush=True)

    results = {}
    for img in sorted(images):
        print(f"\n=== {img.name} ===", flush=True)
        params, model_params = config_reader()  # FINDINGS 3.4: re-read per image
        t = time.time()
        try:
            peaks, shape = extract_peaks(str(img), params, model_params, model)
        except Exception as exc:  # noqa: BLE001
            results[img.name] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"  FAILED: {exc}", flush=True)
            continue
        infer_s = time.time() - t

        detected = {PART_STR[i]: len(peaks[i]) for i in range(18)}
        n_detected = sum(1 for v in detected.values() if v)

        # First peak per part - this is the only one any downstream check ever reads
        # (FINDINGS 3.2: the PAF limb-grouping stage was never implemented).
        first_peak = {
            PART_STR[i]: ([int(peaks[i][0][0]), int(peaks[i][0][1]), round(float(peaks[i][0][2]), 4)] if peaks[i] else None)
            for i in range(18)
        }

        position = _capture(legacy.checkPosition, peaks)
        kneeling = _capture(legacy.checkKneeling, peaks)
        handfold = _capture(legacy.checkHandFold, peaks)
        neck = _capture(legacy.evaluate_neck_posture, peaks)
        feet = _capture(legacy.evaluate_feet_position, peaks)

        # Reproduce __main__'s mapping, including None -> "Straight back position"
        verdict = POSITION_LABEL.get(position["returned"], "Straight back position")
        silent_fn = position["returned"] is None

        results[img.name] = {
            "image_size": [int(shape[1]), int(shape[0])],
            "inference_seconds": round(infer_s, 2),
            "keypoints_detected": n_detected,
            "peaks_per_part": detected,
            "first_peak_xy_score": first_peak,
            "spine": {
                "raw_return": position["returned"],
                "reported_verdict": verdict,
                "silent_false_negative": silent_fn,
                "stdout": position["printed"],
                "error": position["error"],
            },
            "kneeling": kneeling,
            "hand_fold": handfold,
            "neck": {"verdict": neck["returned"], "stdout": neck["printed"], "error": neck["error"]},
            "feet": {"verdict": feet["returned"], "stdout": feet["printed"], "error": feet["error"]},
        }
        print(f"  {n_detected}/18 parts, {infer_s:.1f}s", flush=True)
        print(f"  spine   : {verdict}{'   <-- SILENT FALSE NEGATIVE (checkPosition returned None)' if silent_fn else ''}", flush=True)
        print(f"  kneeling: {kneeling['printed'] or kneeling['error']}", flush=True)
        print(f"  hands   : {handfold['printed'] or handfold['error']}", flush=True)
        print(f"  neck    : {neck['returned']}", flush=True)
        print(f"  feet    : {feet['returned']}", flush=True)

    def _git(*a):
        try:
            return subprocess.check_output(["git", *a], cwd=REPO, text=True).strip()
        except Exception:  # noqa: BLE001
            return None

    doc = {
        "_comment": (
            "Verdicts produced by the ORIGINAL inherited OpenPose engine on the curated fixture "
            "set, captured in OP-11 before API/env and model.h5 were deleted. This is the 'before' "
            "half of the old-vs-new evaluation in docs/evaluation.md (OP-115). The defects visible "
            "here are catalogued in docs/FINDINGS.md and were NOT corrected before capture."
        ),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "captured_at_commit": _git("rev-parse", "HEAD"),
        "engine": {
            "name": "CMU OpenPose (TF/Keras reimplementation), inherited",
            "source": "docs/archive/legacy-openpose/posture_image.py",
            "weights_sha256_prefix": None,
            "weights_bytes": WEIGHTS.stat().st_size,
            "parameters": int(model.count_params()),
            "model_load_seconds": round(load_s, 1),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.machine()}",
            "tensorflow": None,
            "numpy": np.__version__,
            "opencv": cv2.__version__,
        },
        "known_defects_present": [
            "FINDINGS 2.1 ear indices inverted (laterality)",
            "FINDINGS 2.2 neck metric compares y-coordinates, not sagittal offset",
            "FINDINGS 2.3 feet check is a tautology",
            "FINDINGS 2.5 checkPosition None -> 'Straight back position'",
            "FINDINGS 2.6 unnormalized +/-100 px hand-fold threshold",
            "FINDINGS 3.1 single scale iterated but divided by len(multiplier)=4",
        ],
        "results": results,
    }

    import hashlib

    h = hashlib.sha256()
    with open(WEIGHTS, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    doc["engine"]["weights_sha256_prefix"] = h.hexdigest()[:16]

    try:
        import importlib.metadata as md

        doc["environment"]["tensorflow"] = md.version("tensorflow-macos")
    except Exception:  # noqa: BLE001
        pass

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"\nWrote {OUT.relative_to(REPO)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
