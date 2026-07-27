# Contributing

OpenPosture is a rewrite of a 2024 university capstone. The original is preserved under
`docs/archive/` and audited in [`docs/FINDINGS.md`](docs/FINDINGS.md); the plan being executed is
[`docs/V2-PLAN.md`](docs/V2-PLAN.md). Reading the audit first will explain most of the conventions
below — nearly every one exists because its absence caused a specific defect.

## Setting up

Python is a [`uv`](https://docs.astral.sh/uv/) workspace; the frontend is a separate npm package.

```bash
uv python install 3.12          # workspace targets 3.12; 3.11 is also supported and CI-tested
uv sync --all-packages          # every workspace member, editable, one environment

cd apps/web && npm ci           # frontend, exact lockfile install
```

Optional but recommended — the same lint and format checks CI runs, on every commit:

```bash
uv tool install pre-commit && pre-commit install
```

## The architectural rule

```
posture-core  <--  posture-spec  <--  pose-backends  <--  apps/api
```

One direction, no cycles, and nothing depends on `apps/api`.

`posture-spec` sits where it does for one reason: it parses `rules.json`, and parsing JSON is I/O.
The package that owns the numbers is therefore one level *out* from the engine that uses them,
depending on the core rather than the other way round.

**`posture-core` may depend on numpy and the standard library. Nothing else — ever.** Not FastAPI,
not Pydantic, not MediaPipe, not a logger, not a settings object. That constraint is what lets the
rules-engine suite run in seconds with no model download, no container and no database, and it is
what makes the inference backend swappable without touching a single rule.

It is enforced by `packages/posture-core/tests/test_dependency_isolation.py`, which checks both the
declared metadata and what a fresh interpreter actually imports. If you find yourself wanting an
exception, the thing you want almost certainly belongs in `pose-backends` or `apps/api`.

## Workflow

One branch and one pull request per Jira ticket, named `op-<number>-<kebab-slug>`.

When a ticket depends on unmerged work, branch from that branch rather than waiting — pull requests
stack, and the base branch is noted in the body so the review order is obvious.

**The pull request body is where the work is explained.** It is read to understand the code, not
merely to approve it, so it should say why the approach was chosen and what was rejected. The
commit message stays short: a subject line and, if genuinely non-obvious, a couple of lines of why.
Depth goes in the pull request, not duplicated into git.

## Quality gates

Everything below runs in CI as [`pr.yml`](.github/workflows/pr.yml); `ci-ok` is the single
required check. [`scientific-validation.yml`](.github/workflows/scientific-validation.yml) runs
alongside it, aggregated by `scientific-ok`, and asks a different question — see
[below](#the-scientific-gates).

```bash
# Python
uv run ruff check . && uv run ruff format --check .
uv run mypy packages apps
uv run pytest -m "not model"

# Frontend
cd apps/web
npm run lint && npm run format:check && npm run typecheck
npm run test:coverage
npm run test:e2e
```

Two coverage floors, chosen rather than defaulted:

- **`posture-core` — 95%.** No I/O, no model, no database, so there is no honest excuse for an
  unexercised branch.
- **`apps/web` — 70%.** Applies to branches and functions as well as lines, because a lines-only
  gate is easy to clear while leaving whole branches untested.

Clear a floor by writing a test, not by adding an exclusion. Generated code and type-only modules
may be excluded; a difficult branch may not.

`-m "not model"` deselects tests needing real model weights. CI never downloads them; they run on
demand in `model-validation.yml`.

To run them locally you need the weights and the inference stack:

```bash
make fetch-model                       # downloads and verifies a pinned SHA256
uv pip install "mediapipe==0.10.18"    # the optional extra; ~857 MB of site-packages
uv run pytest -m model
```

`make fetch-model` writes to `models/`, which is gitignored — the checksum is what is version
controlled, not the weights. `MODEL_VARIANT=lite|full|heavy` switches variants at fetch time and
`MODEL_PATH` overrides the location at run time; both are documented in
[`models/checksums.txt`](models/checksums.txt).

Most of the pose-backend suite deliberately does *not* need any of this. The adapter's landmark
mapping is tested against a stub, and the fake backend runs the whole pipeline with no model at
all — which is why the pull-request workflow finishes in minutes without a download.

### The scientific gates

`pr.yml` asks whether the change broke the software. `scientific-validation.yml` asks whether the
engine still measures what it claims to measure, which is a different failure and deserves a
separate red mark. Three jobs — invariance properties, the golden corpus, and threshold drift —
each runnable locally:

```bash
uv run pytest packages/posture-core/tests/test_properties.py   # scale, translation, degradation
uv run pytest packages/posture-core/tests/test_boundaries.py   # ±ε at every threshold
uv run pytest packages/posture-core/tests/test_golden.py       # the report snapshot corpus
uv run pytest packages/posture-spec                            # rules.json vs Thresholds drift
```

The invariance properties are the load-bearing ones: they are the proof that the redesign fixed
the original engine's central defect, and they would fail catastrophically against
`posture_image.py` ([ADR-0005](docs/adr/0005-scale-invariant-metrics.md)).

Golden snapshots are regenerated deliberately, never by an environment variable the test run
checks:

```bash
uv run python packages/posture-core/tests/regenerate_golden.py
```

**Read the diff — it is the review.** A retuned threshold should visibly move verdicts and
confidences across the corpus; anything it moves that you did not expect is the finding. CI runs
the same script and fails if it produces a diff, so stale snapshots cannot ride along in someone
else's pull request.

`scientific-ok` is not yet in the `main` ruleset's required checks — only `ci-ok` is — so treat a
failure here as blocking by convention rather than relying on GitHub to stop you
([`.github/main-ruleset.md`](.github/main-ruleset.md)).

## Conventions worth knowing

**Pinned versions are usually load-bearing.** `mediapipe==0.10.18` is the last release publishing
linux `aarch64` wheels, and `numpy<2` / `protobuf<5` are ceilings it imposes on the whole
workspace. Dependabot is configured not to propose bumps to any of them. See
[`docs/adr/0002-mediapipe-pose.md`](docs/adr/0002-mediapipe-pose.md).

**Thresholds are not literals in code.** They live in
[`packages/posture-spec/src/posture_spec/rules.json`](packages/posture-spec/src/posture_spec/rules.json),
loaded into the frozen `Thresholds` dataclass the engine takes as an argument. The TypeScript
mirror in Epic G will load the same file, so retuning is one change in one place and the two
implementations cannot drift. Magic numbers in pixels are the defect described in FINDINGS §2.6.

Adding a tunable means adding it in both places. `packages/posture-spec/tests/test_spec.py`
fails if a field exists in the dataclass but not the file, or the reverse, or if the shipped
values do not round-trip — so drift is a red build rather than a surprise later.

**A metric that cannot be computed returns a gap, never a guess.** The single most damaging
behaviour in the original was reporting "Straight back position" whenever assessment failed —
a diagnostic tool defaulting to "you're fine" when it cannot see you. Absence of evidence must
stay distinguishable from evidence of absence.

**`docs/archive/` is immutable.** FINDINGS cites it by line number, so it is excluded from every
formatter and linter. Do not tidy it.

## Architecture decisions

Decisions expected to outlive their ticket are recorded in [`docs/adr/`](docs/adr/). An ADR is
never edited to change its conclusion — it is superseded by a new one that says what changed and
why. See [`docs/adr/README.md`](docs/adr/README.md).
