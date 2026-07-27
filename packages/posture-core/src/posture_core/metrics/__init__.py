"""The measurements. One module per metric, one function per metric, no shared mutable state.

Every metric has the same signature — ``(resolver, thresholds) -> Metric`` — which is what lets
``report.py`` (OP-32) run all of them from a list without knowing anything about any of them.
Adding a metric is adding a module and one entry to that list.

Each returns a :class:`~posture_core.status.Metric` whether or not it succeeded. None of them
raise for missing or unusable data, and none of them catch anything broader than
:class:`~posture_core.geometry.DegenerateVectorError`. A genuine programming error crashes, loudly,
rather than being reported to the user as "we could not measure this".
"""

from __future__ import annotations

from posture_core.metrics.head import craniovertebral_angle_deg
from posture_core.metrics.trunk import trunk_inclination_deg

__all__ = ["craniovertebral_angle_deg", "trunk_inclination_deg"]
