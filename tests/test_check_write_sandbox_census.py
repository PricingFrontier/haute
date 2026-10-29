"""The coverage gate's write-sandbox census check (scripts/check_write_sandbox_census.py)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_write_sandbox_census.py"
SPEC = importlib.util.spec_from_file_location("check_write_sandbox_census", _SCRIPT)
assert SPEC and SPEC.loader
census = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = census
SPEC.loader.exec_module(census)

ROOTS = {
    "repo": "/home/runner/work/haute/haute",
    "home": "/home/runner",
    "runner-temp": "/home/runner/work/_temp",
}
LISTED = """
[census."repo:mlruns"]
reason = "MLflow's default store."
"""


@pytest.fixture(autouse=True)
def _no_job_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    # On a runner this names the real step's summary; a test must not append to it.
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


def _record(
    root: Path, name: str, writes: list[tuple[str, str]], fields: dict[str, Any] | None = None
) -> Path:
    payload: dict[str, Any] = {
        "version": 1,
        "roots": ROOTS,
        "writes": [
            {"nodeid": nodeid, "api": "open", "path": path, "detail": "mode='w'"}
            for path, nodeid in writes
        ],
    }
    payload.update(fields or {})
    path = root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ratchet(root: Path, text: str = LISTED) -> Path:
    path = root / "write_sandbox.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _run(root: Path, *records: Path, ratchet: Path | None = None) -> int:
    paths = [str(record) for record in records]
    return census.main(["--ratchet", str(ratchet or _ratchet(root)), *paths])


# ── Classes ─────────────────────────────────────────────────────────────────


def test_a_write_takes_the_deepest_root_that_holds_it() -> None:
    # The checkout and the runner's temp directory both sit inside the home directory.
    assert census.classify("/home/runner/work/haute/haute/mlruns/0/meta.yaml", ROOTS) == (
        "repo:mlruns"
    )
    assert (
        census.classify("/home/runner/work/_temp/_runner_file_commands/step_summary_1", ROOTS)
        == "runner-temp:_runner_file_commands"
    )
    assert census.classify("/home/runner/.cache/haute/x.json", ROOTS) == "home:.cache"


def test_a_write_under_no_root_is_classed_by_its_first_two_components() -> None:
    assert census.classify("/etc/haute/settings.toml", ROOTS) == "other:/etc/haute"
    assert census.classify("/home/runnerx/file", ROOTS) == "other:/home/runnerx"


def test_windows_paths_are_classed_as_their_posix_form() -> None:
    roots = {"repo": "C:\\Users\\dev\\haute"}
    assert census.classify("C:\\Users\\dev\\haute\\.haute_cache\\log", roots) == (
        "repo:.haute_cache"
    )


def test_only_the_harness_writes_are_recognised_as_the_harness() -> None:
    rewrite = "/w/haute/tests/__pycache__/test_x.cpython-312-pytest-9.0.3.pyc.2449"
    assert census.harness_kind(rewrite) == "pytest assertion-rewrite bytecode"
    # A test writing bytecode itself is a test's write.
    assert census.harness_kind("/w/haute/tests/__pycache__/test_x.cpython-312.pyc") is None


# ── The gate ────────────────────────────────────────────────────────────────


def test_the_census_holds_when_every_listed_class_is_recorded_and_no_other(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shard1 = _record(
        tmp_path,
        "census-shard1.json",
        [
            ("/home/runner/work/haute/haute/mlruns/0/meta.yaml", "tests/test_a.py::test_one"),
            ("/w/tests/__pycache__/test_b.cpython-312-pytest-9.0.3.pyc.7", "tests/test_b.py::t"),
        ],
    )
    shard2 = _record(tmp_path, "census-shard2.json", [])

    assert _run(tmp_path, shard1, shard2) == 0
    assert "1 writes in 1 listed classes, 1 harness writes" in capsys.readouterr().out


def test_a_new_class_fails_and_names_its_write_and_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shard = _record(
        tmp_path,
        "census-shard1.json",
        [
            ("/home/runner/work/haute/haute/mlruns/0/meta.yaml", "tests/test_a.py::test_one"),
            ("/home/runner/work/haute/haute/data/out.csv", "tests/test_c.py::test_three"),
        ],
    )

    assert _run(tmp_path, shard) == 1
    out = capsys.readouterr().out
    assert "::error title=Write-sandbox census::A new class of write" in out
    assert "repo:data" in out
    assert "/home/runner/work/haute/haute/data/out.csv from tests/test_c.py::test_three" in out


def test_a_listed_class_no_shard_recorded_fails_so_the_table_only_shrinks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shard = _record(tmp_path, "census-shard1.json", [])

    assert _run(tmp_path, shard) == 1
    assert "No coverage shard recorded a write in repo:mlruns" in capsys.readouterr().out


def test_harness_writes_alone_never_make_a_class(tmp_path: Path) -> None:
    shard = _record(
        tmp_path,
        "census-shard1.json",
        [("/w/tests/__pycache__/test_x.cpython-312-pytest-9.0.3.pyc.7", "tests/test_x.py::t")],
    )

    assert _run(tmp_path, shard, ratchet=_ratchet(tmp_path, "[census]\n")) == 0


def test_a_shard_without_its_record_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shard = _record(
        tmp_path,
        "census-shard1.json",
        [("/home/runner/work/haute/haute/mlruns/0/meta.yaml", "tests/test_a.py::test_one")],
    )

    assert _run(tmp_path, shard, tmp_path / "census-shard2.json") == 1
    assert "census-shard2.json: no census record" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"version": 2}, "not a version-1 census record"),
        ({"roots": None}, "without its roots"),
        ({"writes": [{"api": "open"}]}, "without its path and test"),
    ],
)
def test_a_malformed_record_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], overrides: dict[str, Any], message: str
) -> None:
    shard = _record(tmp_path, "census-shard1.json", [], overrides)

    assert _run(tmp_path, shard) == 1
    assert message in capsys.readouterr().out


def test_a_listed_class_must_give_its_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shard = _record(tmp_path, "census-shard1.json", [])
    ratchet = _ratchet(tmp_path, '[census."repo:mlruns"]\nreason = " "\n')

    assert _run(tmp_path, shard, ratchet=ratchet) == 1
    assert "census class 'repo:mlruns' gives no reason" in capsys.readouterr().out


def test_the_job_summary_tables_every_class_and_the_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    shard = _record(
        tmp_path,
        "census-shard1.json",
        [
            ("/home/runner/work/haute/haute/data/out.csv", "tests/test_c.py::test_three"),
            ("/w/tests/__pycache__/test_b.cpython-312-pytest-9.0.3.pyc.7", "tests/test_b.py::t"),
        ],
    )

    assert _run(tmp_path, shard) == 1
    table = summary.read_text(encoding="utf-8")
    assert "| `repo:data` | **new** | 1 | 1 |" in table
    assert "| `repo:mlruns` | **not recorded** | 0 | 0 |" in table
    assert "| pytest assertion-rewrite bytecode | harness | 1 | 1 |" in table


def test_the_repository_table_parses_and_gives_every_class_a_reason() -> None:
    listed = census.load_listed(census.RATCHET_PATH)
    assert listed, "the census table is empty: delete this test with the last class"


# ── The workflow ────────────────────────────────────────────────────────────


@pytest.mark.meta
def test_every_coverage_shard_records_its_census_and_the_gate_reads_each() -> None:
    workflow = yaml.safe_load((_REPO_ROOT / ".github/workflows/ci.yml").read_text("utf-8"))
    shard_job = workflow["jobs"]["backend-coverage-shard"]
    shards = shard_job["strategy"]["matrix"]["shard"]
    (pytest_step,) = [step for step in shard_job["steps"] if "--shard=" in step.get("run", "")]
    out = pytest_step["env"]["HAUTE_TEST_WRITE_SANDBOX_CENSUS_OUT"]
    assert out == ".cache/write-sandbox/census-shard${{ matrix.shard }}.json"
    (upload,) = [
        step
        for step in shard_job["steps"]
        if step.get("with", {}).get("name") == "write-sandbox-census-${{ matrix.shard }}"
    ]
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"

    gate_steps = workflow["jobs"]["backend-coverage-gate"]["steps"]
    (check,) = [
        step for step in gate_steps if "check_write_sandbox_census.py" in step.get("run", "")
    ]
    assert check["if"] == "${{ !cancelled() }}"
    read = [word for word in check["run"].split() if word.endswith(".json")]
    assert read == [f".cache/write-sandbox/census-shard{shard}.json" for shard in shards]
