from __future__ import annotations

from pathlib import Path

import pytest

from scripts import run_mutation_pytest


def test_absolute_test_target_preserves_selector_and_non_test_arguments(tmp_path: Path) -> None:
    assert (
        run_mutation_pytest._absolute_test_target(
            r"tests\test_example.py::TestGroup::test_case",
            repo_root=tmp_path,
        )
        == str(tmp_path / "tests" / "test_example.py") + "::TestGroup::test_case"
    )
    assert run_mutation_pytest._absolute_test_target("-q", repo_root=tmp_path) == "-q"
    assert run_mutation_pytest._absolute_test_target("tests-extra", repo_root=tmp_path) == (
        "tests-extra"
    )
    with pytest.raises(ValueError, match="escapes the repository tests directory"):
        run_mutation_pytest._absolute_test_target("tests/../pyproject.toml", repo_root=tmp_path)


def test_test_targets_file_expands_comments_and_preserves_order(tmp_path: Path) -> None:
    manifest = tmp_path / "tests" / "mutation" / "targets.txt"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "# focused witnesses\n\ntests/test_first.py::test_one\ntests/test_second.py\n",
        encoding="utf-8",
    )

    assert run_mutation_pytest._expand_test_target_files(
        ["--test-targets-file", "tests/mutation/targets.txt", "-q"],
        repo_root=tmp_path,
    ) == ["tests/test_first.py::test_one", "tests/test_second.py", "-q"]


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("", "empty"),
        ("# comments only\n", "empty"),
        ("tests/test_one.py\ntests/test_one.py\n", "duplicate"),
        ("--disable-warnings\n", "non-test target"),
    ],
)
def test_test_targets_file_rejects_malformed_contents(
    tmp_path: Path, contents: str, message: str
) -> None:
    manifest = tmp_path / "targets.txt"
    manifest.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        run_mutation_pytest._test_targets_from_file(str(manifest), repo_root=tmp_path)


def test_test_targets_file_option_fails_closed_for_missing_or_escaping_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="requires a path"):
        run_mutation_pytest._expand_test_target_files(["--test-targets-file"], repo_root=tmp_path)
    with pytest.raises(ValueError, match="escapes the repository"):
        run_mutation_pytest._test_targets_from_file(
            str(tmp_path.parent / "outside.txt"), repo_root=tmp_path
        )


def test_run_uses_repo_configuration_and_removes_relative_runtime_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    original_cwd = Path.cwd()
    observed: dict[str, object] = {}

    def fake_pytest_main(arguments: list[str]) -> int:
        working_dir = Path.cwd()
        observed["working_dir"] = working_dir
        observed["arguments"] = arguments
        cache_dir = working_dir / ".haute_cache"
        cache_dir.mkdir()
        (cache_dir / "marker").write_text("isolated", encoding="utf-8")
        return 7

    monkeypatch.setattr(run_mutation_pytest.pytest, "main", fake_pytest_main)

    exit_code = run_mutation_pytest.run(
        ["tests/test_example.py::test_case", "-q"],
        repo_root=repo_root,
    )

    working_dir = observed["working_dir"]
    assert isinstance(working_dir, Path)
    assert exit_code == 7
    assert Path.cwd() == original_cwd
    assert not working_dir.exists()
    assert observed["arguments"] == [
        "-c",
        str(repo_root / "pyproject.toml"),
        "--rootdir",
        str(repo_root),
        "--basetemp",
        str(working_dir / "pytest"),
        str(repo_root / "tests" / "test_example.py") + "::test_case",
        "-q",
    ]


def test_run_restores_cwd_and_removes_state_when_pytest_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_cwd = Path.cwd()
    observed_working_dir: Path | None = None

    def raise_from_pytest(_arguments: list[str]) -> int:
        nonlocal observed_working_dir
        observed_working_dir = Path.cwd()
        (observed_working_dir / "relative-state").write_text("temporary", encoding="utf-8")
        raise RuntimeError("pytest failed unexpectedly")

    monkeypatch.setattr(run_mutation_pytest.pytest, "main", raise_from_pytest)

    with pytest.raises(RuntimeError, match="pytest failed unexpectedly"):
        run_mutation_pytest.run([], repo_root=tmp_path)

    assert Path.cwd() == original_cwd
    assert observed_working_dir is not None
    assert not observed_working_dir.exists()


def test_script_has_a_spawn_safe_main_guard() -> None:
    source = Path(run_mutation_pytest.__file__).read_text(encoding="utf-8")

    assert 'if __name__ == "__main__":' in source
    assert run_mutation_pytest.REPO_ROOT == Path(__file__).resolve().parents[1]
