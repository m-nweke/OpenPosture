# Image fixtures

Eight curated images, **848 KB total**, downscaled from the original 43 MB / 27-file sample set
in `API/sample_images/` (removed in OP-11). Every long edge is ≤ 1280 px.

These are the images used by:

- `pytest -m model` — real-backend tests (Epic B)
- the CLI demo, `python -m pose_backends.cli --report <image>` (OP-43)
- the old-vs-new evaluation in `docs/evaluation.md` (OP-115)

The legacy engine's verdicts on **these exact files** are recorded in
[`../../docs/archive/legacy-baseline.json`](../../docs/archive/legacy-baseline.json). That capture
was taken before the 209 MB `model.h5` and the TensorFlow 2.12 environment were deleted, and it
cannot be reproduced without re-downloading them. Treat it as a one-shot measurement: **do not
change, re-encode, or rename these files**, or the comparison stops being apples-to-apples.

## Ground truth

The first five come from a deliberately-posed set where the pose is encoded in the original
filename. That makes them usable as labelled evaluation data, not just smoke-test inputs.

| File | Posture | View | Notes |
|---|---|---|---|
| `hunchback_right.jpg` | hunchback | lateral, facing right | |
| `hunchback_left.jpg` | hunchback | lateral, facing **left** | Mirror of the above — the pair isolates laterality handling (FINDINGS §2.1) |
| `straight_armsfolded.jpg` | straight | lateral | Arms folded — the positive case for the arm-fold metric (OP-35) |
| `reclined_right.jpg` | reclined | lateral, facing right | |
| `kneeling_right.jpg` | kneeling | lateral, facing right | Lower legs partly occluded by the chair |
| `desk_lean_exif.jpeg` | leaning forward at a desk | lateral | Real photo. **Retains EXIF `orientation=6`** — see below |
| `desk_hunch.jpeg` | hunched over a laptop | lateral | Real photo, different subject and setting |
| `bench_feet_dangling.jpg` | upright on a bench | lateral | **Feet clearly off the floor**, third subject |

Three subjects, three settings. `bench_feet_dangling.jpg` is the case the original project's
README named as a goal — *"identify if feet are on the ground or dangling"* — and never delivered;
it is the fixture that gives `heel_contact` (OP-37) something real to prove.

## The EXIF fixture

All 13 original `OPnn.jpeg` photos were stored 5712×4284 with **EXIF orientation = 6** (rotate 90°
CW to display). Software that ignores the tag sees every subject lying on their side, which would
make every angular metric meaningless.

So the two real photos are prepared differently *on purpose*:

- **`desk_lean_exif.jpeg`** — resized in stored-pixel space with the orientation tag **preserved**.
  Loading it without applying EXIF gives a sideways person. This is the regression fixture for the
  EXIF-orientation correction required by OP-53.
- **`desk_hunch.jpeg`** — rotation baked into the pixels, tag stripped. The already-normalized case.

`cv2.imread` applies EXIF orientation by default; `PIL.Image.open` does **not** unless you call
`ImageOps.exif_transpose`. That difference is exactly the kind of thing that silently corrupts a
pose pipeline, which is why one fixture keeps the tag.

## Known gap: no frontal image

OP-11 asked for a frontal shot to drive the `view_confidence` rejection test (OP-38). **The
inherited corpus contains none** — all 27 originals are lateral. The unit tests for OP-38 use
synthetic poses from `make_pose()` and do not need a photo, but end-to-end verification
(V2-PLAN "Verification" step 6, *confirm a frontal photo is rejected*) does. A frontal image needs
to be added before Epic D closes.

## Provenance

| Fixture | Original |
|---|---|
| `hunchback_right.jpg` | `API/sample_images/hunchback.jpg` |
| `hunchback_left.jpg` | `API/sample_images/hunchback_flip.jpg` |
| `straight_armsfolded.jpg` | `API/sample_images/straight_hf.jpg` |
| `reclined_right.jpg` | `API/sample_images/recline.jpg` |
| `kneeling_right.jpg` | `API/sample_images/kneeling.jpg` |
| `desk_lean_exif.jpeg` | `API/sample_images/OP55.jpeg` |
| `desk_hunch.jpeg` | `API/sample_images/OP73.jpeg` |
| `bench_feet_dangling.jpg` | `API/sample_images/img/img.jpg` |

`OP55.jpeg` is kept deliberately: it is the image the archived `RUNDOWN.md` verified the original
model against, and V2-PLAN's verification checklist names it. The full original set remains in git
history and can be recovered with `git show <commit>:API/sample_images/<name>`.
