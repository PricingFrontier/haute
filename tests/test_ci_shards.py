"""CI file sharding (tests/_ci_shards.py), its durations refresh, and the workflow invariants."""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts import refresh_test_durations
from tests._ci_shards import (
    ShardSelection,
    ShardSpec,
    assign_modules,
    load_durations,
    unrecorded_shard,
)
from tests._source_files import REPO_ROOT

pytest_plugins = ["pytester"]

_SHARDED_RUN = re.compile(r"--shard=\$\{\{\s*matrix\.shard\s*\}\}/(\d+)")


# --- shard specification ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"), [("1/1", ShardSpec(1, 1)), ("3/4", ShardSpec(3, 4))]
)
def test_a_shard_spec_is_k_of_n(value: str, expected: ShardSpec) -> None:
    assert ShardSpec.parse(value) == expected


@pytest.mark.parametrize(
    "value", ["", "3", "0/3", "4/3", "1/0", "-1/2", "01/2", "1/2/3", " 1/2", "a/b"]
)
def test_anything_but_k_of_n_is_rejected(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="expected K/N with 1 <= K <= N"):
        ShardSpec.parse(value)


# --- durations file -------------------------------------------------------------


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _lanes(**tables: dict[str, object]) -> dict[str, object]:
    return {"version": 2, "lanes": tables}


def test_each_lane_reads_its_own_table(tmp_path: Path) -> None:
    path = _write_json(
        tmp_path / "durations.json",
        _lanes(
            coverage={"tests/test_a.py": 2, "tests/sub/test_b.py": 0.5},
            compat={"tests/test_a.py": 1.25},
        ),
    )

    assert load_durations(path, "coverage") == {"tests/test_a.py": 2.0, "tests/sub/test_b.py": 0.5}
    assert load_durations(path, "compat") == {"tests/test_a.py": 1.25}


def test_a_lane_without_a_table_is_refused_naming_the_tables_there(tmp_path: Path) -> None:
    path = _write_json(tmp_path / "durations.json", _lanes(coverage={}, compat={}))

    with pytest.raises(ValueError, match=re.escape("no durations for lane 'nightly'")) as raised:
        load_durations(path, "nightly")
    assert "['compat', 'coverage']" in str(raised.value)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (None, "Cannot read test durations"),
        ("{not json", "Cannot read test durations"),
        ([], "must be a JSON object"),
        ({"version": 1, "files": {}}, "must be a JSON object"),
        ({"version": 2}, "must be a JSON object"),
        ({"version": 2, "lanes": {}, "source": "x"}, "must be a JSON object"),
        ({"version": 2, "lanes": []}, "must be a JSON object"),
        ({"version": 2, "lanes": {"ci": []}}, "must be a JSON object"),
        (_lanes(ci={"/tests/test_a.py": 1}), "not a repository-relative"),
        (_lanes(ci={"tests/../test_a.py": 1}), "not a repository-relative"),
        (_lanes(ci={"tests\\test_a.py": 1}), "not a repository-relative"),
        (_lanes(ci={"tests//test_a.py": 1}), "not a repository-relative"),
        (_lanes(ci={"tests/test_a.txt": 1}), "not a repository-relative"),
        (_lanes(ci={"tests/test_a.py": -1}), "must be finite and >= 0"),
        (_lanes(ci={"tests/test_a.py": float("nan")}), "must be finite and >= 0"),
        (_lanes(ci={"tests/test_a.py": True}), "must be finite and >= 0"),
        (_lanes(ci={"tests/test_a.py": "1"}), "must be finite and >= 0"),
    ],
)
def test_a_malformed_durations_file_is_refused(
    tmp_path: Path, contents: object, message: str
) -> None:
    path = tmp_path / "durations.json"
    if isinstance(contents, str):
        path.write_text(contents, encoding="utf-8")
    elif contents is not None:
        _write_json(path, contents)

    with pytest.raises(ValueError, match=re.escape(message)) as raised:
        load_durations(path, "ci")
    assert str(path) in str(raised.value)


# --- assignment -----------------------------------------------------------------


def test_modules_go_longest_first_onto_the_least_loaded_shard() -> None:
    durations = {"a.py": 10.0, "b.py": 7.0, "c.py": 5.0, "d.py": 4.0, "e.py": 3.0, "f.py": 1.0}

    assignment = assign_modules(durations, 2)

    assert assignment == {"a.py": 1, "b.py": 2, "c.py": 2, "d.py": 1, "e.py": 2, "f.py": 1}
    loads = [sum(durations[m] for m, shard in assignment.items() if shard == k) for k in (1, 2)]
    assert loads == [15.0, 15.0]


def test_equal_durations_are_placed_by_path_then_lower_shard() -> None:
    assert assign_modules({"c.py": 1.0, "a.py": 1.0, "b.py": 1.0}, 2) == {
        "a.py": 1,
        "b.py": 2,
        "c.py": 1,
    }


def test_an_unrecorded_module_always_hashes_to_the_same_shard() -> None:
    # CRC-32 is fixed by definition, unlike hash(), which is salted per process.
    assert [unrecorded_shard("tests/test_example.py", count) for count in (1, 2, 3, 4)] == [
        1,
        2,
        3,
        4,
    ]


# --- selection ------------------------------------------------------------------


def _touch(root: Path, *relative: str) -> None:
    for name in relative:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("", encoding="utf-8")


def test_a_shard_ignores_only_the_test_modules_of_other_shards(tmp_path: Path) -> None:
    _touch(tmp_path, "tests/test_a.py", "tests/test_b.py", "tests/helpers.py", "tests/conftest.py")
    durations = {"tests/test_a.py": 5.0, "tests/test_b.py": 4.0}
    first = ShardSelection(ShardSpec(1, 2), durations, tmp_path, ["test_*.py"])
    second = ShardSelection(ShardSpec(2, 2), durations, tmp_path, ["test_*.py"])

    assert first.pytest_ignore_collect(tmp_path / "tests" / "test_a.py") is None
    assert first.pytest_ignore_collect(tmp_path / "tests" / "test_b.py") is True
    assert second.pytest_ignore_collect(tmp_path / "tests" / "test_a.py") is True
    assert second.pytest_ignore_collect(tmp_path / "tests" / "test_b.py") is None
    for selection in (first, second):
        for other in ("tests/helpers.py", "tests/conftest.py", "tests", "tests/data.json"):
            assert selection.pytest_ignore_collect(tmp_path / other) is None


def test_a_recorded_module_that_no_longer_exists_carries_no_weight(tmp_path: Path) -> None:
    _touch(tmp_path, "tests/test_a.py", "tests/test_b.py")
    durations = {"tests/test_gone.py": 100.0, "tests/test_a.py": 5.0, "tests/test_b.py": 4.0}

    selection = ShardSelection(ShardSpec(1, 2), durations, tmp_path, ["test_*.py"])

    # With the deleted module weighing in, both remaining modules would share shard 2.
    assert [selection.shard_of("tests/test_a.py"), selection.shard_of("tests/test_b.py")] == [1, 2]


def test_a_module_without_a_recorded_duration_goes_to_its_hashed_shard(tmp_path: Path) -> None:
    _touch(tmp_path, "tests/test_example.py")

    selection = ShardSelection(ShardSpec(1, 4), {}, tmp_path, ["test_*.py"])

    assert selection.shard_of("tests/test_example.py") == 4
    assert selection.pytest_ignore_collect(tmp_path / "tests" / "test_example.py") is True


def test_a_shard_runs_its_longest_modules_first_each_in_its_own_order(tmp_path: Path) -> None:
    _touch(tmp_path, "tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "tests/test_d.py")
    durations = {"tests/test_a.py": 2.0, "tests/test_b.py": 9.0, "tests/test_c.py": 4.0}
    selection = ShardSelection(ShardSpec(1, 1), durations, tmp_path, ["test_*.py"])
    items: list[Any] = [
        SimpleNamespace(path=tmp_path / "tests" / f"test_{module}.py", name=f"{module}{index}")
        for module in "abcd"
        for index in (1, 2)
    ]

    selection.pytest_collection_modifyitems(items)

    # test_d is unrecorded and counts as the median, 4 s: a tie with test_c, which
    # keeps their collection order.
    assert [item.name for item in items] == ["b1", "b2", "c1", "c2", "d1", "d2", "a1", "a2"]


def test_python_files_patterns_naming_directories_are_refused(tmp_path: Path) -> None:
    with pytest.raises(pytest.UsageError, match="unsupported patterns"):
        ShardSelection(ShardSpec(1, 2), {}, tmp_path, ["test_*.py", "checks/check_*.py"])


def test_a_test_module_outside_the_rootdir_cannot_be_placed(tmp_path: Path) -> None:
    selection = ShardSelection(ShardSpec(1, 2), {}, tmp_path / "root", ["test_*.py"])

    with pytest.raises(pytest.UsageError, match="outside the rootdir"):
        selection.pytest_ignore_collect(tmp_path / "elsewhere" / "test_a.py")


# --- end to end through pytest ----------------------------------------------------

_MODULES = ("test_alpha", "test_beta", "test_gamma", "test_delta", "test_epsilon", "test_zeta")
_RECORDED = {"test_alpha.py": 9.0, "test_beta.py": 6.0, "test_gamma.py": 4.0, "test_delta.py": 1.0}


def _sharded_project(pytester: pytest.Pytester) -> set[str]:
    """Six modules, four with recorded durations; each logs its own import."""
    pytester.makeini("[pytest]\nasyncio_default_fixture_loop_scope = function\n")
    pytester.makeconftest(
        """
        from tests import _ci_shards


        def pytest_addoption(parser):
            _ci_shards.add_options(parser)


        def pytest_configure(config):
            _ci_shards.register(config)
        """
    )
    for module in _MODULES:
        pytester.makepyfile(
            **{
                module: f"""
                from pathlib import Path

                with (Path(__file__).parent / "imports.log").open("a", encoding="utf-8") as log:
                    log.write("{module}\\n")


                def test_one():
                    pass


                def test_two():
                    pass
                """
            }
        )
    (pytester.path / "durations.json").write_text(
        json.dumps({"version": 2, "lanes": {"ci": _RECORDED}}), encoding="utf-8"
    )
    return {f"{module}.py::{test}" for module in _MODULES for test in ("test_one", "test_two")}


def _passed(result: pytest.RunResult) -> set[str]:
    return {
        report.nodeid
        for report in result.reprec.getreports("pytest_runtest_logreport")
        if report.when == "call" and report.passed
    }


def _imports(pytester: pytest.Pytester) -> set[str]:
    log = pytester.path / "imports.log"
    modules = set(log.read_text(encoding="utf-8").split()) if log.exists() else set()
    log.unlink(missing_ok=True)
    return modules


def test_the_shards_of_a_lane_run_every_test_exactly_once(pytester: pytest.Pytester) -> None:
    every_test = _sharded_project(pytester)

    per_shard = []
    for index in (1, 2, 3):
        result = pytester.runpytest(
            f"--shard={index}/3", "--shard-lane=ci", "--shard-durations=durations.json"
        )
        result.stdout.fnmatch_lines([f"shard {index}/3: * recorded test modules*"])
        passed = _passed(result)
        # A module outside the shard is ignored before it is ever imported.
        assert _imports(pytester) == {nodeid.split(".py::")[0] for nodeid in passed}
        per_shard.append(passed)

    assert sum(len(passed) for passed in per_shard) == len(every_test)
    assert set().union(*per_shard) == every_test
    assert all(passed for passed in per_shard)


def _module_order(result: pytest.RunResult) -> list[str]:
    calls = [
        report.nodeid.split(".py::")[0]
        for report in result.reprec.getreports("pytest_runtest_logreport")
        if report.when == "call"
    ]
    return list(dict.fromkeys(calls))


def test_a_sharded_run_executes_its_longest_modules_first(pytester: pytest.Pytester) -> None:
    _sharded_project(pytester)

    sharded = pytester.runpytest(
        "--shard=1/1", "--shard-lane=ci", "--shard-durations=durations.json"
    )

    # alpha 9 s, beta 6, gamma 4 and delta 1 are recorded; epsilon and zeta count as
    # the median, 5 s, and keep their collection order.
    assert _module_order(sharded) == [
        "test_alpha",
        "test_beta",
        "test_epsilon",
        "test_zeta",
        "test_gamma",
        "test_delta",
    ]
    sharded.stdout.fnmatch_lines(["shard 1/1: *; longest modules first"])
    # An unsharded run keeps pytest's own order.
    assert _module_order(pytester.runpytest()) == sorted(_MODULES)


def _passed_in_summary(result: pytest.RunResult) -> set[str]:
    return {
        line.removeprefix("PASSED ") for line in result.stdout.lines if line.startswith("PASSED ")
    }


def test_xdist_workers_select_the_same_modules_as_a_single_process(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sharded_project(pytester)
    args = ("--shard=2/3", "--shard-lane=ci", "--shard-durations=durations.json")
    single = _passed(pytester.runpytest(*args))
    # xdist hands workers the sys.path its controller had when xdist was imported, so the
    # controller is a subprocess started with the repository (tests._ci_shards) importable.
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))

    workers = pytester.runpytest_subprocess(*args, "-rA", "-n", "2")

    assert workers.ret == pytest.ExitCode.OK
    assert single
    assert _passed_in_summary(workers) == single


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--shard=4/3", "--shard-lane=ci"], "expected K/N with 1 <= K <= N"),
        (
            ["--shard=1/2", "--shard-lane=ci", "--shard-durations=missing.json"],
            "Cannot read test durations",
        ),
        (["--shard=1/2", "--shard-durations=durations.json"], "--shard requires --shard-lane"),
        (
            ["--shard=1/2", "--shard-lane=nightly", "--shard-durations=durations.json"],
            "no durations for lane 'nightly'",
        ),
        (["--shard-durations=durations.json"], "--shard-durations requires --shard"),
        (["--shard-lane=ci"], "--shard-lane requires --shard"),
    ],
)
def test_a_bad_shard_request_fails_before_collection(
    pytester: pytest.Pytester, args: list[str], message: str
) -> None:
    _sharded_project(pytester)

    result = pytester.runpytest(*args)

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    assert message in result.stderr.str()
    assert _imports(pytester) == set()


# --- durations refresh --------------------------------------------------------------


def _report(path: Path, *cases: dict[str, str]) -> Path:
    suite = ElementTree.Element("testsuite", name="pytest")
    for case in cases:
        ElementTree.SubElement(suite, "testcase", case)
    root = ElementTree.Element("testsuites")
    root.append(suite)
    path.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _case(file: str, name: str, time: str) -> dict[str, str]:
    return {"file": file, "classname": file[:-3].replace("/", "."), "name": name, "time": time}


def test_the_refresh_sums_each_modules_test_case_times(tmp_path: Path) -> None:
    reports = tmp_path / "junit"
    _report(
        reports / "shard1" / "report.xml",
        _case("tests/test_a.py", "test_one", "1.20"),
        _case("tests/test_a.py", "test_two", "0.40"),
    )
    _report(
        reports / "shard2" / "report.xml",
        _case("tests\\test_b.py", "test_one", "4.04"),
        _case("tests/test_c.py", "test_one", "0.01"),
    )
    output = _write_json(tmp_path / "durations.json", _lanes(compat={"tests/test_a.py": 9.0}))

    assert (
        refresh_test_durations.main([str(reports), "--lane", "coverage", "--output", str(output)])
        == 0
    )

    assert json.loads(output.read_text(encoding="utf-8")) == {
        "version": 2,
        "lanes": {
            # The other lane's table is kept.
            "compat": {"tests/test_a.py": 9.0},
            "coverage": {"tests/test_a.py": 1.6, "tests/test_b.py": 4.0, "tests/test_c.py": 0.0},
        },
    }
    # The shard selection reads exactly what the refresh writes.
    assert load_durations(output, "coverage") == {
        "tests/test_a.py": 1.6,
        "tests/test_b.py": 4.0,
        "tests/test_c.py": 0.0,
    }


def test_the_refresh_keeps_each_modules_median_across_runs(tmp_path: Path) -> None:
    # Three runs of two shards; each run sees the same test once, so a run is one table.
    runs = tmp_path / "junit"
    for run, (a, b) in enumerate((("1.0", "8.0"), ("3.0", "2.0"), ("2.0", "4.0")), start=1):
        _report(runs / f"run{run}" / "shard1" / "report.xml", _case("tests/test_a.py", "t", a))
        _report(runs / f"run{run}" / "shard2" / "report.xml", _case("tests/test_b.py", "t", b))
    # A module only the latest run has keeps that run's time.
    _report(runs / "run3" / "shard2" / "new.xml", _case("tests/test_new.py", "t", "5.0"))
    output = _write_json(tmp_path / "durations.json", _lanes(coverage={"tests/test_a.py": 9.0}))

    assert (
        refresh_test_durations.main(
            [
                *(str(runs / f"run{run}") for run in (1, 2, 3)),
                "--median-of-runs",
                "--lane",
                "compat",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    assert load_durations(output, "compat") == {
        "tests/test_a.py": 2.0,
        "tests/test_b.py": 4.0,
        "tests/test_new.py": 5.0,
    }
    assert load_durations(output, "coverage") == {"tests/test_a.py": 9.0}


def test_without_median_of_runs_two_runs_of_one_test_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for run in (1, 2):
        _report(tmp_path / f"run{run}" / "report.xml", _case("tests/test_a.py", "t", "1"))
    output = tmp_path / "durations.json"

    assert (
        refresh_test_durations.main(
            [
                str(tmp_path / "run1"),
                str(tmp_path / "run2"),
                "--lane",
                "compat",
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert "appears twice" in capsys.readouterr().err
    assert not output.exists()


def test_the_refresh_will_not_overwrite_a_file_it_cannot_read_as_lanes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(tmp_path / "report.xml", _case("tests/test_a.py", "test_one", "1"))
    output = _write_json(tmp_path / "durations.json", {"version": 1, "files": {}})

    assert (
        refresh_test_durations.main([str(report), "--lane", "compat", "--output", str(output)]) == 2
    )

    assert "refusing to overwrite the other lanes' tables" in capsys.readouterr().err
    assert json.loads(output.read_text(encoding="utf-8")) == {"version": 1, "files": {}}


@pytest.mark.parametrize(
    ("cases", "message"),
    [
        ([{"classname": "tests.test_a", "name": "test_one", "time": "1"}], "junit_family=xunit1"),
        ([_case("tests/test_a.py", "test_one", "fast")], "no numeric 'time'"),
        ([_case("tests/test_a.py", "test_one", "-1")], "not finite and >= 0"),
        ([_case("tests/test_a.py", "test_one", "inf")], "not finite and >= 0"),
        ([_case("/abs/tests/test_a.py", "test_one", "1")], "not a repository-relative"),
        ([_case("tests/test_a.py", "t", "1"), _case("tests/test_a.py", "t", "2")], "appears twice"),
        ([], "no test cases"),
    ],
)
def test_the_refresh_refuses_reports_it_cannot_trust(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    cases: list[dict[str, str]],
    message: str,
) -> None:
    report = _report(tmp_path / "report.xml", *cases)
    output = tmp_path / "durations.json"

    assert (
        refresh_test_durations.main([str(report), "--lane", "coverage", "--output", str(output)])
        == 2
    )

    assert message in capsys.readouterr().err
    assert not output.exists()


def test_the_refresh_refuses_an_unreadable_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "report.xml"
    report.write_text("<testsuites><testcase", encoding="utf-8")

    output = tmp_path / "out.json"
    assert (
        refresh_test_durations.main([str(report), "--lane", "coverage", "--output", str(output)])
        == 2
    )
    assert "Cannot read JUnit report" in capsys.readouterr().err


# --- workflow invariants --------------------------------------------------------------


def _ci_jobs() -> dict[str, Any]:
    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8"))
    return workflow["jobs"]


def _runs(job: dict[str, Any]) -> list[str]:
    return [step["run"] for step in job.get("steps", []) if "run" in step]


def _shard_count(job: dict[str, Any]) -> int | None:
    counts = {int(count) for run in _runs(job) for count in _SHARDED_RUN.findall(run)}
    assert len(counts) <= 1, f"one job runs different shard counts: {sorted(counts)}"
    return counts.pop() if counts else None


@pytest.mark.meta
def test_every_sharded_ci_job_runs_each_of_its_shards_once() -> None:
    sharded = {}
    for name, job in _ci_jobs().items():
        count = _shard_count(job)
        if count is not None:
            # A shard missing from the matrix would silently drop its modules.
            assert job["strategy"]["matrix"]["shard"] == list(range(1, count + 1)), name
            sharded[name] = count

    assert set(sharded) == {"backend-coverage-shard", "backend-compat", "browser-e2e"}


@pytest.mark.meta
def test_the_coverage_gate_combines_exactly_the_coverage_shards() -> None:
    jobs = _ci_jobs()
    shards = _shard_count(jobs["backend-coverage-shard"])
    assert shards is not None
    pytest_steps = [
        step
        for step in jobs["backend-coverage-shard"]["steps"]
        if "--shard=" in step.get("run", "")
    ]
    assert [step["env"]["COVERAGE_FILE"] for step in pytest_steps] == [
        ".coverage.shard${{ matrix.shard }}"
    ]

    combine = ["uv", "run", "coverage", "combine"]
    combined = [
        words[len(combine) :]
        for run in _runs(jobs["backend-coverage-gate"])
        for words in (line.split() for line in run.splitlines())
        if words[: len(combine)] == combine
    ]

    assert combined == [[f".coverage.shard{index}" for index in range(1, shards + 1)]]


def _uploads(job: dict[str, Any]) -> list[str]:
    return [
        step["with"]["name"]
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact")
    ]


@pytest.mark.meta
def test_the_refresh_recipe_downloads_each_lanes_junit_artifacts() -> None:
    jobs = _ci_jobs()
    recipe = refresh_test_durations.__doc__ or ""

    assert "backend-junit-${{ matrix.shard }}" in _uploads(jobs["backend-coverage-shard"])
    assert '--pattern "backend-junit-*"' in recipe
    assert "backend-compat-junit-${{ matrix.python-version }}-${{ matrix.shard }}" in _uploads(
        jobs["backend-compat"]
    )
    assert '--pattern "backend-compat-junit-3.13-*"' in recipe
    assert "--lane coverage" in recipe and "--lane compat" in recipe


@pytest.mark.meta
def test_each_sharded_backend_lane_balances_on_a_table_the_durations_file_records() -> None:
    lanes = {
        name: re.findall(r"--shard-lane=(\S+)", "\n".join(_runs(job)))
        for name, job in _ci_jobs().items()
        if name in {"backend-coverage-shard", "backend-compat"}
    }

    assert lanes == {"backend-coverage-shard": ["coverage"], "backend-compat": ["compat"]}
    for lane in ("coverage", "compat"):
        assert load_durations(REPO_ROOT / "scripts" / "test_file_durations.json", lane)
