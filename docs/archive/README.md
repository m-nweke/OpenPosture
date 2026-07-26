# Archive — original CS5588 capstone artifacts

Everything in this directory is **reference material from the original OpenPosture capstone**.
It is preserved, not maintained. Nothing here is imported, executed, linted, type-checked, or
tested by the current codebase, and none of it ships in any container image.

It is kept for three concrete reasons:

1. **Provenance.** This project was inherited, not authored from scratch. The coursework
   deliverables are the evidence of what the original team built.
2. **Evidence.** [`../FINDINGS.md`](../FINDINGS.md) cites specific line numbers in
   [`legacy-openpose/`](./legacy-openpose/). Deleting the code would turn a verifiable audit into
   an unverifiable claim. Keeping the "before" readable next to the "after" is the point.
3. **Comparison.** `opresults.py` is the original evaluation artifact. Epic H reruns its
   methodology against the new engine to produce a genuine old-vs-new comparison
   (`docs/evaluation.md`), rather than merely asserting that the rewrite is better.

## Contents

### `legacy-openpose/` — the inherited inference code

The TensorFlow/Keras reimplementation of the CMU OpenPose two-branch, six-stage CNN, plus the
hand-written posture rules layered on top of it.

| File | What it was |
|---|---|
| `model.py` | The 52.3 M-parameter CMU OpenPose architecture, Keras functional API |
| `posture_image.py` | Standalone script: one image → printed verdicts + a blocking OpenCV window |
| `posture_realtime.py` | The same logic against a webcam; ~250 lines copy-pasted from the above |
| `config`, `config_reader.py` | `ConfigObj` inference parameters and their loader |
| `util.py` | Image-padding helper |

**Do not treat this code as a reference implementation.** It is the subject of the audit, and
[`../FINDINGS.md`](../FINDINGS.md) documents its defects in detail — inverted ear indices, a
geometrically meaningless neck metric, a tautological feet check, an uncaught `UnboundLocalError`,
and a silent `None` → *"Straight back position"* false negative that reports good posture for
every pose the system failed to assess.

These files are **excluded from ruff, mypy, and pytest** at the repo root. They would not pass,
and they are not meant to.

Neither `process()` function is importable in the first place — both reference module-level globals
(`model`, `frame`) that are only ever assigned inside `if __name__ == '__main__'`. Running them
also requires the 209 MB `model.h5` weights and a Python 3.11 / TensorFlow 2.12 environment, both
of which were removed from the working tree in **OP-11**. See `ModelReadME.md` for the original
weights link.

### Coursework deliverables

`Presentations/`, `Demos/`, `Misc/`, the root PDFs, `ResultsReportOP.docx`, and
`OP Model Results.xlsx` — posters, reports, recorded demos, and team documentation from the
original course submission.

### Project documentation

| File | What it is |
|---|---|
| `RUNDOWN.md` | The prior maintainer's status notes and open-items list |
| `RUNNING.md` | Original local setup instructions (Python 3.11 + `tensorflow-macos` 2.12) |
| `ModelReadME.md` | Where to obtain the 209 MB `model.h5` weights |
| `COMPARISON.md` | The Vue → React port analysis, written during the original project |
| `React-vs-Vue.pptx` | Slide version of the same comparison |
| `opresults.py` | Colab export producing the original confusion matrices |

## Where the Vue frontend went

`openpose-vue/` was deleted in **OP-11**. React was kept; maintaining two functionally identical
frontends had no purpose, and the React app is the one the v2 dashboard is built on.

The Vue app is **not lost** — git history was deliberately retained rather than rewritten
(see [`../adr/0006-retain-git-history.md`](../adr/)). It is present in full at commit
[`e1bc9b3`](https://github.com/m-nweke/OpenPosture/tree/e1bc9b3/openpose-vue), the
merge of PR #7, which is reachable from `main`.

To read it without changing your working tree:

```bash
git show e1bc9b3:openpose-vue/src/main.ts
git ls-tree -r --name-only e1bc9b3 openpose-vue
```

To restore it to a scratch directory:

```bash
git archive e1bc9b3 openpose-vue | tar -x -C /tmp
```
