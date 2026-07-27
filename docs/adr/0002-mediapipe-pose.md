# ADR-0002 — MediaPipe Pose Landmarker, not the CMU Keras model

**Status:** Accepted
**Date:** 2026-07-26
**Ticket:** OP-15, recording the OP-16 (B1) spike results — spike executed and this ADR corrected
under OP-16 on 2026-07-26
**Supersedes nothing. Descopes OP-22 (B7, ONNX MoveNet fallback) — see "The gated risk".**

This is the platform bet the whole pipeline rests on, so it is recorded in more detail than the
other ADRs.

## Context

The original project used a Keras reimplementation of CMU OpenPose: a 209 MB `model.h5` with
~52.3 M parameters, distributed as a bare Dropbox link. The code around it has three separate
disqualifying problems (FINDINGS §3):

- Heatmaps come out at a quarter of the expected magnitude, so peak detection is tuned against an
  artefact.
- The multi-person association stage is absent entirely.
- Neither `process()` function is importable — both are defined inside `if __name__ == "__main__"`
  scope in the sense that matters: they depend on module-level globals built at import time from a
  cwd-relative config.

Replacing the model was therefore not optional. The question was what to replace it with.

## Decision

**MediaPipe Pose Landmarker**, pinned to **`mediapipe==0.10.18`**, behind the `PoseBackend`
Protocol in `packages/pose-backends`.

| | Keras OpenPose (legacy) | MediaPipe Pose Landmarker |
| --- | --- | --- |
| Model size | 209 MB, Dropbox-hosted | 9 MB (`pose_landmarker_full.task`) |
| Parameters | 52.3 M | ~3 M |
| CPU latency | seconds | ~20–35 ms |
| Python support | 3.11 only, no arm64 wheel | 3.9–3.12 |
| Licence | code MIT; **weights unattributed** | Apache-2.0 |
| Keypoints | 18 (COCO), x/y | 33, x/y/z, incl. heel and foot index |
| Per-point confidence | none | `visibility` **and** `presence`, both [0,1] |
| Metric 3D | none | `pose_world_landmarks`, metres, hip-origin |

Three wins decided it, in order of weight.

**1. World landmarks give scale invariance for free.** `pose_world_landmarks` are in metres with
the hip midpoint as origin, independent of image resolution and subject distance. The original
code's central defect — thresholds in raw pixels, so identical posture at twice the distance gets a
different verdict (FINDINGS §2.6) — stops being a normalization problem to solve and becomes
*"compute the angle in world space."* No competing backend offers this. See
[ADR-0005](0005-scale-invariant-metrics.md).

**2. `visibility` and `presence` make missing data expressible.** Two independent signals
distinguish *occluded* (low visibility, high presence) from *out of frame* (low presence). That is
what lets the API say "I couldn't assess your knees" instead of the original's silent
"Straight back position" whenever assessment failed (FINDINGS §2.5) — the single most damaging
behaviour in the inherited system.

**3. Feet exist.** `LEFT_HEEL(29)`, `RIGHT_HEEL(30)`, `LEFT_FOOT_INDEX(31)`, `RIGHT_FOOT_INDEX(32)`
make heel contact a real measurement. The 18-point COCO schema simply had no foot landmarks, which
is why the original's feet check was a tautology that returned "on the floor" for a fixture whose
subject's feet are visibly dangling (FINDINGS §2.3). The project's own README listed this as a
goal and never delivered it.

On licensing, precisely: the legacy *code* is MIT (Copyright (c) 2020 Vinay Varma, retained at
`docs/archive/legacy-openpose/LICENSE`). It is the *weights* whose provenance cannot be
established — a Dropbox URL with no licence, no checksum and no stated origin
(`docs/archive/legacy-openpose/model-weights-readme.md`). For a public MIT repository that is
disqualifying on its own.

## Keypoint mapping, legacy COCO-18 → MediaPipe 33

The legacy authoritative order, from `API/config`:

```
part_str = [nose, neck, Rsho, Relb, Rwri, Lsho, Lelb, Lwri, Rhip, Rkne,
            Rank, Lhip, Lkne, Lank, Leye, Reye, Lear, Rear, pt19]
```

| Legacy | Idx | MediaPipe | Idx | Note |
| --- | --- | --- | --- | --- |
| `nose` | 0 | `NOSE` | 0 | |
| `neck` | 1 | *(derived)* | — | **See below — the one real schema change.** |
| `Rsho` | 2 | `RIGHT_SHOULDER` | 12 | |
| `Lsho` | 5 | `LEFT_SHOULDER` | 11 | |
| `Relb` | 3 | `RIGHT_ELBOW` | 14 | |
| `Lelb` | 6 | `LEFT_ELBOW` | 13 | |
| `Rwri` | 4 | `RIGHT_WRIST` | 16 | |
| `Lwri` | 7 | `LEFT_WRIST` | 15 | |
| `Rhip` | 8 | `RIGHT_HIP` | 24 | |
| `Lhip` | 11 | `LEFT_HIP` | 23 | Legacy code comments 11 as "Hip"; it is the *left* hip. |
| `Rkne` | 9 | `RIGHT_KNEE` | 26 | |
| `Lkne` | 12 | `LEFT_KNEE` | 25 | |
| `Rank` | 10 | `RIGHT_ANKLE` | 28 | |
| `Lank` | 13 | `LEFT_ANKLE` | 27 | |
| `Leye` | 14 | `LEFT_EYE` | 2 | MediaPipe also has inner/outer eye points (1,3,4,5,6). |
| `Reye` | 15 | `RIGHT_EYE` | 5 | |
| `Lear` | 16 | `LEFT_EAR` | 7 | **Legacy code had 16/17 inverted — see below.** |
| `Rear` | 17 | `RIGHT_EAR` | 8 | |
| `pt19` | 18 | — | — | Background channel. No equivalent; not a body point. |
| — | — | `LEFT_HEEL` | 29 | **New capability.** No legacy equivalent. |
| — | — | `RIGHT_HEEL` | 30 | **New capability.** |
| — | — | `LEFT_FOOT_INDEX` | 31 | **New capability.** |
| — | — | `RIGHT_FOOT_INDEX` | 32 | **New capability.** |

**`NECK` must be derived** as `midpoint(LEFT_SHOULDER, RIGHT_SHOULDER)`. MediaPipe has no neck
landmark, and this is the single schema change that touches the most rule code. Worth noting it
changes nothing semantically: in the legacy COCO-18 output, keypoint 1 was *already* synthesized
from the two shoulders rather than observed. Making that derivation explicit is what exposes
FINDINGS §2.2 for what it was — a comparison of the shoulder midpoint's *y* against the shoulder
midpoint's *y*, a point against itself, which could only ever return one answer.

**The ear-inversion bug disappears by construction.** `API/config` declares 16 = left ear and
17 = right ear, but `posture_image.py:103-107` comments them the other way round, and the
laterality flag `f` — which decides whether to apply `degrees = 180 - degrees` — is keyed off that
misreading (FINDINGS §2.1). MediaPipe returns unambiguous named landmarks, so there is no
hand-rolled laterality flag to get backwards. This is the clearest case in the project of a class
of bug being removed by schema choice rather than by fixing code.

Everything except `NECK` is a rename inside the adapter, invisible to the rules engine.

## The gated risk, and the spike that settled it

MediaPipe historically shipped `manylinux_x86_64` wheels only; linux `aarch64` arrived late and —
as it turns out — left again. Because an Apple Silicon dev machine and arm64 containers are both in
scope, this was the one risk capable of invalidating the decision, so OP-16 gated it *before* any
rules code was written.

Executed 2026-07-26 on Docker 29.5.2, Apple Silicon host — `linux/arm64` natively and
`linux/amd64` under emulation — against `python:3.12-slim` (Python 3.12.13).

**Finding 1: the wheel situation, verified against the live PyPI index.**

| version | linux aarch64 | linux x86_64 |
| --- | --- | --- |
| `0.10.15` | yes | yes |
| **`0.10.18`** | **yes** | **yes** |
| `0.10.20` | **no** | yes |
| `0.10.35` (current) | **no** | yes |

`0.10.18` publishes `manylinux_2_17_aarch64` for cp39–cp312 (33 MB) and `manylinux_2_17_x86_64`
(36 MB). There is no `0.10.19`; **`0.10.20` is the first release with zero linux-aarch64 wheels**,
and `0.10.35` publishes only `manylinux_2_28_x86_64`, `win_amd64`, `win_arm64` and
`macosx_11_0_arm64`. macOS arm64 survives; Linux arm64 does not.

The wheel *installs* cleanly on both platforms with `--only-binary=:all:`, which is the question
the spike existed to answer. **Therefore no ONNX MoveNet fallback is needed, and OP-22 (B7) is
descoped.**

**Finding 2: installing the wheel is not the same as importing it.** The acceptance command as
originally written — `pip install` followed by `python -c 'import mediapipe'` on a bare
`python:3.12-slim` — **fails, on both architectures identically**:

```
  File ".../mediapipe/python/solutions/drawing_utils.py", line 20, in <module>
    import cv2
ImportError: libGL.so.1: cannot open shared object file: No such file or directory
```

This is not an architecture problem and does not affect the decision. `0.10.18` hard-depends on
`opencv-contrib-python` — the *non-headless* OpenCV build — which needs system OpenGL that the
slim image does not ship. It is recorded here because an earlier revision of this ADR claimed both
commands passed as written; they do not, and anyone re-running the spike would hit this first.

**Finding 3: two install routes work, and they are not interchangeable.**

| route | arm64 | amd64 | site-packages |
| --- | --- | --- | --- |
| **A.** default install + `apt-get install libgl1 libglib2.0-0` | pass | pass | 857 MB / 959 MB |
| **B.** `mediapipe` + `opencv-python-headless`, no system packages | **fail** | **fail** | — |
| **C.** `--no-deps` pinned runtime set + headless | pass | pass | 324 MB / 414 MB |

**Route B is the trap, and it had already been committed.** Adding `opencv-python-headless`
alongside mediapipe does *not* replace `opencv-contrib-python`. They are separate distributions
that install into the same `cv2` namespace, so both land and the non-headless one wins the import
— identical `libGL.so.1` failure. `pose-backends` declared exactly this pairing, with a comment
asserting it "avoids that system dependency entirely." It does not. Fixed in the same change as
this correction.

**Route C is the image strategy**, recorded in `packages/pose-backends/requirements-mediapipe.txt`
and consumed by the API image build. `--no-deps` cannot be expressed in `pyproject.toml`, which is
why it is a separate pinned file rather than an extra. The `mediapipe` extra remains for local
development and CI, where route A's system packages cost nothing.

Two corrections to the earlier slim-install estimate. It recorded **311 MB**; the reproducible
figures are **324 MB on arm64 and 414 MB on amd64**. And **`matplotlib` is not droppable** —
`drawing_utils` imports `matplotlib.pyplot` at module scope alongside `cv2`, so it and its whole
chain (contourpy, cycler, fonttools, kiwisolver, pillow, pyparsing, packaging, python-dateutil,
six) must be named explicitly; omitting it fails with `No module named 'PIL'`. The savings come
from dropping **`jax`, `jaxlib` and `scipy`** alone, which pose inference genuinely never imports.

Against OP-112's <600 MB API image budget, 414 MB is site-packages *before* the base image, the
model `.task` file and application code. That is a real constraint, not a comfortable margin.

**Finding 4: no dependency conflict with the web stack.** Transitive ceilings `0.10.18` imposes on
the entire workspace are **`numpy<2`** and **`protobuf<5,>=4.25.3`**. Co-installing the pin with an
unpinned FastAPI/SQLAlchemy/Pydantic and letting the resolver choose yields FastAPI 0.140.0,
SQLAlchemy 2.0.51, Pydantic 2.13.4, Starlette 1.3.1, uvicorn 0.51.0, with `numpy 1.26.4` and
`protobuf 4.25.9` intact. `pip check` reports no broken requirements and all of it imports together
in one interpreter alongside `PoseLandmarker`. The contingency of running the pose backend as its
own compose service over HTTP is therefore not needed.

## Alternatives considered

**Keep the Keras OpenPose model.** Rejected: quarter-magnitude heatmaps, no multi-person stage, no
importable entry point, 209 MB of weights with no provenance, no arm64 wheel for its TensorFlow
pin, and no confidence signal with which to express missing data.

**YOLOv8-pose (Ultralytics).** Strong accuracy and actively maintained. Rejected on licence:
AGPL-3.0 is disqualifying for a public MIT portfolio repository, and no technical merit outweighs
shipping a licence that contradicts the project's own.

**MoveNet Thunder via ONNX Runtime.** Reliably multi-arch — its main advantage, and the reason it
was the designated fallback. Rejected as the primary because it is 2D-only with a single confidence
score per point: no world landmarks (so scale invariance goes back to being a problem to solve) and
no way to separate occluded from out-of-frame. Kept as the documented escape hatch; because
everything sits behind the Protocol, implementing it is ~150 lines and an adapter swap, and nothing
in `posture-core` or `apps/api` moves.

## Consequences

- **`mediapipe==0.10.18` is a hard pin, not a floor.** Any bump breaks arm64 outright. Dependabot
  is configured to ignore this dependency, and `numpy>=2` / `protobuf>=5` are ignored too, because
  a routine-looking bump to either would break inference. Revisiting the pin requires rerunning the
  spike, not reading a changelog.
- **The workspace cannot move to Python 3.13 while pinned.** `0.10.18` publishes cp39–cp312 only.
  `requires-python = ">=3.11"` and the 3.11/3.12 CI matrix are consistent with that; 3.13 would
  need this decision reopened.
- `numpy<2` propagates to every workspace member, including `posture-core` — the one dependency the
  pure package has, now with a ceiling set by a package it must never import. An irony worth
  noticing, but not a violation: the constraint is on the resolved environment, not on
  `posture-core`'s own declared dependencies.
- **Two install routes, deliberately.** The `mediapipe` extra in `pose-backends` pulls the full
  dependency closure and needs `libgl1` + `libglib2.0-0`; that is fine for local dev and CI. The
  API image installs `requirements-mediapipe.txt` with `--no-deps` instead. Because `--no-deps`
  disables all consistency checking, every version in that file is pinned exactly and a bad
  transitive bump would surface as an ImportError at container start rather than at install time —
  regenerating it means re-verifying on both architectures.
- The adapter owns the `NECK` derivation and all landmark renaming. Rules code sees canonical named
  keypoints and never an integer index.
- Choosing a 3 M-parameter model over 52.3 M is a deliberate accuracy-for-latency trade. It is the
  right one here — the target is interactive posture feedback on a CPU, not a pose-estimation
  benchmark — but it should be stated rather than implied.
