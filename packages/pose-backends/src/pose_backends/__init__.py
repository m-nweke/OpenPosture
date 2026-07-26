"""Pose inference adapters.

This package quarantines the one heavy, platform-fragile dependency in the project behind a
Protocol, so that swapping inference engines touches nothing in ``posture_core`` or ``apps/api``.

Backends arrive in Epic B:

* ``MediaPipeBackend``  — the real one (OP-18)
* ``FakePoseBackend``   — deterministic, sub-millisecond, no model (OP-19)

The canonical types (``KeypointName``, ``Landmark``, ``PoseFrame``) deliberately live in
``posture_core``, not here. The rules engine defines the contract and adapters conform to it;
that inversion is what keeps the core installable and testable with no inference stack present.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
