"""Scaffold-level checks. The app factory and its gates arrive in OP-50."""

from __future__ import annotations

import re

# See the note in posture-core's test_dependency_isolation: match the PEP 508 name positively
# rather than splitting on a guessed set of specifier characters.
_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def test_exposes_a_version() -> None:
    import openposture_api

    assert isinstance(openposture_api.__version__, str)
    assert openposture_api.__version__


def test_sits_at_the_top_of_the_dependency_graph() -> None:
    from importlib.metadata import requires

    declared = requires("openposture-api") or []
    names = {
        re.sub(r"[-_.]+", "-", m.group()).lower()
        for m in (_REQUIREMENT_NAME.match(r.strip()) for r in declared)
        if m
    }
    assert {"posture-core", "pose-backends"} <= names
