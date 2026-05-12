"""Dependency floors that protect runtime execution assumptions."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


def test_polars_floor_supports_order_preserving_lazy_joins() -> None:
    """Rating-table streaming joins rely on LazyFrame.join(maintain_order=...)."""
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    polars_requirement = next(
        Requirement(dep) for dep in dependencies if Requirement(dep).name == "polars"
    )
    lower_bounds = [
        Version(spec.version)
        for spec in polars_requirement.specifier
        if spec.operator in {">=", "=="}
    ]

    assert lower_bounds
    assert max(lower_bounds) >= Version("1.39.2")
