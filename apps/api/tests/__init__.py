"""Marks the API tests as a package so mypy can tell this `conftest` from the rules engine's.

Without it both `apps/api/tests/conftest.py` and `packages/posture-core/tests/conftest.py` are
module `conftest`, and `mypy packages apps` stops with a duplicate-module error before checking
anything. With it, this one is `tests.conftest` and the clash is gone.
"""
