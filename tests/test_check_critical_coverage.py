from __future__ import annotations

import json
import textwrap
from pathlib import Path

from scripts import check_critical_coverage


def _write_config(tmp_path: Path, *, coverage_json: str = "coverage.json") -> Path:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            [tool.haute.critical_coverage]
            coverage_json = "{coverage_json}"

            [[tool.haute.critical_coverage.files]]
            path = "src/haute/executor.py"
            min_statement_coverage = 95.0
            min_branch_coverage = 90.0
            rationale = "Execution touches generated user pricing logic."
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_coverage(
    tmp_path: Path,
    *,
    file_path: str = r"src\haute\executor.py",
    percent_statements_covered: float = 95.1,
    percent_branches_covered: float = 90.1,
    branch_coverage: bool = True,
) -> Path:
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(
        json.dumps(
            {
                "meta": {"format": 3, "branch_coverage": branch_coverage},
                "totals": {"percent_covered": 99.9},
                "files": {
                    file_path: {
                        "executed_lines": [1, 2, 3],
                        "missing_lines": [20, 21],
                        "missing_branches": [[10, 11]],
                        "summary": {
                            "percent_statements_covered": percent_statements_covered,
                            "percent_branches_covered": percent_branches_covered,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return coverage_path


def test_checker_accepts_coverage_json_with_windows_paths(tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path)
    coverage_path = _write_coverage(tmp_path)

    exit_code = check_critical_coverage.main(
        ["--config", str(config_path), "--coverage-json", str(coverage_path)]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Critical coverage gates passed" in output
    assert "src/haute/executor.py" in output


def test_checker_fails_when_global_coverage_hides_weak_critical_file(
    tmp_path: Path, capsys
) -> None:
    config_path = _write_config(tmp_path)
    coverage_path = _write_coverage(
        tmp_path,
        percent_statements_covered=89.9,
        percent_branches_covered=89.0,
    )

    exit_code = check_critical_coverage.main(
        ["--config", str(config_path), "--coverage-json", str(coverage_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Critical coverage gates failed" in captured.err
    assert "src/haute/executor.py" in captured.err
    assert "statement coverage 89.90% < 95.00%" in captured.err
    assert "branch coverage 89.00% < 90.00%" in captured.err
    assert "missing lines: 20, 21" in captured.err


def test_checker_fails_loudly_when_critical_file_is_absent(tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path)
    coverage_path = _write_coverage(tmp_path, file_path="src/haute/parser.py")

    exit_code = check_critical_coverage.main(
        ["--config", str(config_path), "--coverage-json", str(coverage_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Critical file is missing from coverage artifact" in captured.err
    assert "src/haute/executor.py" in captured.err


def test_checker_rejects_artifacts_without_branch_coverage(tmp_path: Path, capsys) -> None:
    config_path = _write_config(tmp_path)
    coverage_path = _write_coverage(tmp_path, branch_coverage=False)

    exit_code = check_critical_coverage.main(
        ["--config", str(config_path), "--coverage-json", str(coverage_path)]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "branch coverage must be enabled" in captured.err
