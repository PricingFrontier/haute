"""Dependency floors that protect runtime execution assumptions."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


def _project_requirement(name: str) -> Requirement:
    """The ``[project] dependencies`` entry for *name*; fails if it is absent."""
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    for dep in project["project"]["dependencies"]:
        requirement = Requirement(dep)
        if requirement.name == name:
            return requirement
    raise AssertionError(f"{name} is not a declared [project] dependency in pyproject.toml")


def _floor(requirement: Requirement) -> Version:
    lower_bounds = [
        Version(spec.version) for spec in requirement.specifier if spec.operator in {">=", "=="}
    ]
    assert lower_bounds, f"{requirement.name} has no floor"
    return max(lower_bounds)


def _cap(requirement: Requirement) -> Version:
    upper_bounds = [
        Version(spec.version)
        for spec in requirement.specifier
        if spec.operator in {"<", "<=", "=="}
    ]
    assert upper_bounds, f"{requirement.name} has no cap"
    return min(upper_bounds)


def test_pyarrow_is_declared_and_capped_below_the_segfaulting_release() -> None:
    """pyarrow 25.0.0 segfaults in pq.read_metadata on Linux x86-64 (issue #192).

    haute imports pyarrow directly on the main execution path (parquet
    metadata reads, the _json_shred writer, the model scorer, _database_io),
    so it must be a declared dependency with a cap below 25 and a floor no
    lower than a version CI has actually run (23.0.1, the lock).
    """
    requirement = _project_requirement("pyarrow")

    assert _cap(requirement) <= Version("25"), "pyarrow cap must exclude 25.0.0 (issue #192)"
    assert not requirement.specifier.contains("25.0.0"), (
        "pyarrow specifier admits 25.0.0, the release that segfaults (issue #192)"
    )
    assert _floor(requirement) >= Version("23.0.1"), "pyarrow floor below any CI-exercised version"


def test_pandas_floor_and_cap_cover_the_pyfunc_conversion_boundary() -> None:
    """Every pyfunc score and categorical CatBoost pool passes through
    to_pandas() and select_dtypes(include=["datetimetz"]) into MLflow's schema
    enforcement; pandas 3 changes the default string dtype and copy semantics,
    so the direct constraint is a floor CI has run plus a cap below 3."""
    requirement = _project_requirement("pandas")

    assert _floor(requirement) >= Version("2.3"), "pandas floor below the CI-exercised version"
    assert _cap(requirement) <= Version("3"), "pandas cap must exclude pandas 3"


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


def test_price_contour_floor_supports_ratebook_factor_contexts() -> None:
    """Ratebook frontier materialisation needs the 0.4.1 factor-context API."""
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    price_contour_requirement = next(
        Requirement(dep) for dep in dependencies if Requirement(dep).name == "price-contour"
    )
    lower_bounds = [
        Version(spec.version)
        for spec in price_contour_requirement.specifier
        if spec.operator in {">=", "=="}
    ]

    assert lower_bounds
    assert max(lower_bounds) >= Version("0.4.1")


def _setup_uv_pins(workflow_text: str) -> list[str | None]:
    """The ``version:`` pinned by each ``astral-sh/setup-uv`` step, ``None`` if unpinned.

    A step's ``with:`` block is the run of non-blank lines indented deeper than
    the step's own ``- uses:`` line; the next step, at the same indent, ends it.
    """
    lines = workflow_text.splitlines()
    pins: list[str | None] = []
    for index, line in enumerate(lines):
        if "uses: astral-sh/setup-uv@" not in line:
            continue
        step_indent = len(line) - len(line.lstrip())
        pin: str | None = None
        for following in lines[index + 1 :]:
            stripped = following.strip()
            if not stripped:
                continue
            if len(following) - len(following.lstrip()) <= step_indent:
                break
            match = re.fullmatch(r'version:\s*"([^"]+)"', stripped)
            if match:
                pin = match.group(1)
        pins.append(pin)
    return pins


def test_every_workflow_pins_the_same_uv_version() -> None:
    """``uv`` is the tool every other pin runs through, so it is pinned exactly.

    Tooling is exact-pinned in this repository (the dev group, every npm
    dependency), and ``astral-sh/setup-uv`` installs the latest release unless
    told otherwise. Every ``setup-uv`` step must carry a ``version:`` and they
    must all agree, so a bump is one deliberate change across the workflows
    rather than a drift between them. The value itself is not asserted here;
    bumping it is the workflows' business.
    """
    pins_by_workflow = {
        workflow.name: _setup_uv_pins(workflow.read_text(encoding="utf-8"))
        for workflow in sorted(Path(".github/workflows").glob("*.yml"))
    }

    unpinned = sorted(name for name, pins in pins_by_workflow.items() if None in pins)
    assert not unpinned, f"setup-uv steps without a version: pin in {unpinned}"
    versions = {pin for pins in pins_by_workflow.values() for pin in pins}
    assert versions, "no astral-sh/setup-uv steps found under .github/workflows"
    assert len(versions) == 1, f"setup-uv steps pin different uv versions: {sorted(versions)}"
