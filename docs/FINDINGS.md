# OpenPosture — Audit of the Inherited Codebase

> **Date:** 2026-07-25 · **Companion doc:** [`V2-PLAN.md`](./V2-PLAN.md)
>
> This is the "before" half of the project's before/after story, and the evidence base for
> `docs/evaluation.md` (OP-115). Everything below was verified by reading the code at commit
> `32fa0e7`, not inferred from naming.
>
> Line references point at the pre-rewrite tree. Once Epic A runs, the same files live under
> `docs/archive/legacy-openpose/`.

---

## 1. What the application actually was

Four loosely-related pieces sharing a directory, not one program:

| Path | Role | Lines |
|---|---|---|
| `API/app.py` | Flask server. Four routes, all Firebase file-shuffling. **Never imports the model.** | 56 |
| `API/model.py` | CMU OpenPose two-branch 6-stage CNN architecture. 52,311,446 params. | 208 |
| `API/posture_image.py` | Standalone script: one image → printed verdicts + blocking OpenCV window. | 336 |
| `API/posture_realtime.py` | Same logic against a webcam. ~90% copy-paste of the above. | 324 |
| `API/util.py`, `API/config_reader.py`, `API/config` | Padding helper + ConfigObj loader. | 56 |

Total backend: **1,137 lines.** Plus two functionally identical frontends (`openpose-vue/`, `openpose-react/`).

### The core architecture worth preserving conceptually

The neural network is **a very good eye and nothing more** — it emits ~18 labeled coordinates and has no concept of "slouching." Everything after peak extraction is a hand-written rulebook doing trigonometry on those dots. That split — **learned perception, hand-coded reasoning on top** — is the one structural idea the rewrite keeps. It matters because model quality and rule quality are then two entirely separate problems, and almost every "why did it misjudge this photo?" resolves to *which layer failed?*

In the inherited code the eye was excellent (pretrained CMU research model) and the rulebook was not.

---

## 2. Correctness defects in the posture logic

These are the substantive ones. A rewrite that only modernized the stack would leave every one of them in place.

### 2.1 Ear indices are inverted — laterality is backwards

`API/config` declares the authoritative keypoint order:

```
part_str = [nose, neck, Rsho, Relb, Rwri, Lsho, Lelb, Lwri, Rhip, Rkne,
            Rank, Lhip, Lkne, Lank, Leye, Reye, Lear, Rear, pt19]
```

So index **16 = left ear**, **17 = right ear**. But `posture_image.py:103-107` comments them the other way round, and the laterality flag `f` — which decides whether to apply `degrees = 180 - degrees` — is keyed off that misreading. The same inversion appears in `checkKneeling` (`:176`).

**Effect:** hunchback vs. reclined classification is likely inverted for one of the two facing directions. Index 11 is similarly commented "Hip" but is the *left* hip (index 8 is the right).

**Fixed by:** MediaPipe exposes unambiguous named landmarks (`LEFT_EAR`, `RIGHT_EAR`), so there is no hand-rolled laterality flag to get backwards. The bug disappears by construction.

### 2.2 The neck metric is geometrically meaningless

`evaluate_neck_posture` (`:240-245`) compares the neck's *y* coordinate to the shoulder-centre *y*:

```python
elif neck[1] < shoulder_center[1]:
    neck_posture = "Neck is Forward"
```

In image coordinates y grows **downward**, so `neck_y < shoulder_center_y` means the neck is *above* the shoulders — true for every upright human ever photographed. Forward-head posture is a **sagittal (x-axis) offset** — ear ahead of shoulder — not a vertical one. The `10` threshold is also raw pixels, so it means different things at different image resolutions.

**Effect:** the metric reports "Neck is Forward" essentially always. It carries no information.

**Replaced by:** craniovertebral angle — the angle at C7 between the ear→C7 vector and horizontal, computed in world space. `< 50°` indicates forward head. (OP-34)

### 2.3 The feet check is a tautology

`evaluate_feet_position` (`:274`) concludes "both feet are on the floor" when each ankle's y exceeds its knee's y. That holds for almost any seated or standing pose regardless of whether the feet are actually grounded or dangling.

**Effect:** no information. Notably, the original `README.md` listed *"identify if feet are on the ground or dangling"* as a project goal — it was never achieved.

**Replaced by:** real heel contact using MediaPipe's `HEEL` (29/30) and `FOOT_INDEX` (31/32) landmarks, which the 18-point COCO schema simply did not have. (OP-37)

### 2.4 Uncaught `UnboundLocalError` in `checkKneeling`

```python
if (all_peaks[10][0][0:2] and all_peaks[13][0][0:2]):   # :179
    ...
    leftdegrees = round(math.degrees(leftangle))
if (f == 0):
    leftdegrees = 180 - leftdegrees                      # :188
```

If both ankle entries exist but the `if` evaluates falsy, `leftdegrees` and `rightdegrees` are never bound before line 188 reads them. `UnboundLocalError` is a subclass of `NameError` — **not** caught by the enclosing `except IndexError` (`:200`).

**Effect:** hard crash.

### 2.5 Silent false negative: `None` → "Straight back position"

`checkPosition` (`:100-120`) wraps its body in `except Exception`, prints a message, and implicitly returns `None`. `__main__` then does:

```python
if position == 1:    print("Hunchback position")
elif position == -1: print("Reclined back position")
else:                print("Straight back position")   # ← None lands here
```

**Effect:** every pose the system *failed to assess* is reported as good posture. This is the single most damaging behaviour in the codebase — a diagnostic tool that defaults to "you're fine" when it can't see you.

**Fixed by:** typed `MetricStatus`. A metric that can't be computed yields `value=None` and a `Gap`, never a Finding — so the API can say *"couldn't assess your knees, try a wider shot."* (OP-32)

### 2.6 Unnormalized magic-number thresholds

`checkHandFold` (`:144`) decides arm folding with a literal ±100 pixels. The original author's own inline comment:

> `# this value 100 is arbitary [sic]. this shall be replaced with a calculation which can adjust to different sizes of people.`

The same class of problem affects the neck check's `10` px and, less visibly, every angular threshold that was tuned against one camera setup.

**Effect:** results depend on image resolution and subject distance. A person standing twice as far away gets a different verdict for identical posture.

**Fixed by:** world-space angles (metres, resolution-independent) plus torso- and shoulder-width-normalized ratios. Proven by a Hypothesis property test asserting invariance under uniform scale `s ∈ [0.3, 3.0]` and translation — a test that would fail catastrophically against the current code. (OP-31, OP-41)

### 2.7 The unenforced side-angle requirement

Both dashboards instruct: *"This image must be taken from a side angle."* Nothing anywhere checks it. A frontal photo produces confident, meaningless spine-angle numbers.

**Fixed by:** a `view_confidence` heuristic from the shoulder-width : torso ratio, with frontal images rejected up front. (OP-38)

---

## 3. Inference-pipeline defects

### 3.1 Quarter-magnitude heatmaps

`process()` configures four scales (`scale_search = 0.5, 1, 1.5, 2`) but iterates `range(1)` — only the first runs (`:25`). The accumulators still divide by `len(multiplier)` = 4 (`:44-45`):

```python
heatmap_avg = heatmap_avg + heatmap / len(multiplier)
```

**Effect:** heatmaps come out at ¼ their intended magnitude while `thre1 = 0.1` stays fixed, silently changing which peaks are detected at all. Any attempt to "fix" the loop by iterating all four scales would change every downstream verdict — which is why the v2 thresholds are re-tuned from scratch rather than ported.

### 3.2 The multi-person stage is entirely absent

`connection_all`, `special_k`, and `mid_num` are declared at `:75-77` and never used. The PAF (Part Affinity Field) limb-grouping stage of real OpenPose — the part that assembles keypoints into distinct people — was never implemented. Every downstream check takes `all_peaks[part][0]`, the first detected peak.

**Effect:** the code implicitly assumes exactly one person in frame, undocumented. The 38 PAF channels are computed on every inference and thrown away.

### 3.3 Neither `process()` is importable

- `posture_image.process()` references a module-global `model` assigned only inside `if __name__ == '__main__'` (`:32` vs `:320`).
- `posture_realtime.process()` references a module-global `frame` set only in the `__main__` webcam loop (`:81`).

**Effect:** `NameError` on import. This is precisely why `app.py` never wired the model up — the functions were not callable from anywhere else. `RUNDOWN.md`'s "Open Items" identified the symptom without naming this cause.

### 3.4 Lazy-iterator exhaustion in `config_reader`

`config_reader.py` casts `scale_search` with `map()`, producing a lazy Python-3 iterator. `process()` consumes it at `:22`. It works exactly once; any reuse of the same `params` dict yields an empty `multiplier` and an `IndexError` at `multiplier[0]`.

`posture_realtime.py` survives this **by accident** — it re-calls `config_reader()` on every single frame (`:307`), re-parsing the config file from disk per frame.

### 3.5 Everything is cwd-dependent

`model.load_weights('./model/keras/model.h5')` and `ConfigObj('config')` are both relative paths. Both scripts only run with the working directory set to `API/`.

### 3.6 Smaller inefficiencies

- `util.padRightDownCorner` hardcodes `pad[0] = pad[1] = 0`, so the `pad_up` / `pad_left` `np.tile` calls always produce empty arrays and get concatenated for nothing — dead work on every call.
- `draw()` re-reads the image from disk with a second `cv2.imread` (`:93`) instead of reusing `oriImg`. Double I/O per analysis.
- `posture_realtime.py:301-302` calls `cap.set(100, 160)` / `cap.set(200, 120)` to reduce capture resolution. Those property IDs are bogus — `CAP_PROP_FRAME_WIDTH` is 3, `HEIGHT` is 4 — so full-resolution frames go through a 52 M-parameter model every iteration.
- `cv2.destroyAllWindows()` sits at module level outside the `__main__` guard, firing on import.
- `ret` from `cap.read()` is never checked.
- Results are emitted via bare `print()`. Only `checkPosition` returns a value at all — which is the mechanical reason the analysis functions were unusable from a web request.

---

## 4. Engineering-practice gaps

| Gap | Detail |
|---|---|
| **No tests** | Not one `test_*.py`, no `tests/` directory, no test framework in any dependency file. |
| **No CI** | No `.github/`, no workflow YAML, no pre-commit. |
| **No containers** | No Dockerfile, no compose file, no Procfile, no WSGI entrypoint. |
| **No logging** | Zero `import logging` anywhere. Every diagnostic — including the Firebase credential warning — is a `print()`. |
| **No linting or typing** | No ruff/flake8/pylint/mypy config, no `pyproject.toml`. |
| **Exception swallowing** | ~10 occurrences of `except Exception as e:` with `e` unused and a `print` hiding the failure. |
| **Duplication** | ~250 lines byte-identical between `posture_image.py` and `posture_realtime.py`, already accidentally forked on return strings (`"Neck is Straight"` vs `"Straight"`). |

---

## 5. Security and configuration issues

### 5.1 `POST /upload` is unauthenticated and unvalidated

```python
blob = bucket.blob(file.filename)      # raw client-supplied name
blob.upload_from_file(file)
```

No auth, no `secure_filename`, no content-type check, no size limit — behind `CORS(origins='*')` and using **admin** credentials, which bypass Firebase Security Rules entirely. Any caller can overwrite any object in the bucket, including the `image1.jpg` that `GET /images` serves back. It also returns HTTP 200 for its own error paths (`'No file part'`, `'No selected file'`).

### 5.2 The backend has no authentication at all

Firebase Auth is implemented **client-side only**. No ID token is ever sent to Flask, and Flask never verifies one. Every backend route is fully open.

### 5.3 Committed Firebase configuration

The web `apiKey` is committed in two places — `openpose-vue/src/main.ts:11` and `openpose-react/src/firebase.ts:7` — both pointing at the live `openpose-db` project. A web apiKey is public by design and is **not** a leaked secret, but it means Security Rules are the only access control on that project, which §5.1 then bypasses.

### 5.4 Service-account path in git history

Commits `2002f8b` and `109d71b` hardcoded:

```
/Users/michaelnweke/PhpstormProjects/CS5588-Capstone-Project/API/db/openpose-db-firebase-adminsdk-pl8gq-05904164a8.json
```

The key **file** was never committed, so no private key is exposed. But the project id, a service-account key-id fragment, and a prior machine's directory layout are in history permanently. Cleaned up in `daff744`.

### 5.5 `debug=True`

`app.run(debug=True, port=5000)` exposes the Werkzeug interactive debugger console if that code path is ever reached outside local dev.

### 5.6 Dead configuration

Of the `config` file's keys, only `boxsize`, `stride`, `padValue`, `scale_search`, and `thre1` are read. `thre2`, `thre3`, `min_num`, `mid_num`, `crop_ratio`, `bbox_ratio`, `octave`, and the range settings are dead. `use_gpu = 1` is parsed and never consulted. `[models][[1]]` still references `./model/_trained_COCO/pose_iter_440000.caffemodel`, a path that does not exist.

---

## 6. Dependency and environment rot

The `tensorflow==2.12.0` pin is **load-bearing in both directions**:

- **Upper bound:** `model.py:4-5` imports `keras.layers.convolutional.Conv2D` and `keras.layers.pooling.MaxPooling2D` — module paths removed after Keras 2.12.
- **Lower bound:** TF 2.12 publishes no wheel for Python 3.12+, so **Python 3.11 is mandatory** (`env/pyvenv.cfg` confirms 3.11.15).
- **Platform:** there is no `tensorflow==2.12.0` macOS arm64 wheel. The working venv actually contains `tensorflow_macos-2.12.0`, a substitution documented in `RUNNING.md` but deliberately kept out of `requirements.txt`.

Other fragile pins: `Flask==2.0.2` (which imports `url_quote`, removed in Werkzeug 3 — a real install-blocking conflict that had to be fixed), `firebase-admin==5.1.0` (2021-era), `numpy==1.23.5` (incompatible with the numpy-2 ecosystem), `opencv-contrib-python==4.5.5.62`.

**Model weights:** `API/model/keras/model.h5` is 209,602,136 bytes — over GitHub's 100 MB limit, so it is git-ignored and distributed via a Dropbox URL in `model/keras/readme.md`. A dead link makes the project unrunnable.

*(Note: `RUNDOWN.md` claims the `.gitignore` entry for `.h5` is missing its `*` wildcard. That is stale — the root `.gitignore` reads `*.h5` and correctly ignores the file. Delete that line when rewriting the docs.)*

---

## 7. Repository size

2.5 GB on disk, **~2.2 GB of it already git-ignored and reproducible.** Only ~150 files are tracked.

| Path | Size | Tracked? | Reproducible by |
|---|---|---|---|
| `API/env/` | 1.4 GB | No | `uv sync` |
| `openpose-vue/node_modules` + `openpose-react/node_modules` | ~590 MB | No | `npm install` |
| `API/model/keras/model.h5` | 200 MB | No | Dropbox link |
| `.git` | 138 MB | — | — |
| `Presentations/` | 48 MB | Yes | — |
| `API/sample_images/` | 43 MB (29 near-duplicate JPEGs) | Yes | — |
| `Demos/` | 18 MB | Yes | — |
| `Misc/` + root PDFs/docx/xlsx | ~27 MB | Yes | — |

The `.git` bulk is coursework media committed to history — largest blobs are `Presentations/OpenPosturePhase2.pptx` (17 MB), `OPPoster.pdf` (12 MB), `PostureCapstone.pptx` (11 MB).

**Decision (ADR-0006): do not rewrite git history.** `git filter-repo` would take `.git` from 138 MB to ~15 MB, but 138 MB clones fine, and the full history is the evidence that this is a genuine re-adoption of a real two-year-old team project. Rewriting costs that and risks breaking the remote.

*Practical consequence encountered 2026-07-25:* the working-tree size blocked a Claude Code cloud session ("repo is too large to teleport"). The repo does have a GitHub remote (`m-nweke/CS5588-Capstone-Project`); the fix is to connect that remote to claude.ai so the cloud session clones it, rather than teleporting a working tree that is 90% untracked. Epic A's cleanup resolves it locally either way.

## 7.1 Branch state at the time of the audit

*Historical — describes the tree as of 2026-07-25, before Epic A began. Kept because it explains
why the restructure epics are sequenced the way they are; it is **not** a description of current
`main`.*

At audit time, `main` sat **two commits behind** `react-add` (`daff744` "Add react mirror FE",
`32fa0e7` "Running doc"). `main` therefore contained **no React frontend at all** —
`openpose-react/` was untracked there — and also lacked `RUNNING.md` and the `db/firebase.py`
credential refactor described in the archived `RUNDOWN.md`. PR #7 (`react-add` → `main`,
"React-Mirror + Restore Functionality") was open and unmerged.

This was load-bearing for the v2 restructure: the epic that moves the React app to `apps/web` and
the one that archives `COMPARISON.md` both operate on files that existed only on `react-add`.
That branch had to land on `main` first, or the restructure had to branch from `react-add`.

**Resolved:** PR #7 was merged into `main` as `e1bc9b3` before this audit landed, so the
restructure epics branch from `main` as normal. Any later reference in this document to files
"existing only on `react-add`" should be read against the snapshot above.

---

## 8. The headline gap

**Both dashboards are mocked.** `submitImage()` reads the file into a data URL for local preview, then:

```ts
timerRef.current = window.setTimeout(() => {
  setShowLoading(false)
  setShowResults(true)
}, 5000)
```

…and renders two hardcoded module-level constants, `POSTURE_DETECTION_RESULT` and `WORKOUT_RESULT` (`openpose-react/src/views/Dashboard.tsx:7-16`). Nothing is uploaded. The model is never invoked. The Vue app does the same thing.

Meanwhile the only backend call either frontend makes is `axios.get('http://127.0.0.1:5000/')` — the sanity route returning `"Hello World"` — with the URL hardcoded, no env var, no axios instance, and no Vite proxy. The `/ping`, `/upload`, and `/images` routes are never called by anything.

**This is the single defect that makes the project unpresentable, and closing it is Epic D — the walking skeleton.**

---

## 9. What survives into v2

Not much code, but the important things:

- **The two-layer architecture** — learned perception feeding a hand-coded rules layer. Kept, and made explicit by putting the rules in their own dependency-free package.
- **The five metric families** — trunk inclination, arm folding, knee flexion, neck posture, feet. All five are reimplemented with correct geometry and normalized thresholds; feet becomes a real metric for the first time.
- **The React frontend's structure** — component tree, routing, CSS Modules, and `ProtectedRoute`'s `checking` state (which was correct) all carry forward. Only Firebase and the mocked Dashboard are torn out.
- **`opresults.py`** — the original evaluation artifact (confusion matrices from a Colab export). Archived, and its methodology is rerun against the new engine in `docs/evaluation.md` (OP-115) to produce a genuine old-vs-new comparison.
- **`COMPARISON.md`** — the Vue→React port analysis. A real piece of technical writing; archived rather than deleted.