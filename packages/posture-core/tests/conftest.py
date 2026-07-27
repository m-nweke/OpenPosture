"""Hypothesis profiles.

Two, because the inner loop and CI want different things from the same properties. Locally the
suite has to stay fast enough that people run it before every commit; in CI the properties are the
load-bearing claim of the project and there are seconds to spare.

`--hypothesis-profile=ci` is what `scientific-validation.yml` passes. Registering the profiles here
rather than passing raw `--hypothesis-*` flags keeps the two environments described in one place,
in the repository, rather than in a workflow file nobody reads.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings

settings.register_profile(
    "dev",
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

settings.register_profile(
    "ci",
    max_examples=1000,
    deadline=None,
    # Derandomised so a failure in CI is reproducible from the same command locally. A property
    # test that fails once and then cannot be reproduced teaches nobody anything.
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

settings.load_profile("dev")
