"""Every dependency a deployment can install has a wheel for the target (DEP-R09).

A deployment locks its runtime for Linux x86_64 and refuses a dependency without a wheel
for it (``haute.deploy._lock``), so a dependency haute takes on, or a release it moves to,
that has none would fail deploys and app builds. These resolve every profile against the
real package index with the real uv, for each Python haute supports, before that happens:
the union of everything a scoring image may install (the runtime, every engine and
MLflow, pinned to the versions installed here), and haute's own closure with the
``databricks`` extra, what the Databricks model and the hosted app install.

Marked ``index``: they need the network, so the default run leaves them out and the
dependencies workflow runs them when the dependencies change, and weekly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._model_flavors import model_families
from haute.deploy._container import (
    _PICKLE_RUNTIME_DEPENDENCIES,
    _SCORING_RUNTIME_DEPENDENCIES,
    _pinned_dockerfile_dependency,
    _pinned_price_contour_dependency,
    haute_constraints,
)
from haute.deploy._lock import lock_requirements

pytestmark = pytest.mark.index

_REPO = Path(__file__).resolve().parents[1]
_PYTHONS = ("3.11", "3.12", "3.13")


def _every_scoring_root() -> list[str]:
    """Each root a scoring image may lock, at the version installed here."""
    engines = {distribution for family in model_families() for distribution in family.distributions}
    roots = [
        _pinned_price_contour_dependency()
        if distribution == "price-contour"
        else _pinned_dockerfile_dependency(distribution, install_name)
        for distribution, install_name in _SCORING_RUNTIME_DEPENDENCIES
    ]
    extras = sorted(engines | set(_PICKLE_RUNTIME_DEPENDENCIES) | {"mlflow"})
    return [*roots, *(_pinned_dockerfile_dependency(name, name) for name in extras)]


@pytest.mark.parametrize("python_version", _PYTHONS)
def test_every_scoring_image_runtime_locks_from_wheels(python_version: str) -> None:
    lock = lock_requirements(
        _every_scoring_root(), python_version=python_version, constraints=haute_constraints()
    )

    # uvicorn[standard] brings these, which haute requires and the image does not list,
    # and the Linux-only event loop a Windows host would never have in its freeze.
    assert {"click", "python-dotenv", "watchfiles", "websockets", "uvloop"} <= set(lock.pins)


@pytest.mark.parametrize("python_version", _PYTHONS)
def test_haute_s_own_closure_locks_from_wheels(python_version: str) -> None:
    lock = lock_requirements(
        _REPO / "pyproject.toml", python_version=python_version, extras=["databricks"]
    )

    assert {"mlflow", "polars", "databricks-sdk"} <= set(lock.pins)
