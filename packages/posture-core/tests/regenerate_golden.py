"""Regenerate the golden snapshots.

    uv run python packages/posture-core/tests/regenerate_golden.py

Run this deliberately, after a change that is *meant* to alter the reports, and read the resulting
diff. That diff is the review: a retuned threshold should visibly move confidences and verdicts
across the corpus, and if it moves something you did not expect, that is the finding.

Deliberately not wired to an environment variable checked by the test run. `UPDATE_GOLDEN=1 pytest`
is one absent-minded shell history entry away from a green suite that asserts nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_golden import CASES, GOLDEN, report_for, serialise


def _display(path: Path) -> str:
    """A short path when the caller is somewhere above it, the full one otherwise.

    `relative_to` raises rather than falling back, so running this from anywhere that is not an
    ancestor of the repository — `/tmp`, a sibling checkout, an editor's scratch directory — killed
    the script partway through with a `ValueError` about subpaths. It had already rewritten some of
    the snapshots by then, which is the worst moment to stop. Cosmetics should not be able to fail
    the operation they are describing.
    """
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def main() -> int:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name in sorted(CASES):
        path = GOLDEN / f"{name}.json"
        content = serialise(report_for(name))
        changed = not path.exists() or path.read_text(encoding="utf-8") != content
        path.write_text(content, encoding="utf-8")
        print(f"{'updated' if changed else 'unchanged'}  {_display(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
