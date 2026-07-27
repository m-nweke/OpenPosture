"""Pure posture rules engine.

Layering, established in Epic C:

    PoseFrame -> metrics.py -> Metric -> rules.py -> Finding -> report.py -> PostureReport

Rules for this package, which are the point of it existing separately:

* no I/O — never reads a file, opens a socket, or touches an environment variable
* no printing — results are returned, never logged or printed
* no globals — every tunable is injected (see thresholds.py, OP-31)
* no frameworks — numpy is the only permitted runtime dependency
* no bare ``except Exception`` — a metric that cannot be computed reports a status, not a guess

The last two are what the inherited engine got wrong. It swallowed exceptions and returned
``None``, which the caller then rendered as "Straight back position" — telling users their
posture was fine whenever the system had failed to assess them at all.
"""

from __future__ import annotations

from posture_core.keypoints import KeypointName, Landmark, PoseFrame

__all__ = ["KeypointName", "Landmark", "PoseFrame", "__version__"]

__version__ = "0.1.0"
