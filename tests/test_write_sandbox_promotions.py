"""The monthly write-sandbox survey's verdict (scripts/write_sandbox_promotions.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "write_sandbox_promotions.py"
SPEC = importlib.util.spec_from_file_location("write_sandbox_promotions", _SCRIPT)
assert SPEC and SPEC.loader
promotions = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = promotions
SPEC.loader.exec_module(promotions)

RATCHET = """
strict = ["test_already.py"]

[lint]
"tests/test_flagged.py" = ["0123456789abcdef"]

[census]
"""


@pytest.fixture(autouse=True)
def _no_job_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    # On a runner this names the real step's summary; a test must not append to it.
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _case(module: str, name: str, outcome: str = "pass", *, file: bool = True) -> str:
    where = f' file="tests/{module}.py"' if file else ""
    body = {
        "pass": "",
        "fail": '<failure message="boom"/>',
        "error": '<error message="setup"/>',
        "skip": '<skipped message="no engine"/>',
    }[outcome]
    return f'<testcase classname="tests.{module}" name="{name}"{where} time="0.1">{body}</testcase>'


def _report(root: Path, *cases: str) -> Path:
    path = root / "strict-survey.xml"
    path.write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
        + "".join(cases)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def _ratchet(root: Path) -> Path:
    path = root / "write_sandbox.toml"
    path.write_text(RATCHET, encoding="utf-8")
    return path


def _run(root: Path, report: Path) -> int:
    return promotions.main(["--ratchet", str(_ratchet(root)), str(report)])


def test_a_module_whose_every_test_ran_and_passed_can_join(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        _case("test_clean", "test_a"),
        _case("test_clean", "test_b", "skip"),
        _case("test_red", "test_a"),
        _case("test_red", "test_b", "fail"),
        _case("test_setup", "test_a", "error"),
        _case("test_all_skipped", "test_a", "skip"),
    )

    results = promotions.module_results([report])
    ready, blocked = promotions.promotions(results, [], [])

    # A module whose every test skipped proves nothing about strict.
    assert ready == ["test_clean.py"]
    assert blocked == []


def test_strict_modules_are_not_named_again_and_allowlisted_ones_are_held_back(
    tmp_path: Path,
) -> None:
    report = _report(
        tmp_path,
        _case("test_already", "test_a"),
        _case("test_flagged", "test_a"),
        _case("test_clean", "test_a"),
    )

    ready, blocked = promotions.promotions(
        promotions.module_results([report]), ["test_already.py"], ["tests/test_flagged.py"]
    )

    assert ready == ["test_clean.py"]
    assert blocked == ["test_flagged.py"]


def test_a_case_without_its_file_is_placed_by_its_class_name(tmp_path: Path) -> None:
    report = _report(tmp_path, _case("test_clean", "test_a", file=False))

    assert set(promotions.module_results([report])) == {"test_clean.py"}


def test_the_lane_is_red_while_modules_can_join_and_lists_them_ready_to_paste(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    report = _report(tmp_path, _case("test_clean", "test_a"), _case("test_flagged", "test_a"))

    assert _run(tmp_path, report) == 1
    out = capsys.readouterr().out
    assert '    "test_clean.py",' in out
    assert "convert those writes first): test_flagged.py" in out
    assert '"test_clean.py",' in summary.read_text(encoding="utf-8")


def test_the_lane_is_green_when_no_module_can_join(tmp_path: Path) -> None:
    report = _report(tmp_path, _case("test_already", "test_a"), _case("test_red", "test_a", "fail"))

    assert _run(tmp_path, report) == 0


@pytest.mark.parametrize("content", [None, "<testsuites", '<?xml version="1.0"?><testsuites/>'])
def test_a_survey_without_a_usable_report_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    report = tmp_path / "strict-survey.xml"
    if content is not None:
        report.write_text(content, encoding="utf-8")

    assert _run(tmp_path, report) == 2
    assert "::error title=Write-sandbox survey::" in capsys.readouterr().out


@pytest.mark.meta
def test_the_survey_runs_the_suite_strict_and_hands_its_report_to_the_verdict() -> None:
    workflow = yaml.safe_load(
        (_REPO_ROOT / ".github/workflows/write-sandbox-survey.yml").read_text("utf-8")
    )
    steps = workflow["jobs"]["write-sandbox-survey"]["steps"]
    (survey,) = [step for step in steps if "--junitxml" in step.get("run", "")]
    (verdict,) = [step for step in steps if "write_sandbox_promotions.py" in step.get("run", "")]

    assert survey["env"]["HAUTE_TEST_WRITE_SANDBOX"] == "strict"
    assert "--junitxml=.cache/junit/strict-survey.xml" in survey["run"]
    assert verdict["run"].split()[-1] == ".cache/junit/strict-survey.xml"
