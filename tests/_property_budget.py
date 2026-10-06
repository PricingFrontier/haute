"""Shared Hypothesis budget for the generated families (ENG-T11).

Every generated family in the ordinary lane runs under ``pr_budget``: a small,
derandomised example budget with no example database, so a failure reproduces
from the printed example alone, independent of test order and of the local
``.hypothesis`` cache. Setting ``HAUTE_PROPERTY_EXAMPLES`` switches the same
tests to a larger randomised exploration budget (the explicit exploration lane
runs them that way); it is never set in the PR lane.

Negative controls use :func:`hypothesis.find` under the same settings, so the
example that demonstrates a family's discriminating power is as reproducible as
the property itself.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, settings

EXPLORATION_ENV = "HAUTE_PROPERTY_EXAMPLES"


def exploration_examples() -> int | None:
    """The exploration budget requested through the environment, if any."""
    raw = os.environ.get(EXPLORATION_ENV)
    if raw is None or not raw.strip():
        return None
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{EXPLORATION_ENV} must be a positive integer, got {raw!r}")
    return value


def pr_budget(max_examples: int = 60) -> settings:
    """Settings for one generated family in the PR lane.

    ``max_examples`` is the PR budget; the exploration lane overrides it and
    turns randomisation back on so repeated runs cover new ground.
    """
    exploring = exploration_examples()
    return settings(
        max_examples=max_examples if exploring is None else exploring,
        derandomize=exploring is None,
        database=None,
        deadline=None,
        print_blob=True,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.function_scoped_fixture,
        ],
    )
