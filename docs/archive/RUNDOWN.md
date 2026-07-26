# Setup Rundown — 2026-07-19

A record of getting OpenPosture running locally on macOS (Apple Silicon), what
was broken, and what remains. Companion to [RUNNING.md](RUNNING.md), which has
the step-by-step instructions; this file explains the *why* behind them.

## Starting State

| Component | Location | Status on arrival |
| --- | --- | --- |
| Vue frontend | `openpose-vue/` | Ran once deps installed |
| Flask API | `API/app.py` | Broken — would not import |
| Model scripts | `API/posture_image.py`, `API/posture_realtime.py` | Blocked — no usable Python |
| Model weights | `API/model/keras/model.h5` | Already present (209 MB, untracked) |

## Environment Constraints

The machine had only Python 3.14 and is arm64. Both are problems:

- `tensorflow==2.12.0` publishes no wheel for Python 3.12+, and none for macOS
  arm64 at all.
- `API/model.py` imports `keras.layers.convolutional` and
  `keras.layers.pooling`, module paths removed after Keras 2.12. So the 2.12
  pin is load-bearing and cannot simply be bumped to a modern TensorFlow.

Resolution: `brew install python@3.11`, then a venv at `API/env` using
`tensorflow-macos==2.12.0` in place of `tensorflow==2.12.0`. Every other pin in
`requirements.txt` resolved cleanly on arm64/cp311, including
`opencv-contrib-python==4.5.5.62` and `numpy==1.23.5`.

`requirements.txt` was intentionally left pinned to `tensorflow==2.12.0` so
non-Mac contributors are unaffected; the arm64 substitution is documented in
RUNNING.md rather than baked into the shared file.

## Bugs Fixed

These were genuine defects, not environment issues. The first would have
blocked a clean install on any platform.

### 1. Flask / Werkzeug version conflict — `API/requirements.txt`

`Flask==2.0.2` was pinned alongside `Werkzeug==3.0.1`. Werkzeug 3 removed
`url_quote`, which Flask 2.0.2 imports at module load, so `import flask` raised
`ImportError` before any application code ran. The API could not start for
anyone doing a fresh `pip install -r requirements.txt`.

Fixed by repinning to `Werkzeug==2.0.3`.

### 2. Missing `request` import — `API/app.py`

The `/upload` route referenced `request.files` but `request` was never
imported, so the route would raise `NameError` on first call. Added to the
`flask` import line. The unused `initialize_app` import was dropped at the same
time.

### 3. Hardcoded absolute paths from a previous machine

Two references to `~/PhpstormProjects/CS5588-Capstone-Project/...`, a directory
that no longer exists:

- `API/db/firebase.py` — the service-account key path.
- `API/app.py` `/images` — the download destination.

`/images` additionally downloaded the blob and then returned the string
`"file downloaded?"` instead of the image; it now returns `send_file(...)`.

## Firebase Credential Handling

`API/db/firebase.py` was rewritten to read the service-account path from the
`FIREBASE_CREDENTIALS` environment variable, falling back to
`GOOGLE_APPLICATION_CREDENTIALS`. When neither is set it prints a warning and
leaves `bucket` as `None` rather than raising at import time.

This matters because `app.py` imports `db.firebase` at module scope — under the
old code, a missing key took down the entire API, including routes that have
nothing to do with storage. Now `/` and `/ping` work unconditionally, and only
`/upload` and `/images` degrade, returning 503.

The key itself is not in the repo and should stay out of it. To obtain one:
Firebase Console -> `openpose-db` project -> gear icon -> Project settings ->
Service accounts -> Generate new private key.

## Verification

All three components confirmed running, not just installed:

- **Model** — architecture built and all weights loaded from `model.h5`
  (52,311,446 parameters). Inference run end-to-end on
  `sample_images/OP55.jpeg`, classifying *reclined back, folding hands, not
  kneeling, neck straight, both feet on the floor*. The annotated canvas was
  written out and inspected: keypoints land correctly on ear, shoulder, elbow,
  wrist, hip, knee, and ankle.
- **API** — `GET /` returns `"Hello World"`, `GET /ping` returns `"pong!"`,
  `POST /upload` returns 503 with no credential configured, as intended.
- **Frontend** — Vite dev server serves `http://localhost:5173/` with a 200.

Note that `posture_image.py` ends in a blocking `cv2.imshow` window; the
verification above called `process()` directly to stay headless.

## Parallel React Frontend

`openpose-react/` was added as a line-for-line port of `openpose-vue/`, built to
learn the Vue/React differences by diffing two implementations of the same app.
The Vue tree is untouched — `git diff -- openpose-vue/src` is empty.

Both apps target the same Firebase project and the same Flask API, so a session
started in one is visible in the other. The React dev server is pinned to 5174
with `strictPort` so both can run at once against Vue's 5173.

Stack: Vite + React 19 + TypeScript, `react-router-dom`, `firebase`, `axios`.
Scoped styles were translated to CSS Modules (`*.module.css`), the closest
equivalent Vite offers to Vue's `<style scoped>`. The five SVG icon components
were converted by script rather than by hand, then checked that every
`class`/`xmlns:xlink`/kebab-case attribute became valid JSX.

`openpose-react/COMPARISON.md` is the actual artifact here: a file-to-file map
plus the eight framework differences that matter, each anchored to real code.

Two structural divergences worth knowing, since they are deliberate and will
otherwise look like porting errors:

- **Routing.** Vue Router's global `beforeEach` runs *before* navigation
  commits. React Router has no global guard, so `components/ProtectedRoute.tsx`
  runs during render — the component mounts, then redirects. It needs an
  explicit `checking` state with no Vue counterpart; without it the UI flashes a
  login redirect before Firebase reports back.
- **Firebase init.** Vue initializes Firebase inside `main.ts` and exports
  `auth`/`db` from the file that mounts the app, so components do
  `import { auth } from '@/main'`. The React version puts it in its own
  `src/firebase.ts` instead of mirroring that coupling.

Two bugs were fixed in the port rather than transcribed faithfully. **Both still
exist in the Vue app** and were left there intentionally, as comparison material:

1. `Registration.vue` calls `router.push('/dashboard')` both inside the
   `updateProfile` callback and again immediately after, so it can navigate
   before the display name saves — the Dashboard then greets "Hello, undefined".
   The React version awaits the update.
2. `App.vue` never unsubscribes its `onAuthStateChanged` listener. React's
   `useEffect` cleanup contract made the omission obvious.

Verified: `tsc --noEmit` clean, production build succeeds, dev server returns
200 on 5174 alongside the Vue app on 5173.

Note that the Firebase web config is now duplicated verbatim in
`openpose-vue/src/main.ts` and `openpose-react/src/firebase.ts`. A project
change means editing both.

## Open Items

**The dashboard result text is still mocked.** This is the main gap between the
demo and a working product. There is no inference endpoint — `posture_image.py`
is a standalone script with its own `__main__`, so nothing the frontend calls
ever touches the model. Wiring it up means adding a route to `app.py` that
loads the model once at startup (a cold load is slow, and per-request loading
would be unusable) and calls `process()` on an uploaded image.

This now applies to both frontends: `submitImage()` waits five seconds and shows
canned text in each. Adding the endpoint means wiring it twice, which makes it a
reasonable exercise for comparing how the two frameworks handle async state.

**Firebase web config is committed** in `openpose-vue/src/main.ts` and
`openpose-react/src/firebase.ts`. A web SDK `apiKey` is public by design and is
not a leaked secret, but it does mean database security rules are the only
access control in front of the data. Worth reviewing if that has not been done.

**`model.h5` is untracked and unignored.** At 209 MB it exceeds GitHub's 100 MB
per-file limit and cannot be committed as-is; it is distributed via the link in
`ModelReadME.md`. Consider adding it to `.gitignore` so it is not staged by
accident — the existing `.h5` entry there is missing its `*` wildcard and so
matches nothing.
