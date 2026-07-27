"""OpenPosture FastAPI service.

The application layer: HTTP, persistence, auth, storage and LLM coaching. It composes
``posture_core`` (pure rules) with ``pose_backends`` (inference) and owns everything impure.

Nothing in the repository depends on this package — the dependency direction is one-way and this
is the top of it.

Deliberately holds only the version. The app is built by
:func:`openposture_api.main.create_app`, and re-exporting that here would make ``import
openposture_api`` construct an application as a side effect — the import-time coupling
(FINDINGS §3.3) that the factory exists to remove.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
