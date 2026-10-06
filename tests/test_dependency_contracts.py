"""Dependency floors that protect runtime execution assumptions."""

from __future__ import annotations

import ast
import re
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
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


def test_polars_floor_supports_ordered_and_sliced_streaming_joins() -> None:
    """Rating-table streaming joins rely on LazyFrame.join(maintain_order=...),
    and previews limited at the previewed node rely on a sliced left join
    streaming its probe side instead of buffering it (fixed by 1.43)."""
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
    assert max(lower_bounds) >= Version("1.44.2")


def test_price_contour_guard_specifier_is_the_declared_dependency() -> None:
    """The runtime guard enforces exactly the range the package metadata declares.

    The floor is 0.6.0: haute cancels a frontier point's apply or evaluation
    through the CancelToken that release introduced (OPT-PC02), on top of the
    0.5.0 per-point factor tables and canonical ratebook evaluation.
    """
    from haute._price_contour import REQUIRED_SPECIFIER

    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    declared = next(dep for dep in dependencies if Requirement(dep).name == "price-contour")

    assert declared == f"price-contour{REQUIRED_SPECIFIER}"
    assert SpecifierSet(REQUIRED_SPECIFIER).contains("0.6.0")
    assert not SpecifierSet(REQUIRED_SPECIFIER).contains("0.5.9")


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


def _unlocked_lane_tooling(workflow_text: str) -> set[str]:
    """The dev-group names the unlocked-resolve lane greps out of the lock export.

    The lane installs the built wheel plus a hand-kept list of test tooling at
    its locked pins (``grep -E '^(a|b|c)=='`` over ``uv export``); everything
    else resolves fresh. Exactly one such grep is expected.
    """
    lists = re.findall(r"grep -E '\^\(([^)]+)\)=='", workflow_text)
    assert len(lists) == 1, f"expected one dev-tooling grep in the unlocked lane, found {lists}"
    return set(lists[0].split("|"))


def _top_level_imports(paths: list[Path]) -> set[str]:
    """Top-level module names imported anywhere in *paths* and the test helpers they pull in.

    ``from tests import _helper`` and ``from tests._helper import x`` both add
    ``tests/_helper.py`` to the closure, so a helper that imports a dev package
    counts against the file that imports the helper.
    """
    pending = list(paths)
    seen: set[Path] = set()
    names: set[str] = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                parts = node.module.split(".")
                names.add(parts[0])
                if parts[0] == "tests":
                    helper_names = parts[1:2] or [alias.name for alias in node.names]
                    for helper in helper_names:
                        helper_path = Path("tests") / f"{helper}.py"
                        if helper_path.is_file():
                            pending.append(helper_path)
    return names


def _canonical(name: str) -> str:
    return str(canonicalize_name(name))


def _dev_only_distributions() -> set[str]:
    """Dev-group distributions that the published wheel does not also depend on."""
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    runtime = {_canonical(Requirement(dep).name) for dep in project["project"]["dependencies"]}
    for extra in project["project"].get("optional-dependencies", {}).values():
        runtime.update(_canonical(Requirement(dep).name) for dep in extra)
    dev = {_canonical(Requirement(dep).name) for dep in project["dependency-groups"]["dev"]}
    return dev - runtime - {"haute"}


def test_unlocked_lane_installs_every_dev_package_the_core_subset_imports() -> None:
    """The unlocked-resolve lane's hand-kept tooling list covers what the subset imports.

    The lane (``.github/workflows/dependencies.yml``) installs only the dev
    packages its grep names, so a dev package that ``tests/conftest.py`` or a
    core-subset file imports but the grep omits makes pytest fail to load
    conftest before any test runs. ``hypothesis`` did exactly that from
    7 September 2026, when conftest began registering a profile at import, and
    the lane was red for four weekly runs with a cause that had nothing to do
    with the index it watches. Import names map to distributions through the
    installed metadata rather than a second hand-kept list.
    """
    workflow = Path(".github/workflows/dependencies.yml").read_text(encoding="utf-8")
    lane = {_canonical(name) for name in _unlocked_lane_tooling(workflow)}

    subset = [
        Path(line.strip())
        for line in Path("scripts/core_test_files.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    imported = _top_level_imports([Path("tests/conftest.py"), *subset])

    dev_only = _dev_only_distributions()
    by_import = packages_distributions()
    installed = {_canonical(dist) for dists in by_import.values() for dist in dists}
    assert dev_only <= installed, (
        f"dev-group packages missing from this environment: {sorted(dev_only - installed)}"
    )

    needed = {
        _canonical(dist)
        for name in imported
        for dist in by_import.get(name, ())
        if _canonical(dist) in dev_only
    }
    assert needed <= lane, (
        "tests/conftest.py or the core subset imports dev-group packages the "
        f"unlocked-resolve lane never installs: {sorted(needed - lane)}; add them to the "
        "grep in .github/workflows/dependencies.yml"
    )


def test_unlocked_lane_tooling_parser_reads_the_grep_list() -> None:
    snippet = "uv export | grep -E '^(pytest|httpx|hypothesis)==' > deps.txt"
    assert _unlocked_lane_tooling(snippet) == {"pytest", "httpx", "hypothesis"}
    with pytest.raises(AssertionError, match="expected one dev-tooling grep"):
        _unlocked_lane_tooling("no grep here")
