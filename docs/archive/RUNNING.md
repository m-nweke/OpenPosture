# OpenPosture Run Guide

This guide outlines how to run the current project locally.

## Current Project Layout

- Backend/API and model scripts: `API/`
- Frontend web app (Vue): `openpose-vue/`
- Frontend web app (React port): `openpose-react/`
- Model download link: `ModelReadME.md`

The two frontends are equivalent implementations of the same app and talk to
the same Flask API and Firebase project. Running one is enough; see
`openpose-react/COMPARISON.md` for how they differ.

## 1) Download and Place the Model File

The model is not stored in git because of file size.

1. Open `ModelReadME.md`.
2. Download `model.h5` from the link.
3. Place the file at:

`API/model/keras/model.h5`

Without this file, `posture_image.py` and `posture_realtime.py` will fail at model load.

## 2) Backend Setup (Python)

From the repo root:

Python 3.11 is required: TensorFlow 2.12 has no wheels for 3.12+, and
`API/model.py` imports `keras.layers.convolutional`, a path removed after
Keras 2.12.

```bash
cd API
python3.11 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

### Apple Silicon (arm64) Note

There is no `tensorflow==2.12.0` wheel for macOS arm64. On an M-series Mac,
install `tensorflow-macos` instead:

```bash
brew install python@3.11
/opt/homebrew/bin/python3.11 -m venv env
source env/bin/activate
grep -v '^tensorflow==' requirements.txt | pip install -r /dev/stdin
pip install tensorflow-macos==2.12.0
```

### Firebase Admin Credential Note

`API/db/firebase.py` reads the service-account key path from the
`FIREBASE_CREDENTIALS` environment variable (falling back to
`GOOGLE_APPLICATION_CREDENTIALS`). The key is deliberately not committed.

```bash
export FIREBASE_CREDENTIALS=/path/to/openpose-db-firebase-adminsdk.json
```

If the variable is unset, the API still starts and `/` and `/ping` work;
only `/upload` and `/images` are disabled, returning 503.

To obtain the key: Firebase Console -> the `openpose-db` project -> gear icon ->
Project settings -> Service accounts -> Generate new private key.

## 3) Run Backend Components

From `API/` with the virtual environment activated:

### Option A: Run image posture script

```bash
python posture_image.py
```

### Option B: Run realtime webcam posture script

```bash
python posture_realtime.py
```

### Option C: Run Flask API

```bash
python -m flask run --port 5000
```

API sanity endpoints:

- `GET http://127.0.0.1:5000/`
- `GET http://127.0.0.1:5000/ping`

## 4) Frontend Setup

In a second terminal, from repo root. Run either app — or both, since they use
different ports.

### Vue

```bash
cd openpose-vue
npm install
npm run dev
```

Then open `http://localhost:5173`.

### React

```bash
cd openpose-react
npm install
npm run dev
```

Then open `http://localhost:5174`.

The React dev server is pinned to 5174 with `strictPort`, so it will fail
loudly on a port conflict rather than silently moving to another port. Both
apps share a Firebase project, so a session started in one is visible in the
other.

Note: `posture_image.py` opens a blocking OpenCV window to display the
annotated result. Press any key with the window focused to exit.

## 5) What Works End-to-End Right Now

- Firebase authentication flow is implemented in the frontend.
- The homepage currently calls backend `GET /` to confirm API availability.
- Dashboard file upload UI exists.

## 6) Current Limitation

The dashboard result text is currently mocked and not yet fully wired to
backend model inference. This applies to both frontends — there is no inference
endpoint on the API yet, so nothing either dashboard calls reaches the model.

