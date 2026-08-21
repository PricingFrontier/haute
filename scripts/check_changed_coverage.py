"""Fail closed when changed executable code lacks Coverage.py evidence."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("pyproject.toml")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class ChangedCoverageError(Exception):
    """Raised when changed coverage cannot be evaluated safely."""


@dataclass(frozen=True)
class ChangedCoverageConfig:
    coverage_json: Path
    paths: tuple[str, ...]
    min_statement_coverage: float
    min_branch_coverage: float


@dataclass(frozen=True)
class FileCoverage:
    path: str
    executed_lines: frozenset[int]
    missing_lines: frozenset[int]
    executed_branches: frozenset[tuple[int, int]]
    missing_branches: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class CoverageResult:
    statement_targets: int
    branch_targets: int
    missing_lines: tuple[int, ...]
    missing_branches: tuple[tuple[int, int], ...]


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ChangedCoverageError(f"{name} must be an object.")
    return value


def _path(raw: str) -> str:
    value = raw.replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    normalized = posixpath.normpath(value)
    if (
        not value
        or normalized in {"", "."}
        or normalized.startswith("../")
        or normalized.startswith("/")
    ):
        raise ChangedCoverageError(f"Invalid normalized repo-relative path: {raw!r}")
    return normalized


def _percent(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ChangedCoverageError(f"{name} must be numeric.")
    result = float(value)
    if not 0 <= result <= 100:
        raise ChangedCoverageError(f"{name} must be between 0 and 100.")
    return result


def _load_config(config_path: Path) -> ChangedCoverageConfig:
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ChangedCoverageError(f"Missing changed coverage config: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ChangedCoverageError(f"Invalid TOML in {config_path}: {exc}") from exc
    tool = _mapping(data.get("tool"), "tool")
    haute = _mapping(tool.get("haute"), "tool.haute")
    section = _mapping(haute.get("changed_coverage"), "tool.haute.changed_coverage")
    raw_json = section.get("coverage_json")
    if not isinstance(raw_json, str) or not raw_json.strip():
        raise ChangedCoverageError("tool.haute.changed_coverage.coverage_json is required.")
    coverage_json = Path(raw_json)
    if not coverage_json.is_absolute():
        coverage_json = config_path.parent / coverage_json
    raw_paths = section.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ChangedCoverageError("tool.haute.changed_coverage.paths must be a non-empty list.")
    paths: list[str] = []
    for raw_path in raw_paths:
        if not isinstance(raw_path, str):
            raise ChangedCoverageError("tool.haute.changed_coverage.paths entries must be strings.")
        normalized = _path(raw_path)
        if normalized in paths:
            raise ChangedCoverageError(f"Duplicate changed coverage path: {normalized}")
        paths.append(normalized)
    return ChangedCoverageConfig(
        coverage_json=coverage_json,
        paths=tuple(paths),
        min_statement_coverage=_percent(
            section.get("min_statement_coverage"), "min_statement_coverage"
        ),
        min_branch_coverage=_percent(section.get("min_branch_coverage"), "min_branch_coverage"),
    )


def _lines(value: Any, name: str) -> frozenset[int]:
    if not isinstance(value, list):
        raise ChangedCoverageError(f"{name} must be a list.")
    result: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ChangedCoverageError(f"{name} entries must be positive integers.")
        if item in result:
            raise ChangedCoverageError(f"{name} contains duplicate line {item}.")
        result.add(item)
    return frozenset(result)


def _arcs(value: Any, name: str) -> frozenset[tuple[int, int]]:
    if not isinstance(value, list):
        raise ChangedCoverageError(f"{name} must be a list.")
    result: set[tuple[int, int]] = set()
    for arc in value:
        if not isinstance(arc, list) or len(arc) != 2:
            raise ChangedCoverageError(f"{name} entries must be two-item lists.")
        first, second = arc
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (first, second)):
            raise ChangedCoverageError(f"{name} arc endpoints must be integers.")
        item = (first, second)
        if item in result:
            raise ChangedCoverageError(f"{name} contains duplicate arc {first}->{second}.")
        result.add(item)
    return frozenset(result)


def _load_coverage_artifact(coverage_path: Path) -> dict[str, FileCoverage]:
    try:
        payload = json.loads(coverage_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ChangedCoverageError(f"Missing coverage JSON artifact: {coverage_path}") from exc
    except json.JSONDecodeError as exc:
        raise ChangedCoverageError(f"Invalid JSON in {coverage_path}: {exc}") from exc
    artifact = _mapping(payload, "coverage JSON")
    meta = _mapping(artifact.get("meta"), "coverage JSON meta")
    if meta.get("format") != 3:
        raise ChangedCoverageError("coverage JSON artifact must use Coverage.py format 3.")
    if meta.get("branch_coverage") is not True:
        raise ChangedCoverageError("coverage JSON artifact branch coverage must be enabled.")
    files = _mapping(artifact.get("files"), "coverage JSON files")
    result: dict[str, FileCoverage] = {}
    for raw_path, value in files.items():
        if not isinstance(raw_path, str):
            raise ChangedCoverageError("coverage JSON file paths must be strings.")
        path = _path(raw_path)
        if path in result:
            raise ChangedCoverageError(f"Duplicate coverage JSON file after normalization: {path}")
        file_data = _mapping(value, f"coverage JSON file {path}")
        executed_lines = _lines(file_data.get("executed_lines"), f"{path}.executed_lines")
        missing_lines = _lines(file_data.get("missing_lines"), f"{path}.missing_lines")
        executed_branches = _arcs(file_data.get("executed_branches"), f"{path}.executed_branches")
        missing_branches = _arcs(file_data.get("missing_branches"), f"{path}.missing_branches")
        if executed_lines & missing_lines or executed_branches & missing_branches:
            raise ChangedCoverageError(
                f"Coverage JSON file {path} has contradictory duplicate evidence."
            )
        result[path] = FileCoverage(
            path, executed_lines, missing_lines, executed_branches, missing_branches
        )
    return result


def _diff_path(value: str) -> str | None:
    value = value.strip()
    if value == "/dev/null":
        return None
    if value.startswith('"') and value.endswith('"'):
        try:
            value = bytes(value[1:-1], "utf-8").decode("unicode_escape")
        except UnicodeDecodeError as exc:
            raise ChangedCoverageError(f"Invalid quoted diff path: {value!r}") from exc
    if value.startswith("b/"):
        value = value[2:]
    return _path(value)


def parse_unified_zero_diff(diff: str) -> dict[str, set[int]]:
    """Return changed new-file lines from a ``git diff --unified=0`` payload."""
    changed: dict[str, set[int]] = {}
    current: str | None = None
    saw_file = False
    for line in diff.splitlines():
        if line.startswith("diff --git ") or line.startswith("--- "):
            current = None
            if line.startswith("diff --git "):
                saw_file = False
            continue
        if line.startswith("+++ "):
            current = _diff_path(line[4:])
            saw_file = True
            continue
        if line.startswith("@@ "):
            if not saw_file:
                raise ChangedCoverageError("Diff hunk has no new-file association.")
            match = _HUNK.match(line)
            if match is None:
                raise ChangedCoverageError(f"Malformed unified diff hunk: {line}")
            # A deleted file has ``+++ /dev/null`` and may still carry removal
            # hunks. It contributes no new-file lines to coverage.
            if current is None:
                continue
            start = int(match.group(1))
            length = int(match.group(2) or "1")
            if length < 0:
                raise ChangedCoverageError(f"Malformed unified diff hunk: {line}")
            if length:
                changed.setdefault(current, set()).update(range(start, start + length))
    return changed


def _git(repo: Path, args: Sequence[str]) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise ChangedCoverageError(f"Git command failed: git {' '.join(args)}: {detail}")
    return completed.stdout


def collect_changed_lines(
    config: ChangedCoverageConfig, repo: Path, base_ref: str | None
) -> tuple[dict[str, set[int]], set[str]]:
    """Collect tracked changed lines and configured untracked files."""
    common = ["diff", "--unified=0", "--no-ext-diff", "--find-renames"]
    outputs: list[str] = []
    if base_ref is not None:
        outputs.append(_git(repo, [*common, f"{base_ref}...HEAD", "--", *config.paths]))
    outputs.append(_git(repo, [*common, "HEAD", "--", *config.paths]))
    changed: dict[str, set[int]] = {}
    for output in outputs:
        for path, lines in parse_unified_zero_diff(output).items():
            if path in config.paths:
                changed.setdefault(path, set()).update(lines)
    untracked = {
        _path(line)
        for line in _git(
            repo, ["ls-files", "--others", "--exclude-standard", "--", *config.paths]
        ).splitlines()
        if line.strip()
    }
    return changed, untracked & set(config.paths)


def evaluate_changed_coverage(
    config: ChangedCoverageConfig,
    coverage: dict[str, FileCoverage],
    changed: dict[str, set[int]],
    untracked: Iterable[str] = (),
) -> dict[str, CoverageResult]:
    results: dict[str, CoverageResult] = {}
    untracked_paths = set(untracked)
    all_changed = set(changed) | untracked_paths
    for path in sorted(all_changed):
        file = coverage.get(path)
        if file is None:
            raise ChangedCoverageError(
                f"Changed configured file is missing from coverage artifact: {path}"
            )
        arcs = file.executed_branches | file.missing_branches
        executable = file.executed_lines | file.missing_lines
        branch_endpoints = {endpoint for arc in arcs for endpoint in arc if endpoint > 0}
        changed_lines = (
            executable | branch_endpoints if path in untracked_paths else changed.get(path, set())
        )
        lines = executable & changed_lines
        target_arcs = {
            arc
            for arc in arcs
            if any(endpoint > 0 and endpoint in changed_lines for endpoint in arc)
        }
        results[path] = CoverageResult(
            statement_targets=len(lines),
            branch_targets=len(target_arcs),
            missing_lines=tuple(sorted(lines & file.missing_lines)),
            missing_branches=tuple(sorted(target_arcs & file.missing_branches)),
        )
    return results


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--coverage-json", type=Path, default=None)
    parser.add_argument("--base-ref")
    return parser.parse_args(argv)


def _percent_covered(total: int, missing: int) -> float | None:
    return None if total == 0 else 100 * (total - missing) / total


def _print_results(config: ChangedCoverageConfig, results: dict[str, CoverageResult]) -> int:
    violations: list[str] = []
    statement_total = branch_total = 0
    for path, result in results.items():
        statement_total += result.statement_targets
        branch_total += result.branch_targets
        statement = _percent_covered(result.statement_targets, len(result.missing_lines))
        branch = _percent_covered(result.branch_targets, len(result.missing_branches))
        if statement is not None and statement + 1e-9 < config.min_statement_coverage:
            violations.append(
                f"{path}: statement coverage {statement:.2f}% < "
                f"{config.min_statement_coverage:.2f}%"
            )
        if branch is not None and branch + 1e-9 < config.min_branch_coverage:
            violations.append(
                f"{path}: branch coverage {branch:.2f}% < {config.min_branch_coverage:.2f}%"
            )
        if result.missing_lines:
            violations.append(
                f"{path}: missing changed lines: {', '.join(map(str, result.missing_lines))}"
            )
        if result.missing_branches:
            arcs = ", ".join(f"{first}->{second}" for first, second in result.missing_branches)
            violations.append(f"{path}: missing changed branches: {arcs}")
    if violations:
        print("Changed coverage gate failed:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    if statement_total == 0 and branch_total == 0:
        print("Changed coverage gate passed: no changed executable targets.")
    else:
        print(
            "Changed coverage gate passed: "
            f"{statement_total} statement and {branch_total} branch targets."
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        config = _load_config(args.config)
        coverage = _load_coverage_artifact(args.coverage_json or config.coverage_json)
        changed, untracked = collect_changed_lines(config, args.config.parent, args.base_ref)
        return _print_results(
            config, evaluate_changed_coverage(config, coverage, changed, untracked)
        )
    except ChangedCoverageError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
