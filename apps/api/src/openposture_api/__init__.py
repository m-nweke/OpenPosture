"""OpenPosture FastAPI service.

The application layer: HTTP, persistence, auth, storage and LLM coaching. It composes
``posture_core`` (pure rules) with ``pose_backends`` (inference) and owns everything impure.

Built out in Epic D onward. Nothing in the repository depends on this package.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
