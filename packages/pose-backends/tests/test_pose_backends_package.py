"""Scaffold-level checks. Real adapter tests arrive with the adapters in Epic B."""

from __future__ import annotations

import re

# See the note in posture-core's test_dependency_isolation: match the PEP 508 name positively
# rather than splitting on a guessed set of specifier characters.
_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def test_exposes_a_version() -> None:
    import pose_backends

    assert isinstance(pose_backends.__version__, str)
    assert pose_backends.__version__


def test_depends_on_posture_core() -> None:
    """The dependency direction: adapters conform to the core's contract, never the reverse."""
    from importlib.metadata import requires

    declared = requires("pose-backends") or []
    base = [r for r in declared if "extra ==" not in r]
    names = {
        re.sub(r"[-_.]+", "-", m.group()).lower()
        for m in (_REQUIREMENT_NAME.match(r.strip()) for r in base)
        if m
    }
    assert "posture-core" in names
