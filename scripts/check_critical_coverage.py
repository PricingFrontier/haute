"""Enforce per-file coverage gates for high-risk backend modules."""

from __future__ import annotations

import argparse
import json
import posixpath
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("pyproject.toml")


class CriticalCoverageError(Exception):
    """Raised when the coverage gate cannot be evaluated safely."""


@dataclass(frozen=True)
class CriticalFileThreshold:
    path: str
    min_statement_coverage: float
    min_branch_coverage: float
    rationale: str


@dataclass(frozen=True)
class CriticalCoverageConfig:
    coverage_json: Path
    files: tuple[CriticalFileThreshold, ...]


@dataclass(frozen=True)
class FileCoverage:
    path: str
    percent_statements_covered: float
    percent_branches_covered: float
    missing_lines: tuple[int, ...]
    missing_branches: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class CoverageViolation:
    threshold: CriticalFileThreshold
    actual: FileCoverage
    reasons: tuple[str, ...]


def _normalize_repo_path(raw_path: str) -> str:
    path = raw_path.replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    normalized = posixpath.normpath(path)
    if not normalized or normalized == "." or normalized.startswith("../"):
        raise CriticalCoverageError(f"Invalid repo-relative path: {raw_path!r}")
    return normalized


def _as_mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CriticalCoverageError(f"{name} must be an object.")
    return value


def _as_percent(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CriticalCoverageError(f"{name} must be numeric.")
    percent = float(value)
    if percent < 0 or percent > 100:
        raise CriticalCoverageError(f"{name} must be between 0 and 100.")
    return percent


def _as_missing_lines(value: Any, *, name: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CriticalCoverageError(f"{name} must be a list.")
    lines: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise CriticalCoverageError(f"{name} entries must be integers.")
        lines.append(item)
    return tuple(lines)


def _as_missing_branches(value: Any, *, name: str) -> tuple[tuple[int, ...], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CriticalCoverageError(f"{name} must be a list.")
    branches: list[tuple[int, ...]] = []
    for branch in value:
        if not isinstance(branch, list):
            raise CriticalCoverageError(f"{name} entries must be lists.")
        branch_parts: list[int] = []
        for item in branch:
            if isinstance(item, bool) or not isinstance(item, int):
                raise CriticalCoverageError(f"{name} entries must contain integers.")
            branch_parts.append(item)
        branches.append(tuple(branch_parts))
    return tuple(branches)


def _load_config(config_path: Path) -> CriticalCoverageConfig:
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CriticalCoverageError(f"Missing coverage gate config: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CriticalCoverageError(f"Invalid TOML in {config_path}: {exc}") from exc

    tool = _as_mapping(payload.get("tool"), name="tool")
    haute = _as_mapping(tool.get("haute"), name="tool.haute")
    coverage_config = _as_mapping(
        haute.get("critical_coverage"),
        name="tool.haute.critical_coverage",
    )

    raw_coverage_json = coverage_config.get("coverage_json")
    if not isinstance(raw_coverage_json, str) or not raw_coverage_json:
        raise CriticalCoverageError("tool.haute.critical_coverage.coverage_json is required.")
    coverage_json = Path(raw_coverage_json)
    if not coverage_json.is_absolute():
        coverage_json = config_path.parent / coverage_json

    raw_files = coverage_config.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise CriticalCoverageError("tool.haute.critical_coverage.files must be a non-empty list.")

    thresholds: list[CriticalFileThreshold] = []
    seen_paths: set[str] = set()
    for index, raw_file in enumerate(raw_files):
        file_config = _as_mapping(
            raw_file,
            name=f"tool.haute.critical_coverage.files[{index}]",
        )
        raw_path = file_config.get("path")
        if not isinstance(raw_path, str):
            raise CriticalCoverageError(f"Critical coverage file {index} must define path.")
        path = _normalize_repo_path(raw_path)
        if path in seen_paths:
            raise CriticalCoverageError(f"Duplicate critical coverage file: {path}")
        seen_paths.add(path)

        rationale = file_config.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise CriticalCoverageError(f"Critical coverage file {path} must define rationale.")

        thresholds.append(
            CriticalFileThreshold(
                path=path,
                min_statement_coverage=_as_percent(
                    file_config.get("min_statement_coverage"),
                    name=f"{path}.min_statement_coverage",
                ),
                min_branch_coverage=_as_percent(
                    file_config.get("min_branch_coverage"),
                    name=f"{path}.min_branch_coverage",
                ),
                rationale=rationale.strip(),
            )
        )

    return CriticalCoverageConfig(coverage_json=coverage_json, files=tuple(thresholds))


def _load_coverage_artifact(coverage_path: Path) -> dict[str, FileCoverage]:
    try:
        payload = json.loads(coverage_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CriticalCoverageError(f"Missing coverage JSON artifact: {coverage_path}") from exc
    except json.JSONDecodeError as exc:
        raise CriticalCoverageError(f"Invalid JSON in {coverage_path}: {exc}") from exc

    artifact = _as_mapping(payload, name="coverage JSON")
    meta = _as_mapping(artifact.get("meta"), name="coverage JSON meta")
    if meta.get("format") != 3:
        raise CriticalCoverageError("coverage JSON artifact must use Coverage.py format 3.")
    if meta.get("branch_coverage") is not True:
        raise CriticalCoverageError("coverage JSON artifact branch coverage must be enabled.")

    raw_files = _as_mapping(artifact.get("files"), name="coverage JSON files")
    if not raw_files:
        raise CriticalCoverageError("coverage JSON artifact does not contain any files.")

    files: dict[str, FileCoverage] = {}
    for raw_path, raw_file in raw_files.items():
        if not isinstance(raw_path, str):
            raise CriticalCoverageError("coverage JSON file paths must be strings.")
        path = _normalize_repo_path(raw_path)
        if path in files:
            raise CriticalCoverageError(f"Duplicate coverage JSON file after normalization: {path}")

        file_payload = _as_mapping(raw_file, name=f"coverage JSON file {path}")
        summary = _as_mapping(file_payload.get("summary"), name=f"coverage JSON summary {path}")
        files[path] = FileCoverage(
            path=path,
            percent_statements_covered=_as_percent(
                summary.get("percent_statements_covered"),
                name=f"{path}.summary.percent_statements_covered",
            ),
            percent_branches_covered=_as_percent(
                summary.get("percent_branches_covered"),
                name=f"{path}.summary.percent_branches_covered",
            ),
            missing_lines=_as_missing_lines(
                file_payload.get("missing_lines"),
                name=f"{path}.missing_lines",
            ),
            missing_branches=_as_missing_branches(
                file_payload.get("missing_branches"),
                name=f"{path}.missing_branches",
            ),
        )
    return files


def _evaluate_thresholds(
    thresholds: Sequence[CriticalFileThreshold],
    coverage_files: dict[str, FileCoverage],
) -> list[CoverageViolation]:
    violations: list[CoverageViolation] = []
    for threshold in thresholds:
        actual = coverage_files.get(threshold.path)
        if actual is None:
            raise CriticalCoverageError(
                f"Critical file is missing from coverage artifact: {threshold.path}"
            )

        reasons: list[str] = []
        if actual.percent_statements_covered + 1e-9 < threshold.min_statement_coverage:
            reasons.append(
                "statement coverage "
                f"{actual.percent_statements_covered:.2f}% < "
                f"{threshold.min_statement_coverage:.2f}%"
            )
        if actual.percent_branches_covered + 1e-9 < threshold.min_branch_coverage:
            reasons.append(
                "branch coverage "
                f"{actual.percent_branches_covered:.2f}% < "
                f"{threshold.min_branch_coverage:.2f}%"
            )
        if reasons:
            violations.append(
                CoverageViolation(
                    threshold=threshold,
                    actual=actual,
                    reasons=tuple(reasons),
                )
            )
    return violations


def _format_missing_lines(lines: Sequence[int], *, limit: int = 12) -> str:
    shown = ", ".join(str(line) for line in lines[:limit])
    if len(lines) > limit:
        shown = f"{shown}, ..."
    return shown


def _format_missing_branches(branches: Sequence[Sequence[int]], *, limit: int = 8) -> str:
    formatted = []
    for branch in branches[:limit]:
        formatted.append("->".join(str(part) for part in branch))
    result = ", ".join(formatted)
    if len(branches) > limit:
        result = f"{result}, ..."
    return result


def _print_success(
    thresholds: Sequence[CriticalFileThreshold],
    coverage_files: dict[str, FileCoverage],
) -> None:
    print(f"Critical coverage gates passed for {len(thresholds)} files.")
    for threshold in thresholds:
        actual = coverage_files[threshold.path]
        print(
            f"OK {threshold.path}: statement coverage "
            f"{actual.percent_statements_covered:.2f}% >= "
            f"{threshold.min_statement_coverage:.2f}%; branch coverage "
            f"{actual.percent_branches_covered:.2f}% >= "
            f"{threshold.min_branch_coverage:.2f}%"
        )


def _print_violations(violations: Sequence[CoverageViolation]) -> None:
    print("Critical coverage gates failed:", file=sys.stderr)
    for violation in violations:
        print(
            f"- {violation.threshold.path}: {'; '.join(violation.reasons)}",
            file=sys.stderr,
        )
        print(f"  rationale: {violation.threshold.rationale}", file=sys.stderr)
        if violation.actual.missing_lines:
            print(
                f"  missing lines: {_format_missing_lines(violation.actual.missing_lines)}",
                file=sys.stderr,
            )
        if violation.actual.missing_branches:
            print(
                "  missing branches: "
                f"{_format_missing_branches(violation.actual.missing_branches)}",
                file=sys.stderr,
            )


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="TOML config containing [tool.haute.critical_coverage].",
    )
    parser.add_argument(
        "--coverage-json",
        type=Path,
        default=None,
        help="Coverage.py JSON artifact to check. Overrides coverage_json from config.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        config = _load_config(args.config)
        coverage_json = (
            args.coverage_json if args.coverage_json is not None else config.coverage_json
        )
        coverage_files = _load_coverage_artifact(coverage_json)
        violations = _evaluate_thresholds(config.files, coverage_files)
    except CriticalCoverageError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if violations:
        _print_violations(violations)
        return 1

    _print_success(config.files, coverage_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
