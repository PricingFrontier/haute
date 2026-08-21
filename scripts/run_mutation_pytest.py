"""Run a mutation test command from a fresh, disposable working directory.

Cosmic Ray starts a new pytest process for every mutant.  Keeping those
processes rooted at the repository makes runtime caches accumulate between
mutants, which both distorts timings and lets one mutant observe another's
state.  Test modules still resolve from the repository, while all relative
runtime state is confined to a unique temporary directory.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_TARGETS_FILE_OPTION = "--test-targets-file"


def _absolute_test_target(argument: str, *, repo_root: Path = REPO_ROOT) -> str:
    """Resolve a pytest ``tests/...`` target without changing other arguments."""
    path_argument, separator, selector = argument.partition("::")
    normalized_path = path_argument.replace("\\", "/")
    if normalized_path != "tests" and not normalized_path.startswith("tests/"):
        return argument
    tests_root = (repo_root / "tests").resolve()
    resolved = (repo_root / normalized_path).resolve()
    try:
        resolved.relative_to(tests_root)
    except ValueError as exc:
        raise ValueError(
            f"pytest target escapes the repository tests directory: {argument}"
        ) from exc
    absolute = str(resolved)
    return absolute + (separator + selector if separator else "")


def _test_targets_from_file(raw_path: str, *, repo_root: Path = REPO_ROOT) -> list[str]:
    path = Path(raw_path)
    if not path.is_absolute():
        path = repo_root / path
    path = path.resolve()
    try:
        path.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ValueError(f"test-targets file escapes the repository: {raw_path}") from exc
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read test-targets file {path}: {exc}") from exc
    targets = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not targets:
        raise ValueError(f"test-targets file is empty: {path}")
    if len(set(targets)) != len(targets):
        raise ValueError(f"test-targets file contains duplicate entries: {path}")
    for target in targets:
        normalized = target.replace("\\", "/")
        if normalized != "tests" and not normalized.startswith("tests/"):
            raise ValueError(f"test-targets file contains a non-test target: {target}")
        _absolute_test_target(target, repo_root=repo_root)
    return targets


def _expand_test_target_files(
    arguments: Sequence[str], *, repo_root: Path = REPO_ROOT
) -> list[str]:
    expanded: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument != TEST_TARGETS_FILE_OPTION:
            expanded.append(argument)
            index += 1
            continue
        if index + 1 >= len(arguments):
            raise ValueError(f"{TEST_TARGETS_FILE_OPTION} requires a path")
        expanded.extend(_test_targets_from_file(arguments[index + 1], repo_root=repo_root))
        index += 2
    return expanded


def _pytest_arguments(
    arguments: Sequence[str], *, repo_root: Path = REPO_ROOT, basetemp: Path | None = None
) -> list[str]:
    infrastructure = [
        "-c",
        str(repo_root / "pyproject.toml"),
        "--rootdir",
        str(repo_root),
    ]
    if basetemp is not None:
        infrastructure.extend(("--basetemp", str(basetemp)))
    return [
        *infrastructure,
        *(_absolute_test_target(argument, repo_root=repo_root) for argument in arguments),
    ]


def run(arguments: Sequence[str], *, repo_root: Path = REPO_ROOT) -> int:
    """Run pytest with repository configuration and isolated relative state."""
    expanded_arguments = _expand_test_target_files(arguments, repo_root=repo_root)
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="haute-mutation-") as isolation_dir:
        isolation_root = Path(isolation_dir)
        working_dir = isolation_root / "project"
        working_dir.mkdir()
        try:
            os.chdir(working_dir)
            return int(
                pytest.main(
                    _pytest_arguments(
                        expanded_arguments,
                        repo_root=repo_root,
                        # Match a real project: pytest's temporary files must
                        # be outside the project boundary, while Haute's
                        # relative runtime/cache state stays inside it.
                        basetemp=isolation_root / "pytest",
                    )
                )
            )
        finally:
            os.chdir(original_cwd)


def main(argv: Sequence[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
