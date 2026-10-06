"""Dependency floors that protect runtime execution assumptions."""

from __future__ import annotations

import ast
import re
import tomllib
from importlib.metadata import distributions, packages_distributions
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
    """The dev-group names the unlocked-resolve lane greps out of the lock export, as spelt.

    The lane installs the built wheel plus a hand-kept list of test tooling at
    its locked pins (``grep -E '^(a|b|c)=='`` over ``uv export``); everything
    else resolves fresh. Exactly one such grep is expected. The spellings are
    returned untouched because the grep matches the export literally.
    """
    lists = re.findall(r"grep -E '\^\(([^)]+)\)=='", workflow_text)
    assert len(lists) == 1, f"expected one dev-tooling grep in the unlocked lane, found {lists}"
    return set(lists[0].split("|"))


def _unlocked_lane_extras(workflow_text: str) -> set[str]:
    """The extras the lane installs with the wheel (``haute[a,b] @ file://...``)."""
    installs = re.findall(r"haute\[([^\]]+)\] @ file://", workflow_text)
    assert len(installs) == 1, (
        f"expected one wheel install with extras in the lane, found {installs}"
    )
    return {extra.strip() for extra in installs[0].split(",")}


def _unlocked_lane_pytest_plugins(workflow_text: str) -> set[str]:
    """Plugins the lane's pytest command line needs, by the flags it passes.

    These never appear as imports, so the import closure cannot see them; the
    command line is the only evidence. The step is found by name so a second
    pytest invocation elsewhere in the file cannot be mistaken for it.
    """
    match = re.search(
        r"- name: Core subset against the unlocked install\n(.*?)(?=\n\s*- name:)",
        workflow_text,
        flags=re.DOTALL,
    )
    assert match, "the unlocked lane's core-subset step was not found by name"
    command = match.group(1)
    flags_to_plugins = {
        r"(?:^|\s)-n\s*\d": "pytest-xdist",
        r"(?:^|\s)--timeout\b": "pytest-timeout",
        r"(?:^|\s)--cov\b": "pytest-cov",
    }
    return {plugin for flag, plugin in flags_to_plugins.items() if re.search(flag, command)}


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _runtime_nodes(tree: ast.AST):  # type: ignore[no-untyped-def]
    """Every node except those under an ``if TYPE_CHECKING:`` body.

    Imports guarded by ``try/except ImportError`` are kept: the interpreter
    does attempt them, so counting them over-requires the lane at worst.
    """
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, ast.If) and _is_type_checking_guard(node):
            stack.extend(node.orelse)
            continue
        stack.extend(ast.iter_child_nodes(node))


def _module_files(base: Path, parts: list[str]) -> list[Path]:
    """The files importing ``base/parts`` executes: the module and every package ``__init__``.

    Returns an empty list when nothing resolves, which callers treat as an error.
    """
    files: list[Path] = []
    for depth in range(1, len(parts)):
        package_init = base.joinpath(*parts[:depth], "__init__.py")
        if package_init.is_file():
            files.append(package_init)
    module = base.joinpath(*parts).with_suffix(".py")
    package = base.joinpath(*parts, "__init__.py")
    if module.is_file():
        files.append(module)
    elif package.is_file():
        files.append(package)
    else:
        return []
    return files


def _helper_files(node: ast.Import | ast.ImportFrom, source: Path, tests_root: Path) -> list[Path]:
    """The ``tests`` helper files an import statement in *source* executes.

    Absolute ``tests.x.y`` imports resolve under *tests_root*; relative imports
    resolve from *source*'s own directory. An import of a ``tests`` module that
    resolves to no file fails, rather than being skipped: a silent gap here is
    exactly the kind of hole that let the lane go red unnoticed.
    """
    resolutions: list[tuple[str, list[Path]]] = []
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] == "tests":
                resolutions.append((alias.name, _module_files(tests_root, parts[1:])))
        return [path for _, paths in resolutions for path in paths]

    if node.level:
        base = source.parent
        for _ in range(node.level - 1):
            base = base.parent
        module_parts = node.module.split(".") if node.module else []
    elif node.module and node.module.split(".")[0] == "tests":
        base = tests_root
        module_parts = node.module.split(".")[1:]
    else:
        return []

    if module_parts:
        module_files = _module_files(base, module_parts)
        resolutions.append((".".join(module_parts), module_files))
        if module_files and module_files[-1].name == "__init__.py":
            # ``from tests.pkg import deep``: a name may be a submodule of the
            # package rather than an attribute of it; include it when it is.
            for alias in node.names:
                if alias.name != "*":
                    module_files.extend(_module_files(base, [*module_parts, alias.name]))
    else:
        for alias in node.names:
            assert alias.name != "*", f"{source}: star import of a tests helper is not supported"
            resolutions.append((alias.name, _module_files(base, [alias.name])))
    unresolved = [name for name, paths in resolutions if not paths]
    assert not unresolved, (
        f"{source}:{node.lineno} imports tests helpers that resolve to no file: {unresolved}"
    )
    return [path for _, paths in resolutions for path in paths]


def _top_level_imports(paths: list[Path], tests_root: Path = Path("tests")) -> set[str]:
    """Top-level module names imported by *paths*, the test helpers they pull in, and conftests.

    Follows absolute ``tests.…`` and relative imports into helper modules and
    packages, adds every ``conftest.py`` on a file's path under *tests_root*
    (pytest loads them all), skips ``if TYPE_CHECKING:`` bodies, and counts
    the modules named in a ``pytest_plugins`` assignment as imports.
    """
    pending = list(paths)
    for path in paths:
        for ancestor in [path.parent, *path.parent.parents]:
            conftest = ancestor / "conftest.py"
            if conftest.is_file():
                pending.append(conftest)
            if ancestor == tests_root:
                break
    seen: set[Path] = set()
    names: set[str] = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _runtime_nodes(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".", 1)[0] for alias in node.names)
                pending.extend(_helper_files(node, path, tests_root))
            elif isinstance(node, ast.ImportFrom):
                if node.module and not node.level:
                    names.add(node.module.split(".", 1)[0])
                pending.extend(_helper_files(node, path, tests_root))
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "pytest_plugins"
                for target in node.targets
            ):
                for constant in ast.walk(node.value):
                    if isinstance(constant, ast.Constant) and isinstance(constant.value, str):
                        names.add(constant.value.split(".", 1)[0])
    return names - {"tests", "haute"}


def _canonical(name: str) -> str:
    return str(canonicalize_name(name))


def _dev_only_distributions(lane_extras: set[str]) -> set[str]:
    """Dev-group distributions the wheel plus *lane_extras* does not also depend on."""
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    runtime = {_canonical(Requirement(dep).name) for dep in project["project"]["dependencies"]}
    extras = project["project"].get("optional-dependencies", {})
    for name in lane_extras:
        assert name in extras, f"the lane installs extra {name!r}, which pyproject does not declare"
        runtime.update(_canonical(Requirement(dep).name) for dep in extras[name])
    dev = {_canonical(Requirement(dep).name) for dep in project["dependency-groups"]["dev"]}
    return dev - runtime - {"haute"}


def _subset_files() -> list[Path]:
    return [
        Path(line.strip())
        for line in Path("scripts/core_test_files.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_unlocked_lane_installs_every_dev_package_the_core_subset_needs() -> None:
    """The unlocked-resolve lane's hand-kept tooling list covers what the subset needs.

    The lane (``.github/workflows/dependencies.yml``) installs only the dev
    packages its grep names. A dev package ``tests/conftest.py`` imports but
    the grep omits stops pytest loading conftest before any test runs; one a
    core-subset file imports fails that file's collection; a plugin the lane's
    own command line relies on fails argument parsing. ``hypothesis`` was the
    first case from 7 September 2026, when conftest began registering a
    profile at import, and the lane was red for four weekly runs with a cause
    that had nothing to do with the index it watches.

    Import names map to distributions through the installed metadata rather
    than a second hand-kept list. A dev package reachable only transitively
    (through a package the grep does name) is outside this check: it asserts
    the declared dev-group names the subset needs, nothing finer.
    """
    workflow = Path(".github/workflows/dependencies.yml").read_text(encoding="utf-8")
    lane = _unlocked_lane_tooling(workflow)
    misspelt = sorted(name for name in lane if name != _canonical(name))
    assert not misspelt, (
        f"uv export writes canonical names and the grep matches them literally; respell {misspelt}"
    )

    imported = _top_level_imports([Path("tests/conftest.py"), *_subset_files()])

    dev_only = _dev_only_distributions(_unlocked_lane_extras(workflow))
    installed = {_canonical(dist.metadata["Name"]) for dist in distributions()}
    assert dev_only <= installed, (
        f"dev-group packages missing from this environment: {sorted(dev_only - installed)}"
    )

    by_import = packages_distributions()
    needed = {
        _canonical(dist)
        for name in imported
        for dist in by_import.get(name, ())
        if _canonical(dist) in dev_only
    }
    needed |= _unlocked_lane_pytest_plugins(workflow) & dev_only
    assert needed <= lane, (
        "tests/conftest.py, the core subset or the lane's own pytest flags need dev-group "
        f"packages the unlocked-resolve lane never installs: {sorted(needed - lane)}; add them "
        "to the grep in .github/workflows/dependencies.yml"
    )


def test_unlocked_lane_workflow_parsers_read_the_real_shapes() -> None:
    snippet = (
        "uv export | grep -E '^(pytest|httpx|hypothesis)==' > deps.txt\n"
        '"haute[databricks] @ file://${PWD}/${WHEEL}"\n'
        "      - name: Core subset against the unlocked install\n"
        "        run: >\n"
        "          python -m pytest tests/a.py -q -n 4 --timeout=60\n"
        "      - name: Next step\n"
    )
    assert _unlocked_lane_tooling(snippet) == {"pytest", "httpx", "hypothesis"}
    assert _unlocked_lane_extras(snippet) == {"databricks"}
    assert _unlocked_lane_pytest_plugins(snippet) == {"pytest-xdist", "pytest-timeout"}
    with pytest.raises(AssertionError, match="expected one dev-tooling grep"):
        _unlocked_lane_tooling("no grep here")


def test_import_closure_follows_helpers_and_skips_typing_only_imports(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    (tests_root / "pkg").mkdir(parents=True)
    (tests_root / "conftest.py").write_text(
        "import hypothesis\npytest_plugins = ['pytest_randomly']\n", encoding="utf-8"
    )
    (tests_root / "_flat.py").write_text("import httpx\n", encoding="utf-8")
    (tests_root / "pkg" / "__init__.py").write_text("import attrs\n", encoding="utf-8")
    (tests_root / "pkg" / "deep.py").write_text("from . import sibling\n", encoding="utf-8")
    (tests_root / "pkg" / "sibling.py").write_text("import yaml\n", encoding="utf-8")
    (tests_root / "pkg" / "conftest.py").write_text("import nested_only\n", encoding="utf-8")
    (tests_root / "pkg" / "test_x.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "import tests._flat\n"
        "from tests.pkg import deep\n"
        "if TYPE_CHECKING:\n"
        "    import typing_only\n"
        "else:\n"
        "    import runtime_branch\n",
        encoding="utf-8",
    )

    names = _top_level_imports([tests_root / "pkg" / "test_x.py"], tests_root=tests_root)

    assert {"hypothesis", "pytest_randomly", "httpx", "attrs", "yaml", "nested_only"} <= names
    assert "runtime_branch" in names
    assert "typing_only" not in names
    assert "tests" not in names


def test_import_closure_fails_on_a_helper_that_resolves_to_no_file(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    source = tests_root / "test_y.py"
    source.write_text("from tests import _missing\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="resolve to no file"):
        _top_level_imports([source], tests_root=tests_root)
