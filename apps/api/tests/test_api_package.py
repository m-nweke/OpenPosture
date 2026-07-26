"""Scaffold-level checks. The app factory and its gates arrive in OP-50."""

from __future__ import annotations


def test_exposes_a_version() -> None:
    import openposture_api

    assert isinstance(openposture_api.__version__, str)
    assert openposture_api.__version__


def test_sits_at_the_top_of_the_dependency_graph() -> None:
    from importlib.metadata import requires

    declared = requires("openposture-api") or []
    names = {r.split()[0].split(">")[0].split("=")[0].split("[")[0].lower() for r in declared}
    assert {"posture-core", "pose-backends"} <= names
