"""Scaffold-level checks. Real adapter tests arrive with the adapters in Epic B."""

from __future__ import annotations


def test_exposes_a_version() -> None:
    import pose_backends

    assert isinstance(pose_backends.__version__, str)
    assert pose_backends.__version__


def test_depends_on_posture_core() -> None:
    """The dependency direction: adapters conform to the core's contract, never the reverse."""
    from importlib.metadata import requires

    declared = requires("pose-backends") or []
    base = [r for r in declared if "extra ==" not in r]
    names = {r.split()[0].split(">")[0].split("=")[0].split("[")[0].lower() for r in base}
    assert "posture-core" in names
