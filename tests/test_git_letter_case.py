"""A save reaches version history after its folder or file is renamed to other letter case.

Windows and macOS keep a folder renamed from ``rating`` to ``Rating`` as the same folder, and
git there, which ``git init`` sets to ignore case, keeps its own spelling: a save naming
``Rating/main.py`` found nothing to record. Where the test folder ignores case (Windows and
macOS, which CI's platform smoke runs this module on) the rename is real; where it keeps case
apart, a link under the new spelling stands for the folder that ignores it.
"""

import os
import subprocess
from pathlib import Path

import pytest

import haute._git_transactions as git_transactions
from haute._git import GitDomainError, commit_save, set_working_branch
from haute.routes._save_pipeline import capture_save_in_ledger

WORKING = "pricing-dev"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _ignores_case(folder: Path) -> bool:
    probe = folder / "case-probe"
    probe.write_text("")
    try:
        return (folder / "CASE-PROBE").exists()
    finally:
        probe.unlink()


def _respell(root: Path, old: str, new: str) -> None:
    """*old* renamed to *new*, other letter case of it, as Explorer or Finder renames it."""
    if _ignores_case(root):
        os.rename(root / old, root / new)
    else:
        os.symlink(Path(old).name, root / new, target_is_directory=(root / old).is_dir())


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test Actuary")
    _git(root, "config", "user.email", "test@example.com")
    (root / ".gitignore").write_text(".haute/\n")
    (root / "rating" / "files" / "data").mkdir(parents=True)
    (root / "rating" / "main.py").write_text("# pipeline\n")
    (root / "rating" / "files" / "data" / "rates.csv").write_text("rate\n1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    set_working_branch(WORKING, root, create=True, cwd=root)
    return root


def _in_ledger(root: Path, path: str) -> str:
    return _git(root, "show", f"{WORKING}-save:{path}")


def _tracked_changes(root: Path) -> str:
    return _git(root, "status", "--porcelain", "--untracked-files=no")


def test_a_save_after_the_pipeline_folder_is_renamed_is_recorded(project: Path) -> None:
    _respell(project, "rating", "Rating")
    (project / "Rating" / "main.py").write_text("# renamed folder\n")

    sha = commit_save(["Rating/main.py"], WORKING, cwd=project)

    assert sha is not None
    assert _in_ledger(project, "rating/main.py") == "# renamed folder"
    assert _tracked_changes(project) == ""


def test_an_upload_into_a_renamed_data_folder_is_recorded(project: Path) -> None:
    _respell(project, "rating/files/data", "rating/files/Data")
    (project / "rating" / "files" / "Data" / "new.csv").write_text("x\n1\n")

    sha = commit_save(["rating/files/Data/new.csv"], WORKING, cwd=project)

    assert sha is not None
    assert _in_ledger(project, "rating/files/data/new.csv") == "x\n1"
    assert _git(project, "diff", "--cached", "--name-only") == ""


def test_a_replace_of_a_renamed_data_file_is_recorded(project: Path) -> None:
    _respell(project, "rating/files/data/rates.csv", "rating/files/data/Rates.csv")
    (project / "rating" / "files" / "data" / "Rates.csv").write_text("rate\n2\n")

    sha = commit_save(["rating/files/data/Rates.csv"], WORKING, cwd=project)

    assert sha is not None
    assert _in_ledger(project, "rating/files/data/rates.csv") == "rate\n2"
    assert _tracked_changes(project) == ""


def test_a_folder_named_in_other_capitals_records_into_the_folder_git_keeps(
    project: Path,
) -> None:
    _respell(project, "rating/files/data", "rating/files/DATA")
    (project / "rating" / "files" / "DATA" / "b.csv").write_text("b\n")

    commit_save(["rating/files/DATA/b.csv"], WORKING, cwd=project)

    assert _in_ledger(project, "rating/files/data/b.csv") == "b"


def test_a_switch_after_a_renamed_folder_s_saves_succeeds(project: Path) -> None:
    from haute._git import create_working_branch

    create_working_branch("other", project, cwd=project)
    _respell(project, "rating", "Rating")
    (project / "Rating" / "main.py").write_text("# renamed folder\n")
    commit_save(["Rating/main.py"], WORKING, cwd=project)

    set_working_branch("other", project, cwd=project)

    assert _git(project, "symbolic-ref", "--short", "HEAD") == "other-save"


def test_a_capture_that_records_nothing_for_a_changed_file_says_so(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The net under any other way a path can miss git's: the file on disk differs from the
    ledger's copy, so the capture warns and keeps the file for the next one."""
    _respell(project, "rating", "Rating")
    (project / "Rating" / "main.py").write_text("# renamed folder\n")
    monkeypatch.setattr(git_transactions, "_git_spelling", lambda paths, _cwd: paths)

    with pytest.raises(GitDomainError) as refused:
        commit_save(["Rating/main.py"], WORKING, cwd=project)
    warnings: list[str] = []
    captured = capture_save_in_ledger(project, ["Rating/main.py"], warnings)

    assert str(refused.value) == (
        "Version history did not take the change to Rating/main.py; the next save tries again."
    )
    assert captured.git_sha is None and captured.uncaptured == ["Rating/main.py"]
    assert warnings == [f"Changes saved; version capture failed: {refused.value}"]


def test_files_whose_names_differ_only_in_case_stay_two_where_the_disk_keeps_them_apart(
    project: Path,
) -> None:
    if _ignores_case(project):
        pytest.skip("this disk cannot hold two names that differ only in case")
    (project / "rating" / "Main.py").write_text("# a second file\n")

    commit_save(["rating/Main.py"], WORKING, cwd=project)

    assert _in_ledger(project, "rating/Main.py") == "# a second file"
    assert _in_ledger(project, "rating/main.py") == "# pipeline"
