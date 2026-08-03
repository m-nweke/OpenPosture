# OpenPosture v2 — Rebuild as a Portfolio-Grade Full-Stack AI Application

> **Status:** approved 2026-07-25, in execution · **Tracking:** Jira project `OP`
> **Companion doc:** [`FINDINGS.md`](./FINDINGS.md) — the full audit of the inherited codebase this plan replaces.

> **⚠️ The ticket numbers below are the pre-import draft numbers and are not the Jira keys.**
> The import assigned `OP-1`–`OP-8` to the epics themselves and renumbered the stories
> contiguously beneath them, so the plan's `OP-1` (write the plan and findings docs) is Jira
> `OP-9`, its `OP-20` (the MediaPipe spike) is `OP-16`, and its `OP-30` (`geometry.py`) is
> `OP-23`. Code comments, commit messages and branch names cite the **Jira** keys; only this
> document uses the draft numbering, and it is left as written rather than renumbered because it
> is the record of what was planned before any of it was built. The mapping:
>
> | Epic | Jira epic | Plan stories | Jira stories |
> | --- | --- | --- | --- |
> | A — Foundation | `OP-1` | OP-1…7 | `OP-9`…`OP-15` |
> | B — Pose backend | `OP-4` | OP-20…26 | `OP-16`…`OP-22` |
> | C — Rules engine | `OP-2` | OP-30…45 | `OP-23`…`OP-38` |
> | D — Walking skeleton | `OP-3` | OP-50…59 | `OP-39`…`OP-48` |
> | E — Persistence + auth | `OP-5` | OP-60…70 | `OP-49`…`OP-59` |
> | F — LLM coaching | `OP-6` | OP-80…88 | `OP-63`…`OP-75` |
> | G — Live mode | `OP-7` | OP-100…104 | — |
> | H — Polish and proof | `OP-8` | OP-110…116 | `OP-60`, `OP-61` |
>
> Epics G and H are held as the plan text below until their stories are cut; the two H stories
> that do exist (`OP-60` threshold recalibration, `OP-61` the `overall_score` design) were raised
> during Epic C rather than planned here.
>
> **Epic F is the one section rewritten rather than preserved.** Its stories were cut *after* the
> import and after Epics A–D shipped, so the draft numbering never described anything that was
> built. Re-planning it revealed three changes the original text got wrong: the provider sequence
> (local-first, paid last), the exercise library (referenced as a phrase, never designed as an
> artifact), and coaching persistence (a text column that cannot answer the questions a history
> view asks). The section below is written against the **Jira** keys because, unlike the rest of
> this document, it is a current plan rather than a historical record.
>
> **Progress against the plan:** Epics A, B and D are merged. Epic C is merged through the
> shared spec, the report CLI and `scientific-validation.yml`; its remaining stories are the
> extended scientific property suite and the evaluation-data contract. Epic E onward has not
> started.
>
> Epic D shipped as planned: the walking skeleton works end to end, a real photograph produces
> a real analysis in a browser, and `containers.yml` and `e2e.yml` arrived with the capabilities
> they check. Two deviations worth recording. The upload response carries **landmarks**, which
> the plan did not anticipate — D8's canvas overlay needs coordinates and the report only
> carried statuses. And D7 required **Pydantic response models** before type generation was
> worth anything: a route annotated `-> dict[str, Any]` produces a schema that says "an
> object".
> Plan story OP-26 / Jira `OP-22`, the ONNX MoveNet fallback, was **cancelled**: the spike passed
> on both architectures, so the contingency was never needed.

## Context

OpenPosture is a CS5588 capstone the user inherited rather than authored. Three things keep it off a resume:

**1. It doesn't work end-to-end.** Both dashboards are fake — `submitImage()` runs a 5-second `setTimeout` and renders hardcoded strings (`openpose-react/src/views/Dashboard.tsx:7-16,49-58`). `API/app.py` never imports the model. There is no inference endpoint. This one missing wire is the whole gap between demo and product.

**2. The stack is rotting.** `tensorflow==2.12.0` is pinned in both directions — no Python 3.12+ wheel, no macOS arm64 wheel, and `API/model.py:4-5` imports `keras.layers.convolutional`, a path deleted after Keras 2.12. The 209 MB `model.h5` ships via a Dropbox link.

**3. No engineering signal.** Zero tests, zero CI, no Docker, no logging (every result is a bare `print()`), ~250 lines copy-pasted between `posture_image.py` and `posture_realtime.py`, and `/upload` writes attacker-chosen blob names into Firebase with admin credentials behind `origins: '*'`.

**And the posture logic is substantively wrong** — this matters, because a rewrite that only modernizes the stack has no story. Four defects found during exploration, all quotable in the README:

| Bug | Where | Effect |
|---|---|---|
| **Ear indices inverted.** `API/config` `part_str` says index 16 = `Lear`, 17 = `Rear`; the code comments them backwards and keys the laterality flag `f` off that. | `posture_image.py:103-111`, `checkKneeling` | Hunchback/recline classification likely **inverted for one facing direction** |
| **Neck metric is geometrically meaningless.** Compares neck *y* to shoulder-center *y*. In image coords (y grows downward) `neck_y < shoulder_y` is true for every upright human. Forward-head posture is a *sagittal* offset, not vertical. | `posture_image.py:240-245` | Always reports "Neck is Forward" |
| **Feet check is a tautology.** "Ankle below knee → feet on floor" holds for nearly any seated pose. | `posture_image.py:274` | Metric carries no information |
| **Uncaught `UnboundLocalError`.** If both ankles exist but the `if` is falsy, `leftdegrees` is never bound before `180 - leftdegrees`. `UnboundLocalError` is a `NameError`, **not** caught by `except IndexError`. | `posture_image.py:179-189` | Hard crash |

Plus: `checkPosition` returns `None` on failure, which `__main__` maps to **"Straight back position"** — a silent false negative on every undetectable pose.

**Intended outcome:** `git clone && cp .env.example .env && docker compose up` — with **no accounts to create and no API key required** — brings up an app where a real photo produces a real, model-derived analysis plus LLM coaching, backed by tests, CI, typed contracts, and a README a recruiter skims in 30 seconds.

**Settled decisions:** modern pose backend + rules engine + LLM coach · Dockerized local, no public deploy · React+TS survives, Vue deleted · self-hosted JWT, Firebase removed entirely · browser-side live mode · plan in markdown now, Jira project OP later.

---

## Target architecture

```
openposture/
├─ README.md  ARCHITECTURE.md  CHANGELOG.md  Makefile  .env.example
├─ docker-compose.yml            # dev, hot reload
├─ docker-compose.prod.yml       # prod overlay, used by CI smoke test
├─ .github/
│  ├─ workflows/
│  │  ├─ pr.yml                    # fast, required engineering checks
│  │  ├─ integration.yml           # Postgres/MinIO/migrations
│  │  ├─ scientific-validation.yml # properties, data quality, evaluation regression
│  │  ├─ e2e.yml                   # deterministic full-stack tests
│  │  ├─ containers.yml            # production-image and Compose validation
│  │  ├─ security.yml              # CodeQL, dependency and container scanning
│  │  ├─ model-validation.yml      # on-demand real-model and performance regression
│  │  └─ release.yml               # reproducible local release bundles; no deployment
│  ├─ actions/setup-project/action.yml
│  ├─ dependabot.yml
│  ├─ pull_request_template.md
│  └─ CODEOWNERS
├─ apps/
│  ├─ api/                       # FastAPI + Pydantic v2 + SQLAlchemy 2.0
│  │  └─ src/openposture_api/{main,config,deps}.py  api/v1/  db/  schemas/  services/  security/
│  └─ web/                       # React 19 + TS (from openpose-react/)
├─ packages/
│  ├─ posture-core/              # ⭐ PURE rules engine. numpy only. No I/O, no globals.
│  ├─ pose-backends/             # inference adapters behind a Protocol (impure, heavy)
│  ├─ posture-core-ts/           # TS mirror for browser live mode
│  ├─ posture-spec/              # SHARED rules.json + golden/*.json (cross-language contract)
│  └─ posture-coaching/          # curated exercise library keyed by finding code (data only)
├─ docs/
│  ├─ V2-PLAN.md                 # this plan, committed
│  ├─ adr/0001..0006.md
│  ├─ evaluation.md              # old-engine vs new-engine metrics
│  ├─ images/                    # screenshots + demo.gif
│  └─ archive/                   # coursework provenance + legacy-openpose/
└─ fixtures/images/              # ~8 curated, downscaled sample images
```

Python packaging via **`uv` workspace** (`[tool.uv.workspace] members = ["apps/api", "packages/*"]`) — one lockfile, editable local packages, fast Docker builds. No `requirements.txt`.

**Why `apps/` + `packages/` is load-bearing, not decoration:** `posture-core` has zero heavy dependencies, so its ~200 unit tests run in CI in under two seconds with no model, no Docker, no DB. `pose-backends` quarantines the one impure, platform-fragile dependency behind a Protocol. `apps/api` depends on both. **That dependency direction is the entire architectural argument, and it's visible from the directory tree.**

### Why FastAPI, not modernized Flask

Nothing in `app.py`'s 56 lines survives the new requirements, so this is a rewrite either way — the only question is which framework. FastAPI wins on all four actual needs: `UploadFile` is spooled to disk (a 15 MB photo doesn't sit in RAM); `run_in_threadpool` keeps blocking CPU-bound inference off the event loop; `StreamingResponse` over an async LLM stream is ~10 lines where Flask+WSGI streaming while holding a DB session is genuinely awkward; and `app.dependency_overrides` **is the answer** to "how do I test an endpoint that runs a model without running the model."

The bonus that improves the frontend: FastAPI emits OpenAPI 3.1, so `openapi-typescript` generates `apps/web/src/api/schema.d.ts` from `/openapi.json`. A breaking backend change then fails `tsc` in CI. *"My frontend types are generated from my backend schema"* is a stronger interview line than anything else in the stack.

### Why MediaPipe Pose Landmarker, not the CMU model

| | Current (TF/Keras OpenPose) | MediaPipe Pose Landmarker |
|---|---|---|
| Model | 209 MB, Dropbox-hosted, 52.3 M params | 9 MB (`_full.task`), ~3 M params |
| CPU latency | seconds | ~20–35 ms |
| Python | 3.11 only, no arm64 wheel | 3.9–3.12 |
| License | provenance unclear | Apache-2.0 |
| Keypoints | 18 (COCO), x/y only | 33 incl. **heel + foot_index**, x/y/z |
| Confidence | none | **`visibility` + `presence`, both [0,1]** |
| 3D | none | **`pose_world_landmarks` — metres, hip-origin** |

Three decisive wins, in order:

1. **World landmarks give scale invariance for free.** They're in metres with the hip midpoint as origin — independent of image resolution and subject distance. The core defect of the existing code ("normalize thresholds by torso length") collapses into *"compute the angle in world space."* No competing backend offers this.
2. **`visibility` + `presence` implement explicit missing-data handling.** You can distinguish *occluded* (low visibility, high presence) from *out of frame* (low presence) and surface that honestly in the API — instead of `checkPosition` silently reporting "Straight back position."
3. **Feet.** `LEFT_HEEL(29)`/`RIGHT_HEEL(30)`/`LEFT_FOOT_INDEX(31)`/`RIGHT_FOOT_INDEX(32)` make `heel_contact` a real metric. The original `README.md` listed "identify if feet are on the ground or dangling" as a project goal and never delivered it.

Rejected: **YOLOv8-pose** — AGPL-3.0, disqualifying for a public MIT portfolio repo. **MoveNet Thunder (ONNX)** — solid and reliably multi-arch, but 2D-only with a single confidence score; it's the *fallback*, not the choice.

**The one real risk, gated first:** MediaPipe historically shipped `manylinux_x86_64` wheels only; `aarch64` arrived late. **OP-20 is a 1-point spike, run before any rules code**, with a mechanical criterion:

```bash
docker run --rm --platform linux/arm64 python:3.12-slim \
  sh -c "pip install --only-binary=:all: mediapipe==<pin> && python -c 'import mediapipe'"
```

Pass → MediaPipe, multi-arch. Fail → **do not** fall back to QEMU-emulated amd64 (≈10× slower inference ruins the local demo); implement `ONNXMoveNetBackend` instead. Because everything sits behind the Protocol, that's ~150 lines plus an adapter change, and **nothing in `posture-core` or `apps/api` moves.**

### Keypoint mapping (ADR-0002)

`NECK` must be **derived** as midpoint(`LEFT_SHOULDER`, `RIGHT_SHOULDER`) — the single schema change touching the most rule code. Everything else is a rename inside the adapter, invisible to `metrics.py`. Legacy `Rsho/Lsho` 2/5 → 12/11 · `Relb/Lelb` 3/6 → 14/13 · `Rwri/Lwri` 4/7 → 16/15 · `Rhip/Lhip` 8/11 → 24/23 · `Rkne/Lkne` 9/12 → 26/25 · `Rank/Lank` 10/13 → 28/27 · `Lear/Rear` 16/17 → 7/8. The ear-inversion bug **disappears by construction**: MediaPipe gives unambiguous named landmarks, so there is no hand-rolled `f` laterality flag to get backwards.

### Keeping one rules engine across two languages

Live mode needs the rules in TypeScript; the API needs them in Python. Two implementations that drift are worse than no live mode, so duplication is contained by `packages/posture-spec/`:

- **`rules.json`** — every threshold, landmark index, and normalization constant. Neither implementation hardcodes a number; both load this. Retuning is a one-line change in one place.
- **`golden/*.json`** — the synthetic fixtures from Epic C plus expected verdicts. **Both** pytest and Vitest run this corpus, and **CI fails if the two engines disagree on any fixture.**

The TS mirror implements only what live mode needs (trunk inclination, craniovertebral angle, shoulder asymmetry); Python stays canonical and complete.

---

## Epics

Ticket-sized stories (½–2 days), ready to convert to Jira project OP. Sequence: **A → B → C → D → E → F → G**; A and B overlap. **OP-20 is the very first technical ticket.**

### Delivery rule: implementation, tests, and automation ship together

Testing and CI are not later hardening phases. **Every capability adds its tests and corresponding GitHub Actions check in the same pull request that introduces the capability.** This applies to Python packages, React, FastAPI, Docker, OpenAPI generation, Postgres/MinIO, Alembic, authentication, cross-language rules, evaluation data, and release packaging.

A ticket is done only when:

1. the implementation and its smallest appropriate automated tests ship together;
2. happy paths, boundaries, error/degradation behavior, and authorization rules are covered where applicable;
3. lint, formatting, and strict type checks pass for the affected language;
4. coverage does not decrease and the component's ratcheted floor passes;
5. generated contracts, migrations, fixtures, and evaluation baselines show no unexplained drift; and
6. its automation runs at the cheapest correct layer—pure unit first, then contract/integration, with E2E reserved for critical journeys.

Coverage begins when each codebase becomes testable: **95% for `posture-core`, 85% for API application code, and 70% for React**. New packages start at their target rather than accumulating untested code to repair later. Generated code and type-only declarations may be excluded; difficult branches may not.

### Epic A — Foundation
*Leaves: clean repo with Python and React quality gates that went live alongside their codebases.*

- **OP-1** ⬅️ **FIRST ACTION.** Write two markdown files into the repo before touching any code:
  - `docs/V2-PLAN.md` — this plan verbatim, so it's versioned alongside the work and is the source for the Jira import.
  - `docs/FINDINGS.md` — the full audit of the inherited codebase: the four posture-logic bugs in the Context table, the silent `None`→"Straight back" false negative, the single-scale/÷4 heatmap mismatch (`posture_image.py:25,44`), the non-importable `process()` functions (module-global `model` and `frame`), the exhausted `scale_search` map iterator in `config_reader.py`, the dead PAF limb-grouping stage, `padRightDownCorner`'s always-empty `pad_up`/`pad_left` tiles, `draw()`'s redundant second `cv2.imread`, `cv2.destroyAllWindows()` outside the `__main__` guard, the unauthenticated `/upload` with raw `file.filename` as blob key, and the ~250 lines duplicated between the two posture scripts. **This file becomes the "before" half of the README's before/after story and the evidence base for `docs/evaluation.md` (OP-115).**

  Then create `apps/`, `packages/`, `docs/archive/`, `fixtures/`.
- **OP-2** `git mv` archive material: `Demos/`, `Misc/`, `Presentations/`, root PDFs/docx/xlsx, `opresults.py`, `RUNDOWN.md`, `RUNNING.md`, `ModelReadME.md`, `openpose-react/COMPARISON.md`, `React-vs-Vue.pptx` → `docs/archive/`. Legacy Python (`posture_image.py`, `posture_realtime.py`, `model.py`, `config`, `config_reader.py`, `util.py`) → `docs/archive/legacy-openpose/`, excluded via `tool.ruff.exclude`. **Keeping the "before" readable next to the "after" is the point.**
- **OP-3** Delete `openpose-vue/`, `API/env/` (1.4 GB), `API/model/keras/model.h5`, `API/app.py`, `API/db/`. Prune `API/sample_images/` (43 MB, 29 near-duplicate files) to 8 images downscaled to ≤1280 px → `fixtures/images/`. **Do not rewrite git history** — `filter-repo` would take `.git` from 138 MB to ~15 MB, but 138 MB clones fine and the full history is the evidence this is a real re-adoption of a real team project. Record in ADR-0006.
- **OP-4** `uv` workspace + root `pyproject.toml`; ruff (lint+format), mypy `--strict`, pytest + cov + asyncio, pre-commit. **In this same ticket create `.github/workflows/pr.yml` with independent Python `lint`, `typecheck`, and `test-python` jobs.** The first Python package cannot merge without frozen-lockfile installation, style checks, strict types, tests, and its initial coverage floor.
- **OP-5** `git mv openpose-react apps/web`. Remove the `firebase` dependency and `src/firebase.ts`. Add Vitest + RTL + MSW; add Playwright and **delete the scaffold spec** (`openpose-vue/e2e/vue.spec.ts` asserted `'You did it!'` and would have failed). **In this same ticket extend `pr.yml` with independent `web-lint`, `web-typecheck`, `web-test`, and `web-build` jobs.** They run in parallel with Python, so React is protected from the first commit in its new location.
- **OP-6** Harden the already-live `pr.yml`: concurrency cancellation for superseded commits, least-privilege `permissions`, dependency caches keyed by lockfiles, immutable pins for third-party actions, stable required-check names, and a local composite `setup-project` action. Target useful parallel feedback in **≤5 minutes** with no model, DB, Docker, network service, or secret.
- **OP-7** ADRs 0001–0006 (FastAPI/Flask · MediaPipe/OpenPose · JWT/Firebase · Postgres+MinIO/Firebase · scale-invariant thresholds · git-history retention), `CONTRIBUTING.md`, `dependabot.yml`, PR template, `CODEOWNERS`, MIT `LICENSE`, and a `main` ruleset that requires pull requests, resolved conversations, an up-to-date branch, linear history, and the stable CI checks. Dependabot covers GitHub Actions, npm, Python, and Docker; group low-risk development updates while keeping security updates separate.

### Epic B — Pose backend
*Leaves: a CLI printing real landmarks for a real image.*

- **OP-20** ⚠️ **SPIKE, DO FIRST.** Verify the `mediapipe` wheel installs on `linux/arm64` + `linux/amd64` under `python:3.12-slim`. Decision → ADR-0002. **Blocks OP-22.**
- **OP-21** `KeypointName` enum, `Landmark`, `PoseFrame` (in `posture-core`, so the core depends on no backend); `PoseBackend` Protocol (`detect`, `warmup`) in `pose-backends/base.py`.
- **OP-22** `MediaPipeBackend`: model load in `__init__`, canonical-name mapping, derived `NECK`, world-landmark passthrough. Ship adapter/mapping/error tests in the same PR using a stubbed MediaPipe task—no model download in required CI.
- **OP-23** `FakePoseBackend` with named presets (`straight`, `hunchback`, `reclined`, `kneeling`, `partial_occlusion`) plus contract tests proving fake and real adapters return the same canonical `PoseFrame` shape.
- **OP-24** `make fetch-model` with a pinned SHA256; `MODEL_PATH` config override.
- **OP-25** `python -m pose_backends.cli <image>` prints a landmark table + `inference_ms`. **In this ticket add `model-validation.yml` with `workflow_dispatch` only**: verify the model hash, run real-model fixture tests, and publish latency/landmark diagnostics. It is available when the real backend becomes demoable but consumes no scheduled CI time.
- **OP-26** *(only if OP-20 fails)* `ONNXMoveNetBackend` behind the same Protocol.

### Epic C — Rules engine (`packages/posture-core`) ⭐
*Leaves: `image → JSON report` from the CLI. This is the resume centerpiece — zero I/O, zero printing, zero globals, fully typed, exhaustively tested.*

Layering: `PoseFrame → metrics.py → Metric → rules.py → Finding → report.py → PostureReport`.

- **OP-30** `geometry.py`: `angle_between`, `signed_angle_to_vertical`, `distance`, `midpoint`, `to_world_vec` + unit tests.
- **OP-31** `thresholds.py` — one frozen dataclass holding every tunable, **injected, never global**, env-loadable in the API layer. Makes tuning a config change and every rule test parameterizable.
- **OP-32** Keypoint resolver with `KeypointStatus{OK, LOW_CONFIDENCE, NOT_DETECTED, OUT_OF_FRAME}` and `MetricStatus{OK, INSUFFICIENT_KEYPOINTS, LOW_CONFIDENCE}`. **No `except Exception` anywhere.** A metric with `status != OK` has `value = None` and produces a `Gap`, not a Finding — so the API can say *"couldn't assess your knees, try a wider shot"* instead of the current silent "Straight back position." **Cheapest, highest-value correctness fix in the project.**
- **OP-33** `trunk_inclination_deg` — signed angle of hip-mid→shoulder-mid vs gravity, world space. *Replaces `checkPosition`; fixes the ear-index inversion and the `None`→"Straight back" false negative.*
- **OP-34** `craniovertebral_angle_deg` — angle at C7 between the ear→C7 vector and horizontal; `<50°` = forward head. *Replaces the geometrically-wrong `evaluate_neck_posture`.*
- **OP-35** `arms_crossed` / `elbow_flexion_deg`, normalized: `abs(forearm − upper_arm) / torso_px < 0.15`. *Replaces the ±100 px literal — whose own inline comment admits it "shall be replaced with a calculation which can adjust to different sizes of people." Quote that in the README, then show it fixed.*
- **OP-36** `knee_flexion_deg` — hip–knee–ankle angle, world space. *Replaces `checkKneeling`; fixes the `UnboundLocalError`.*
- **OP-37** `heel_contact` via heel + foot_index. **New capability the original never delivered.**
- **OP-38** `view_confidence` (lateral vs frontal, from shoulder-width : torso ratio) + `quality.gaps`. The original app *told* users "this image must be taken from a side angle" and never enforced it.
- **OP-39** `report.py`: `PostureReport`, `overall_score`, `schema_version`, `rules_version`.
- **OP-40** `tests/builders.py` — `make_pose(trunk_deg=…, knee_deg=…)` constructs landmarks analytically from a parameterized stick figure. Tests read as `assert metric(make_pose(trunk_deg=35)).value == approx(35, abs=1.0)`.
- **OP-41** **Hypothesis property tests:** for any pose, uniform scale `s ∈ [0.3, 3.0]`, and translation, every angular metric is invariant to 1e-6. **This test *is* the proof the redesign fixed the original defect** — it would fail catastrophically against `posture_image.py`. Put it in the README.
- **OP-42** Boundary tests (±ε at every threshold), degradation tests (drop each keypoint, assert correct status, assert nothing raises), golden-report snapshots. Enable `--cov-fail-under=95` scoped to this package.
- **OP-43** Extract thresholds + golden fixtures into `packages/posture-spec/`. Wire into the CLI: `python -m pose_backends.cli --report <image>` emits the full JSON report. **In this ticket create `scientific-validation.yml`** with golden snapshots, threshold/config validation, and the initial Python scientific gates. **A genuinely impressive demoable milestone with zero web stack.**
- **OP-44** Scientific property suite beyond scale/translation: mirror-consistency for symmetric metrics, valid physical domains for all angles, confidence monotonicity (reducing required-keypoint confidence can never increase finding confidence), deterministic reports for identical inputs, and explicit abstention when evidence is insufficient. These properties run in `scientific-validation.yml` and are part of the capstone's correctness argument, not optional test polish.
- **OP-45** Versioned evaluation-data contract: `evaluation/manifest.csv`, `evaluation/quality-gates.yml`, `evaluation/baseline.json`, and `make validate-data`. Extend `scientific-validation.yml` in the same PR with checks for duplicate IDs/hashes, corrupt or orphaned images, invalid labels, missing provenance/license/consent fields, unexpected dimensions, EXIF/PII leakage, split leakage, and class-distribution changes.

### Epic D — 🎯 WALKING SKELETON
*Leaves: real image in → real result on screen. **The highest-value milestone in the plan** — the first moment the app stops lying. Everything before is scaffolding; everything after is enrichment. Tag `v0.1.0` here.*

- **OP-50** FastAPI app factory, `config.py` (pydantic-settings), `structlog` + request-ID middleware, RFC 9457 `application/problem+json` error handler, `GET /health` + `/health/ready`. Ship health/error/config tests in the same PR and extend `pr.yml` with the API's ruff, mypy `--strict`, pytest, and **85% coverage** gate immediately—the API never exists without its checks.
- **OP-51** `lifespan` loads the pose backend **once at startup** + `warmup()`, exposed via `get_pose_backend`. *This is exactly the problem `RUNDOWN.md`'s Open Items flagged — "a cold load is slow, per-request loading would be unusable" — solved properly.*
- **OP-52** `StorageBackend` Protocol + `LocalDiskStorage` + `S3Storage` (MinIO).
- **OP-53** `POST /api/v1/analyses` (multipart): 10 MB limit, content-type allowlist, EXIF-orientation correction, decode → `detect()` → `build_report()` → `201`. **No auth, no DB yet—in-memory.** Add endpoint contract tests for valid upload, limits/types, decode failure, no-person, low-confidence, and backend failure in this ticket.
- **OP-54** `docker-compose.yml` with `api` + `web` only; Vite `server.proxy` sends `/api` → `api:8000`—**kills every CORS and base-URL problem at once. In this same ticket create `containers.yml`** to build both images, validate Compose, start with fake backends, wait for readiness, smoke-test API/web, capture logs on failure, and shut down cleanly. Docker cannot merge without a working container check.
- **OP-55** Rewrite `apps/web/src/views/Dashboard.tsx`: **delete `POSTURE_DETECTION_RESULT`, `WORKOUT_RESULT`, and `setTimeout(…, 5000)`**; real upload with progress; render real metrics/findings; explicit error and "no person detected" states.
- **OP-56** `openapi-typescript` codegen + typed `apiClient`; delete the hardcoded `axios.get('http://127.0.0.1:5000/')` in `HelloWorld.tsx:17`. **Create the OpenAPI contract job in this ticket**: regenerate schema/types, fail on committed drift, run `tsc`, validate examples, and flag unreviewed breaking changes.
- **OP-57** Skeleton overlay drawn **client-side on `<canvas>`** from returned landmarks — no server-side image writing, no extra round trip, and it looks great in the GIF.
- **OP-58** First Playwright E2E: upload fixture → assert a real metric value appears. **Create `e2e.yml` in this ticket**, using fake pose/template coaching and uploading Playwright traces, screenshots, video, and Compose/API logs on failure.
- **OP-59** README v1 with a screenshot of a real result. **Ship it. Tag `v0.1.0`.**

### Epic E — Persistence + auth
*Leaves: multi-user app with history.*

Postgres 16 + SQLAlchemy 2.0 (typed `Mapped[]`, async/asyncpg) + Alembic + MinIO.

Tables: `users` · `refresh_tokens` · `sessions` · `analyses` · `keypoints` · `metrics` · `findings`. Design notes worth defending in an interview: `keypoints` is a **table, not a JSONB blob**, so trend queries are plain indexed SQL; every analysis stamps `pose_backend` + `rules_version` + `schema_version` so results stay interpretable after retuning; the DB stores **object keys, not URLs** — the storage layer owns URL construction.

- **OP-60** Postgres + MinIO in compose with healthchecks; `minio-init` one-shot bucket bootstrap. **Create `integration.yml` in this ticket** with service health, connectivity, bucket-bootstrap, clean startup, and cleanup checks; extend `containers.yml` to exercise the expanded stack.
- **OP-61** SQLAlchemy 2.0 models; async engine + session dependency.
- **OP-62** Alembic init + initial migration; entrypoint `upgrade head` behind an advisory lock. Extend `integration.yml` in this ticket with fresh upgrade → downgrade → re-upgrade, concurrent-startup/advisory-lock behavior, and SQLAlchemy-model/autogenerate drift detection.
- **OP-63** Repositories + `testcontainers[postgres]` integration tests.
- **OP-64** Persist analyses: original → MinIO; write `analyses`/`keypoints`/`metrics`/`findings`.
- **OP-65** `GET /analyses/{id}`, `GET /analyses` (cursor-paginated), `DELETE /analyses/{id}`.
- **OP-66** Auth: `argon2-cffi` (argon2id) — **not `passlib`, which is effectively unmaintained**. `PyJWT` HS256; 15-min access token held **in memory, never `localStorage`**; 30-day opaque refresh token stored **hashed**, delivered `HttpOnly; SameSite=Lax; Secure`, rotated on every use, with reuse-of-rotated-token revoking the whole family (replay detection). `config.py` refuses to boot if `JWT_SECRET` is the dev default while `ENV=production`. Ship the full auth abuse-case suite and **create `security.yml` in this ticket** with dependency review, CodeQL, secret detection, and dependency/license policy; extend it with container scanning once production images exist.
- **OP-67** `get_current_user`; **scope every query by `user_id` at the repository layer, not the route.** Another user's analysis returns **404, not 403** — don't leak existence. Tested.
- **OP-68** Frontend `AuthContext` + axios 401-refresh interceptor with a **single-flight guard** so concurrent 401s don't fire N refreshes. Keep `ProtectedRoute`'s `checking` state — it was correct. Delete all Firebase code and the committed config.
- **OP-69** History view: past analyses with thumbnails + a trend sparkline for `trunk_inclination_deg`.
- **OP-70** Rate limiting (`slowapi`) on `/auth/login` (5/min/IP) and `/analyses`.

### Epic F — LLM coaching
*Leaves: personalized, streaming, metric-grounded feedback — what your posture is doing now, plus exercises chosen for your specific findings — persisted so progression is visible over time.*

**The LLM sits strictly off the critical path.** `POST /analyses` returns the deterministic report immediately and never waits on a model; coaching is a separate, cached, explicitly-requested call. The app stays fully functional when the LLM is down, unconfigured, or over budget. **Only the structured report is sent — never the image.** Cheaper, faster, and no user photo leaves the machine: real privacy-by-design, worth stating in the README.

**Provider sequencing: local first, paid last.** Ollama runs locally and free as the first real model (OP-65); Anthropic on `claude-haiku-4-5` lands last (OP-75), behind an explicit spend gate, once the app is confirmed working end to end. Prompt iteration is where a paid API bleeds — dozens of runs while the prompt is still wrong. Doing that locally costs nothing, and Anthropic is billed only for calls already known to work. **OP-63 through OP-74 cost nothing.** The caveat, stated honestly: a prompt tuned on a small local model isn't *optimal* for a hosted one, but the expensive parts — schema, guardrails, serializer, snapshot test — transfer completely.

**The exercise library is data, not prompt text.** If exercises live only as prose inside a system prompt, the model invents them — and a posture app confidently inventing stretches is the one failure mode that makes the project look unserious. As a versioned data module the library is reviewable, citable, diffable, and assertable. The LLM selects and phrases; it never generates. Retrieval is an **exact-match lookup by finding code** — the rules engine already computed the key, so there is no vector database, no embeddings, and no RAG.

**Grounding is a tested property.** `recommendations` are structured (`finding_code`, `metric_name`, `metric_value`, `exercise_id`, `why_this_helps`, `how_to`), so every claim is checkable against the report and the library. **Abstention is respected**: no exercise for a metric Epic C could not measure.

**Coaching persists as rows, not a text blob** — the same argument Epic E makes about `keypoints`, one layer up. Prose cannot answer "which findings keep recurring"; rows can. Analysis history itself belongs to Epic E (OP-54, OP-58); OP-71 extends it rather than rebuilding it.

- **`OP-63`** `packages/posture-coaching`: ~100 curated exercises across 10 groups keyed by finding code, each with a source citation and contraindications. Includes two **maintenance** groups, because good posture emits no finding and a healthy user must still receive something; excludes `frontal_view`, which is camera guidance, not a posture state. A test enumerates every code `rules.evaluate` can emit and **fails CI on a gap**. *(1.5–2 days — the largest content item in the epic.)*
- **`OP-64`** `LLMClient` Protocol + `FakeLLMClient` + **`TemplateLLMClient`** (Jinja over the library, no network) + the `CoachingResponse`/`Recommendation` schema. **`LLM_PROVIDER=template` is the default in `.env.example`**, so `docker compose up` yields a fully working app with real analysis and real recommendations, **no API key and no model download**. A recruiter cloning the repo gets a working demo in one command; that is worth more than streaming.
- **`OP-65`** `OllamaLLMClient` — structured output via Ollama's JSON-schema `format`. Model **pinned by tag, never `:latest`**, pulled with `make fetch-llm` into `~/.ollama` and therefore never in the repo tree — the Epic B `fetch-model` pattern, not the pre-v2 Dropbox link. Ollama runs on the **host**, not in Compose: Docker Desktop on macOS has no GPU passthrough. Unreachable → falls back to the template client.
- **`OP-66`** System prompt + report→prompt serializer, **provider-neutral**. Guardrails: recommend only from the supplied library; nothing for an abstained metric; never diagnose; never contradict the numeric metrics; one recommendation per finding; always cite the measured value; general guidance, not medical advice. Prompt snapshot test.
- **`OP-67`** Coaching persistence: `coaching` (one row per analysis, unique on `analysis_id`) + `coaching_recommendations` rows, stamped with `llm_provider`, `llm_model`, `prompt_version`, `library_version` so history survives the OP-75 swap. Alembic migration, reversible.
- **`OP-68`** `POST /analyses/{id}/coaching` — idempotent via the unique constraint, **one generation per analysis ever**. This is the real cost control: spend scales with distinct analyses, not page views.
- **`OP-69`** `GET /analyses/{id}/coaching/stream` (SSE), persisting on completion only — a mid-stream disconnect must not leave a half-written row.
- **`OP-70`** Frontend consumer using **`fetch` + `ReadableStream`, not `EventSource`** — `EventSource` can't set an `Authorization` header and the access token lives in memory. Comment the reason. Graceful non-streaming fallback.
- **`OP-71`** Coaching in history + recommendation progression: which findings recur, which have stopped, which exercises were recommended and when — beside the existing `trunk_inclination_deg` sparkline. Present the series adjacently; correlation is not proof the exercises worked, and overclaiming would undo the credibility Epic C's abstention work bought.
- **`OP-72`** `max_tokens=1500`; per-user rate limit (10/hr); `LLM_MONTHLY_TOKEN_BUDGET` silently falling back to `TemplateLLMClient`; token usage recorded per analysis **for every provider including Ollama**, so OP-75's cost estimate comes from measured counts rather than a guess. With a local model the binding constraint is the machine, not money.
- **`OP-73`** `GET /sessions/{id}/summary` — trend narrative over the last N analyses (one indexed query, thanks to the normalized schema), cached 1 h. What makes it read as a product rather than a one-shot classifier. **Cut line #1.**
- **`OP-74`** Tests: grounding properties, Protocol conformance across all four clients, SSE frame parsing and disconnect, the concurrent-generation race, budget and outage fallbacks, tenancy 404s. **CI never requires Ollama, a model, a key, or network** — a test that quietly depends on a running Ollama is the same class of defect as the Dropbox-hosted model.
- **`OP-75`** `AnthropicLLMClient` on `claude-haiku-4-5` via `LLM_MODEL`, **config-only swap**. `output_config.effort` errors on Haiku; thinking uses `budget_tokens` and should be off; handle `stop_reason == "refusal"` before reading content (HTTP 200, not an error). **The cacheable prefix minimum is 4096 tokens on Haiku 4.5** — an ~800-token system prompt won't cache and fails *silently*, so either accept it or grow the prompt deliberately; never ship a cache marker that can't fire. `config.py` refuses to boot on a paid provider without `LLM_ALLOW_PAID_PROVIDER=true`. ≈**$0.0034/call**.

### Epic G — Browser-side live mode
*Leaves: real-time skeleton + posture verdict in the browser. Replaces `posture_realtime.py`, which is deleted (90% copy-paste, a broken `cap.set(100, …)` resolution call, and a `config_reader()` re-parse on every single frame).*

- **OP-100** `@mediapipe/tasks-vision` WASM in a Web Worker; `getUserMedia`; skeleton overlay on `<canvas>`. **Frames never leave the browser** — a real privacy story and zero server load.
- **OP-101** `packages/posture-core-ts` — trunk inclination, craniovertebral angle, shoulder asymmetry, evaluated per frame against `posture-spec/rules.json`.
- **OP-102** Temporal smoothing (rolling median over ~15 frames) so the verdict doesn't flicker.
- **OP-103** `POST /api/v1/sessions` at session end with **aggregates only**: duration, time-in-good-posture, verdict histogram. No video, no frames.
- **OP-104** Vitest runs the shared golden corpus; **wire the Python-vs-TS parity diff into CI here** — do not defer it to Epic H.

### Epic H — Polish and proof
*Leaves: the thing you link on a resume.*

- **OP-110** CI audit and optimization—not initial CI implementation. By this point `pr`, `integration`, `scientific-validation`, `e2e`, `security`, and `containers` already exist because each arrived with its capability. Audit stable check names, least privilege, action pins, cache correctness, parallel job dependencies, failure artifacts, branch rules, runtime/cost, and documentation. There is deliberately **no deployment workflow**.
- **OP-111** Harden the existing on-demand `model-validation.yml`: verify SHA256, run real inference over the full evaluation set, generate metric/performance regressions, and record the environment manifest. Trigger it manually before evaluation/model/rules releases and automatically from `release.yml`; **do not schedule it nightly**.
- **OP-112** Prod compose overlay: nginx-served web, non-root containers, multi-stage prod images, **image-size budget assertion (<600 MB API)**, Compose-config validation, healthcheck smoke test, vulnerability scan, secret/layer inspection, and amd64+arm64 build verification. **Extend the already-required `containers.yml` in the same PR.** Images are build artifacts for reproducibility; nothing is deployed.
- **OP-113** README rewrite: hero GIF, architecture diagram, quickstart, a "what I changed and why" section built from the bug table above, ADR links.
- **OP-114** Record `docs/images/demo.gif`: upload → skeleton overlay → metrics → streaming coaching.
- **OP-115** **`docs/evaluation.md` + `make evaluate`** — verify data/model hashes, run `opresults.py`-style evaluation from the frozen environment, and publish old vs new: accuracy, balanced accuracy, per-class precision/recall/F1/support, macro-F1, confusion matrices, **assessment coverage/abstention rate**, and bootstrap confidence intervals. Emit git/model/rules/schema/data/environment versions and runtime. Report coverage beside classification quality so the engine cannot appear to improve merely by refusing difficult samples. Document sample size, provenance, exclusions, limitations, threshold-selection method, and threats to validity. **Extend `scientific-validation.yml` in this same ticket** to reproduce results within documented tolerances and fail on unexplained baseline drift. **The strongest single artifact you can produce: it demonstrates the improvement rather than asserting it.**
- **OP-116** `CHANGELOG.md`, repo description/topics, tag `v1.0.0`. `release.yml` reruns the release gate and produces a **local-use release bundle**, not a deployment: Compose files, `.env.example`, evaluation summary, model/environment metadata, SBOMs, image digests, and `SHA256SUMS`.

---

## Testing strategy

| Layer | Tools | What | Gate |
|---|---|---|---|
| `posture-core` | pytest + **Hypothesis** | Every metric on synthetic fixtures; scale/translation invariance; mirror consistency; physical domains; confidence monotonicity; determinism; boundaries; every degradation/abstention path; golden snapshots | **95%**, enforced |
| `pose-backends` | pytest `@pytest.mark.model` | Real MediaPipe on fixture images; asserts person detected, landmark coverage, plausible metrics, and performance budgets. **Deselected in required PR CI; exercised on demand and before releases.** | Manual/release |
| API unit | pytest + `httpx.ASGITransport` | Routers/services with fakes via `dependency_overrides`; auth flows, validation, 401/404, pagination | 85% |
| API integration | pytest + `testcontainers[postgres]` | Real Postgres, real Alembic, real repositories, fake pose + fake LLM; full round trip | — |
| Migrations | pytest | `upgrade head` → `downgrade base` on a fresh container; assert no autogenerate diff vs models (**catches drift**) | — |
| Cross-language | pytest + vitest | `posture-spec/golden/` through both engines; **CI fails on any disagreement** | — |
| API contract | FastAPI OpenAPI + `openapi-typescript` | Regenerate OpenAPI/TS types; fail on uncommitted drift or an unreviewed breaking contract change | Required |
| Evaluation data | custom Python validator | Manifest schema, image integrity, duplicate hashes/IDs, provenance, leakage, EXIF/PII, class balance | Required when data changes |
| Scientific evaluation | scikit-learn + bootstrap resampling | Accuracy, balanced accuracy, per-class and macro metrics, confusion matrix, coverage/abstention, confidence intervals | Regression budget |
| Frontend unit | Vitest + RTL + **MSW** | Dashboard states, `ProtectedRoute`, auth interceptor, SSE consumer | 70% |
| E2E | Playwright | register → login → upload → real metrics → stream coaching → history → logout | 1 happy + 2 error paths |
| Containers | Buildx + Compose + vulnerability scanner | From first Compose commit: build/config/readiness/smoke/cleanup; later add multi-stage/non-root, amd64+arm64, size budget, healthcheck, layer/secret scan | Required from first Docker commit |

**Testing the model endpoint without running the model**, concretely: `FakePoseBackend` returns a canned `PoseFrame` built by the *same* `make_pose()` builder the core tests use (sub-millisecond, no decode). It's injected via `app.dependency_overrides[get_pose_backend]`. And `POSE_BACKEND=fake` in `config.py` means **the entire app runs backend-free** — which is what CI's compose smoke test uses, so CI never downloads a model.

## CI and GitHub Actions

The repository is **not publicly hosted**, so there is no deployment pipeline. The automation exists to prove five claims: the software is correct, the data is valid, the scientific results are reproducible, cross-layer contracts do not drift, and a clean checkout produces a secure production-like local system. Workflows use least-privilege permissions, immutable pins for third-party actions, lockfile-keyed caches, concurrency cancellation for superseded PR commits, and uploaded diagnostics only on failure.

### Progressive activation map

CI grows with the architecture; Epic H only audits and hardens it.

| Capability first becomes real | Same-ticket automation |
|---|---|
| Python workspace/packages (OP-4) | Python format, lint, strict types, unit tests, coverage |
| React app (OP-5) | Frontend format, lint, strict types, Vitest coverage, production build |
| Real pose CLI (OP-25) | On-demand real-model validation; no schedule |
| Shared posture spec (OP-43) | Scientific/golden validation |
| Evaluation manifest (OP-45) | Data-quality and leakage validation |
| FastAPI app (OP-50) | API lint/type/unit/coverage gates |
| First Docker Compose stack (OP-54) | Image build, Compose validation, readiness, smoke, cleanup |
| Generated OpenAPI client (OP-56) | Schema/type regeneration and drift/breaking-change check |
| First critical full-stack journey (OP-58) | Playwright E2E with diagnostic artifacts |
| Postgres + MinIO (OP-60) | Integration services and full-stack startup checks |
| Alembic (OP-62) | Upgrade/downgrade/re-upgrade and model/schema drift |
| Authentication (OP-66) | Abuse-case tests plus security workflow |
| TypeScript rules mirror (OP-104) | Required Python↔TypeScript golden parity |
| Production Compose overlay (OP-112) | Non-root/multi-arch/size/vulnerability/layer checks |
| Scientific evaluation (OP-115) | Reproducibility and metric-regression gates |
| Version tag (OP-116) | Local release bundle, SBOM, digests, checksums; no deployment |

### Required pull-request workflows

- **`pr.yml`** starts at OP-4 with Python jobs, expands at OP-5 with React jobs, and expands at OP-50 with API-specific coverage. Frozen installs; ruff + oxlint + Prettier; mypy `--strict` + `tsc --noEmit`; Python 3.11/3.12 matrix; `posture-core` 95%, API 85%, React 70%; production web build. Independent jobs run in parallel. Fast target: **≤5 minutes**.
- **`scientific-validation.yml`** starts with the shared spec at OP-43, then gains property, data-quality, parity, and evaluation-regression jobs as those capabilities appear. This is the capstone's signature workflow.
- **`integration.yml`** starts with Postgres/MinIO at OP-60 and immediately protects service startup; Alembic and repository/auth/storage round trips are added by their implementation tickets.
- **`e2e.yml`** starts with the first walking-skeleton journey at OP-58 and grows only for critical cross-layer behavior. It uses `POSE_BACKEND=fake` and `LLM_PROVIDER=template`; failures upload traces, screenshots/video, API logs, and Compose logs.
- **`containers.yml`** is required from OP-54, the first Docker/Compose commit. It begins with build/config/readiness/smoke/cleanup and grows with Postgres, MinIO, production images, multi-architecture support, budgets, and scans.
- **`security.yml`** starts when authentication is introduced. Dependency review is required on PRs; CodeQL, secret detection, license policy, and image/dependency vulnerability scans run on `main` and weekly. New high/critical findings fail unless a time-bounded exception is documented.

Required check names stay stable and are enforced by the `main` ruleset. Avoid path-filtering an entire required workflow because a skipped workflow can leave a required check pending; trigger it and skip irrelevant internal jobs instead. If a merge queue is enabled later, add `merge_group` to every required workflow.

### Scientific regression policy

`evaluation/quality-gates.yml` owns policy rather than embedding unexplained numbers in workflow YAML. Initial gates are set only after the baseline dataset is audited, but the shape is fixed:

```yaml
metrics:
  macro_f1:
    minimum: <baseline-derived>
    maximum_regression: 0.02
  assessment_coverage:
    minimum: <baseline-derived>
    maximum_regression: 0.05
  per_class_recall:
    minimum: <baseline-derived>
    maximum_regression: 0.05
```

Baseline updates are intentional review events: the PR must include the regenerated evaluation report, changed baseline, reason, fixture/model/rules versions, and a discussion of regressions. CI never silently accepts or overwrites a new baseline.

### On-demand model validation and releases

- **`model-validation.yml`** — invoked manually with `workflow_dispatch` and by `release.yml`, not on a nightly schedule. Verify SHA256; run the curated evaluation set; enforce tolerant metric and latency/memory budgets; upload metrics, confusion matrices, regression diff, and environment manifest. Numerical comparisons use documented tolerances, not byte equality.
- **`release.yml`** — triggered by `v*.*.*`; reruns release gates and creates a reproducible local bundle with Compose configuration, metadata, evaluation summary, SBOMs, digests, and checksums. It does **not** deploy or require cloud credentials.

**Every PR runs with no model weights, no `ANTHROPIC_API_KEY`, no running Ollama, and no write-capable external credential.** Deterministic fake backends keep application CI stable; on-demand/release validation supplies separate evidence about real-model behavior without paying for redundant scheduled runs.

## Docker

`db` (postgres:16-alpine, `pg_isready` healthcheck) · `minio` + `minio-init` · `api` (uvicorn `--reload`, bind-mounts `apps/api/src` and `packages/*/src`, `depends_on: service_healthy`, `/health/ready` checks DB + storage + `pose_backend.is_loaded`) · `web` (vite `--host`, `/api` proxied to `api:8000`).

**Model weights:** a build stage `ADD`s the 9 MB `.task` from its published URL with a **pinned SHA256** into the final layer. Self-contained, reproducible, verifiable. `MODEL_PATH` overrides for local dev. The legacy 209 MB `model.h5` never enters any image.

**Container gotcha to pre-empt:** use `opencv-python-headless`, never `opencv-python`, and pin `libgl1` + `libglib2.0-0` in the base image — that pair is the #1 cause of "works locally, ImportError in Docker" with cv2.

---

## Cut lines, in order

Cut bottom-up. The first four are nearly free; past #4 you start losing signal.

1. `GET /sessions/{id}/summary` (`OP-73`) — *~1 day*
2. MinIO → `LocalDiskStorage` on a named volume; the Protocol already exists — *~1 day*
3. SSE streaming (`OP-69`/`OP-70`) → non-streaming with a spinner — *~1 day*
4. Multi-arch Docker; build amd64 only in CI — *~0.5 day*
5. **Epic G live mode entirely** — the most self-contained epic; nothing depends on it, and `posture-spec` still pays for itself as the Python engine's config — *~3 days*
6. Playwright → keep exactly one happy path (don't drop it; "has E2E tests" is a checkbox recruiters look for) — *~0.5 day*
7. `heel_contact` + `shoulder_asymmetry` — four solid metrics beat six shaky ones — *~1 day*
8. Alembic → `create_all()`. **This one hurts** — "no migrations" is a visible gap in a backend portfolio piece. Only under real pressure.

### Never cut

- **Epic D, the walking skeleton.** The mocked dashboard is the single thing making this repo unpresentable.
- **`posture-core` at 95% coverage.** The load-bearing evidence of mid-level ability — and the cheapest thing in the plan.
- **`docker compose up` working with no accounts, no API key, and no model download.** `LLM_PROVIDER=template` + `TemplateLLMClient` + the `posture-coaching` library is what makes that true; guard it with the CI smoke test.
- **The README with a real GIF.** Most people evaluating this will never run it.
- **ADR-0002 and ADR-0005 + OP-115.** They convert "I picked MediaPipe" into "I evaluated three backends against six criteria," and "I changed some numbers" into "the original thresholds were raw pixels — here's the invariance property test and the old-vs-new evaluation."

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| No MediaPipe `linux/arm64` wheel | Medium | **High** — no local Docker demo on your M-series Mac | **OP-20 spike first.** ONNX MoveNet fallback is ~150 lines behind the same Protocol; `posture-core` untouched |
| MediaPipe pins (`protobuf`, `numpy<2`) conflict with the FastAPI/SQLAlchemy stack | Medium | Medium | Resolve with `uv` at OP-3/OP-22. Worst case, run the pose backend as its own compose service over HTTP — the Protocol makes that a swap, and it's a fine story |
| Cross-language drift between the two rules engines | Medium | Medium | `rules.json` + golden corpus; **parity job lands in OP-104, not deferred to Epic H** |
| Retuned thresholds classify *worse* than the originals | Medium | Medium | OP-115's evaluation is the detector; thresholds are one injected dataclass, so tuning is config, not code |
| Scope creep into Epic F (the LLM is the fun part) | **High** | Medium | Hard rule: **Epic D ships and is tagged before any LLM code exists** |
| Host Python 3.14 ≠ container 3.12 | High | Low | Docker is the supported path; document `uv python install 3.12` |

## Verification

1. `git clone && cp .env.example .env && docker compose up` on a clean machine, **no API key, no accounts** → `/health/ready` ok, `localhost:5173` loads.
2. `pytest packages/posture-core -q --cov-fail-under=95` — passes with no network, no model, no DB.
3. `pytest apps/api -q -m "not model"` — passes with fake pose + fake LLM.
4. Run `pytest -m model` locally and dispatch `model-validation.yml` on demand — real MediaPipe passes on `fixtures/images/`; no nightly schedule exists.
5. Golden-parity: same corpus through Python and TS, zero verdict diffs.
6. Upload `OP55.jpeg` (the image `RUNDOWN.md` verified against the old model) via the UI → canvas skeleton + real metrics; confirm a *frontal* photo is rejected by `view_confidence`.
7. `curl -X POST localhost:8000/api/v1/analyses -F "image=@fixtures/images/OP55.jpeg"` returns typed JSON; `/docs` renders the schema.
8. Request coaching with `LLM_PROVIDER=template`, then `=ollama`, then `=anthropic`; confirm each cites **actual measured angles** and recommends only library exercises matched to the findings actually present. Request it twice and confirm the second call regenerates nothing.
9. Live mode: webcam skeleton tracks with a stable, non-flickering verdict.
10. Register user B, request user A's analysis id → **404**.
11. `npx playwright test` green; push a branch → independent Python, React, API, contract, container, integration, scientific, security, and E2E jobs run in parallel where dependencies allow and finish green **with zero secrets configured**.
12. `make validate-data` passes; deliberately duplicate an evaluation image under a new id → duplicate-hash detection fails.
13. `make evaluate` from a frozen clean environment reproduces the committed metrics within documented tolerances and emits the git/model/rules/schema/data/environment manifest.
14. Deliberately regress one class beyond `evaluation/quality-gates.yml` → `scientific-validation.yml` fails and publishes the metric/confusion-matrix diff without rewriting the baseline.
15. Tag a release candidate → the workflow produces the local-use bundle, SBOMs, digests, and checksums, and performs **no application deployment or cloud-infrastructure mutation**.
