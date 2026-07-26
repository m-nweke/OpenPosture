"""The architectural constraint of the project, enforced as a test.

`posture-core` must stay pure: numpy and the standard library, nothing else. That is what lets
its suite run in seconds in CI with no model download, no Docker and no database, and what makes
the inference backend swappable without touching a single rule.

Stated in a README, this decays the first time someone adds a convenient import. Stated here, it
fails the build. That is the whole reason this file exists.

Checked in a *subprocess* rather than by inspecting ``sys.modules`` in-process, because by the
time pytest has collected the suite a great many modules are already imported for unrelated
reasons. A fresh interpreter is the only honest way to ask what importing this package costs.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap

import pytest

# A PEP 508 requirement opens with its name, which PEP 508 restricts to these characters. Anything
# after it is a version specifier, an extras list or an environment marker. Matching the name
# positively is the only reliable way to read it: splitting on a hand-listed set of separators
# silently misses the ones nobody thought of ("fastapi<1", "torch!=2.0", "numpy~=1.26").
_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _distribution_names(requirements: list[str]) -> set[str]:
    """Requirement strings -> the PEP 503 normalised names they declare."""
    names = set()
    for requirement in requirements:
        match = _REQUIREMENT_NAME.match(requirement.strip())
        if match:
            names.add(re.sub(r"[-_.]+", "-", match.group()).lower())
    return names


# Importing posture_core must not drag in any of these, directly or transitively.
FORBIDDEN = [
    "mediapipe",
    "cv2",
    "fastapi",
    "starlette",
    "pydantic",
    "sqlalchemy",
    "anthropic",
    "onnxruntime",
    "torch",
    "tensorflow",
]


def _import_in_fresh_interpreter(statement: str) -> set[str]:
    """Run `statement` in a clean interpreter and return the resulting top-level module names."""
    script = textwrap.dedent(f"""
        import sys
        {statement}
        print(",".join(sorted({{m.split(".")[0] for m in sys.modules}})))
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    return set(result.stdout.strip().split(","))


def test_importing_posture_core_pulls_in_no_heavy_dependency() -> None:
    loaded = _import_in_fresh_interpreter("import posture_core")
    leaked = sorted(set(FORBIDDEN) & loaded)
    assert not leaked, (
        f"posture_core must stay dependency-light, but importing it loaded: {leaked}. "
        "The rules engine may depend on numpy and the standard library only — see the "
        "dependency direction documented in the root pyproject.toml."
    )


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_forbidden_module_is_not_a_declared_dependency(forbidden: str) -> None:
    """Guards the metadata as well as the import, so a stray declaration is caught too."""
    from importlib.metadata import requires

    names = _distribution_names(requires("posture-core") or [])
    assert forbidden not in names, (
        f"posture-core declares a dependency on {forbidden!r}. "
        "Heavy dependencies belong in pose-backends or apps/api."
    )


def test_posture_core_exposes_a_version() -> None:
    import posture_core

    assert isinstance(posture_core.__version__, str)
    assert posture_core.__version__
