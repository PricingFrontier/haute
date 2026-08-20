from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from scripts import check_changed_coverage as checker


def _config(tmp_path: Path, paths: str = '["src/haute/a.py"]') -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        textwrap.dedent(
            f"""
            [tool.haute.changed_coverage]
            coverage_json = "coverage.json"
            paths = {paths}
            min_statement_coverage = 100
            min_branch_coverage = 100
            """
        ).strip(),
        encoding="utf-8",
    )
    return path


def _coverage(tmp_path: Path, files: dict[str, object] | None = None) -> Path:
    path = tmp_path / "coverage.json"
    path.write_text(
        json.dumps(
            {
                "meta": {"format": 3, "branch_coverage": True},
                "files": files
                or {
                    r"src\haute\a.py": {
                        "executed_lines": [1, 2],
                        "missing_lines": [3],
                        "executed_branches": [[2, 3]],
                        "missing_branches": [[3, 4]],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_config_rejects_empty_and_duplicate_normalized_paths(tmp_path: Path) -> None:
    config = _config(tmp_path, '["src\\\\haute\\\\a.py", "src/haute/a.py"]')
    with pytest.raises(checker.ChangedCoverageError, match="Duplicate"):
        checker._load_config(config)


def test_artifact_requires_format_branch_and_unique_evidence(tmp_path: Path) -> None:
    path = _coverage(
        tmp_path,
        {
            "src/haute/a.py": {
                "executed_lines": [1, 1],
                "missing_lines": [],
                "executed_branches": [],
                "missing_branches": [],
            }
        },
    )
    with pytest.raises(checker.ChangedCoverageError, match="duplicate line"):
        checker._load_coverage_artifact(path)


def test_artifact_normalizes_windows_paths(tmp_path: Path) -> None:
    loaded = checker._load_coverage_artifact(_coverage(tmp_path))
    assert list(loaded) == ["src/haute/a.py"]


@pytest.mark.parametrize(
    ("diff", "expected"),
    [
        ("+++ b/src/haute/a.py\n@@ -1,0 +5,2 @@\n+x\n+y\n", {"src/haute/a.py": {5, 6}}),
        ("+++ b/src/haute/a.py\n@@ -3 +3 @@\n-old\n+new\n", {"src/haute/a.py": {3}}),
        ("+++ b/src/haute/a.py\n@@ -3,1 +3,0 @@\n-old\n", {}),
        ("+++ /dev/null\n@@ -3,1 +0,0 @@\n-old\n", {}),
        ("+++ b/src/haute/new.py\n@@ -1 +1 @@\n-old\n+new\n", {"src/haute/new.py": {1}}),
        (
            "+++ b/src/haute/a.py\n@@ -1 +1 @@\n-a\n+b\n@@ -7 +8,2 @@\n-c\n+d\n+e\n",
            {"src/haute/a.py": {1, 8, 9}},
        ),
    ],
)
def test_parse_unified_zero_diff(diff: str, expected: dict[str, set[int]]) -> None:
    assert checker.parse_unified_zero_diff(diff) == expected


def test_diff_parser_handles_quoted_rename_and_rejects_bad_association() -> None:
    assert checker.parse_unified_zero_diff('+++ "b/src/haute/a b.py"\n@@ -1 +1 @@\n-x\n+y\n') == {
        "src/haute/a b.py": {1}
    }
    with pytest.raises(checker.ChangedCoverageError, match="association"):
        checker.parse_unified_zero_diff("@@ -1 +1 @@\n+x\n")


def test_evaluation_reports_statement_and_branch_misses() -> None:
    config = checker.ChangedCoverageConfig(Path("coverage.json"), ("src/haute/a.py",), 100, 100)
    coverage = {
        "src/haute/a.py": checker.FileCoverage(
            "src/haute/a.py", frozenset({1}), frozenset({2}), frozenset(), frozenset({(2, 3)})
        )
    }
    result = checker.evaluate_changed_coverage(config, coverage, {"src/haute/a.py": {2}})
    assert result["src/haute/a.py"].missing_lines == (2,)
    assert result["src/haute/a.py"].missing_branches == ((2, 3),)


def test_branch_is_targeted_when_its_positive_destination_changed() -> None:
    config = checker.ChangedCoverageConfig(Path("coverage.json"), ("src/haute/a.py",), 100, 100)
    coverage = {
        "src/haute/a.py": checker.FileCoverage(
            "src/haute/a.py", frozenset({4}), frozenset(), frozenset(), frozenset({(-1, 4)})
        )
    }
    assert checker.evaluate_changed_coverage(config, coverage, {"src/haute/a.py": {4}})[
        "src/haute/a.py"
    ].missing_branches == ((-1, 4),)


def test_branch_endpoint_need_not_also_be_a_statement_target() -> None:
    config = checker.ChangedCoverageConfig(Path("coverage.json"), ("src/haute/a.py",), 100, 100)
    coverage = {
        "src/haute/a.py": checker.FileCoverage(
            "src/haute/a.py", frozenset({1}), frozenset(), frozenset(), frozenset({(1, 4)})
        )
    }
    result = checker.evaluate_changed_coverage(config, coverage, {"src/haute/a.py": {4}})
    assert result["src/haute/a.py"].statement_targets == 0
    assert result["src/haute/a.py"].missing_branches == ((1, 4),)


def test_no_target_and_missing_artifact_file() -> None:
    config = checker.ChangedCoverageConfig(Path("coverage.json"), ("src/haute/a.py",), 100, 100)
    coverage = {
        "src/haute/a.py": checker.FileCoverage(
            "src/haute/a.py", frozenset({4}), frozenset(), frozenset(), frozenset()
        )
    }
    assert (
        checker.evaluate_changed_coverage(config, coverage, {"src/haute/a.py": {3}})[
            "src/haute/a.py"
        ].statement_targets
        == 0
    )
    with pytest.raises(checker.ChangedCoverageError, match="missing from coverage"):
        checker.evaluate_changed_coverage(config, {}, {"src/haute/a.py": {3}})


def test_untracked_file_targets_all_executable_evidence() -> None:
    config = checker.ChangedCoverageConfig(Path("coverage.json"), ("src/haute/a.py",), 100, 100)
    coverage = {
        "src/haute/a.py": checker.FileCoverage(
            "src/haute/a.py", frozenset({1}), frozenset({2}), frozenset(), frozenset()
        )
    }
    assert checker.evaluate_changed_coverage(config, coverage, {}, {"src/haute/a.py"})[
        "src/haute/a.py"
    ].missing_lines == (2,)


def test_collect_uses_base_and_worktree_and_fails_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = checker.ChangedCoverageConfig(Path("coverage.json"), ("src/haute/a.py",), 100, 100)
    calls: list[list[str]] = []

    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, "+++ b/src/haute/a.py\n@@ -1 +1 @@\n-x\n+y\n" if args[1] == "diff" else ""
        )

    monkeypatch.setattr(checker.subprocess, "run", run)
    changed, untracked = checker.collect_changed_lines(config, tmp_path, "base")
    assert changed == {"src/haute/a.py": {1}}
    assert not untracked and len(calls) == 3
    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "bad ref"),
    )
    with pytest.raises(checker.ChangedCoverageError, match="Git command failed"):
        checker.collect_changed_lines(config, tmp_path, None)


def test_cli_exit_taxonomy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(tmp_path)
    coverage = _coverage(tmp_path)
    monkeypatch.setattr(
        checker, "collect_changed_lines", lambda *args: ({"src/haute/a.py": {3}}, set())
    )
    assert checker.main(["--config", str(config), "--coverage-json", str(coverage)]) == 1
    assert "missing changed lines: 3" in capsys.readouterr().err
    monkeypatch.setattr(
        checker, "collect_changed_lines", lambda *args: ({"src/haute/a.py": {1}}, set())
    )
    assert checker.main(["--config", str(config), "--coverage-json", str(coverage)]) == 0
    assert checker.main(["--config", str(tmp_path / "missing.toml")]) == 2
