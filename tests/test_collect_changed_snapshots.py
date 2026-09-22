"""What the Linux snapshot-refresh lane hands back for review.

`scripts/collect_changed_snapshots.py` decides which regenerated baselines reach
the artifact a developer downloads. Both of its failure modes look identical
from outside — an empty artifact — so they are pinned here: a Git query that
failed must not read as "nothing changed", and a spec whose snapshot directory
is wholly new must not collapse into one unusable directory entry.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.collect_changed_snapshots import changed_paths, main, stage

_SNAPSHOTS = "frontend/e2e/canvas-assurance.spec.ts-snapshots"


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _write(repo: Path, relative: str, content: bytes) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repository holding one committed baseline."""
    _git(tmp_path, "init", "-q", ".")
    _write(tmp_path, f"{_SNAPSHOTS}/committed-linux-chromium.png", b"committed")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_a_rerendered_baseline_is_handed_back_with_its_new_bytes(
    repo: Path, tmp_path: Path
) -> None:
    _write(repo, f"{_SNAPSHOTS}/committed-linux-chromium.png", b"rerendered")
    into = tmp_path / "artifact"

    staged = stage(repo, into, changed_paths(repo, ["frontend/e2e"]))

    assert staged == [f"{_SNAPSHOTS}/committed-linux-chromium.png"]
    assert (into / staged[0]).read_bytes() == b"rerendered"


def test_a_spec_whose_snapshots_are_all_new_is_handed_back_file_by_file(
    repo: Path, tmp_path: Path
) -> None:
    """Git's default collapses a wholly untracked directory into one entry.

    A new spec's baselines would then arrive as a single path naming the
    directory, which is not a file to copy and not a diff to review.
    """
    new = "frontend/e2e/added.spec.ts-snapshots"
    _write(repo, f"{new}/one-linux-chromium.png", b"one")
    _write(repo, f"{new}/two-linux-chromium.png", b"two")
    into = tmp_path / "artifact"

    staged = stage(repo, into, changed_paths(repo, ["frontend/e2e"]))

    assert sorted(staged) == [
        f"{new}/one-linux-chromium.png",
        f"{new}/two-linux-chromium.png",
    ]
    assert (into / f"{new}/two-linux-chromium.png").read_bytes() == b"two"


def test_a_baseline_the_render_did_not_move_is_left_out(repo: Path, tmp_path: Path) -> None:
    """The artifact is the diff; an unchanged baseline is not part of it."""
    into = tmp_path / "artifact"

    staged = stage(repo, into, changed_paths(repo, ["frontend/e2e"]))

    assert staged == []
    assert not into.exists() or list(into.rglob("*.png")) == []


def test_changes_outside_the_paths_asked_about_are_not_collected(
    repo: Path, tmp_path: Path
) -> None:
    _write(repo, "frontend/src/App.tsx", b"edited")
    into = tmp_path / "artifact"

    assert stage(repo, into, changed_paths(repo, ["frontend/e2e"])) == []


def test_a_deleted_baseline_is_not_handed_back(repo: Path, tmp_path: Path) -> None:
    """A deletion is a real change, but there is no file to review."""
    (repo / _SNAPSHOTS / "committed-linux-chromium.png").unlink()
    into = tmp_path / "artifact"

    assert stage(repo, into, changed_paths(repo, ["frontend/e2e"])) == []


def test_a_failed_query_is_raised_rather_than_read_as_nothing_changed(
    tmp_path: Path,
) -> None:
    """The quiet failure this script exists to prevent.

    Asking a directory that is not a repository must not answer "no baseline
    changed", which would leave the lane green and the artifact absent.
    """
    with pytest.raises(subprocess.CalledProcessError):
        changed_paths(tmp_path, ["frontend/e2e"])


def test_the_entry_point_terminates_every_path_it_prints(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The workflow counts with `wc -l`, which counts terminators, not paths.

    So the newline after the last path is the contract, not decoration: drop it
    and one changed baseline counts as zero, the upload step is skipped by its
    own condition, and the lane reports success having handed back nothing.
    Compared as one exact string rather than by lines, because every
    line-splitting comparison in Python accepts the unterminated output.
    """
    _write(repo, f"{_SNAPSHOTS}/added-linux-chromium.png", b"added")
    _write(repo, f"{_SNAPSHOTS}/committed-linux-chromium.png", b"rerendered")
    into = tmp_path / "artifact"

    exit_code = main(["--repo", str(repo), "--into", str(into), "--path", "frontend/e2e"])

    assert exit_code == 0
    # Git reports its tracked modifications before its untracked additions, so
    # the re-rendered baseline precedes the new one whatever their names.
    assert capsys.readouterr().out == (
        f"{_SNAPSHOTS}/committed-linux-chromium.png\n{_SNAPSHOTS}/added-linux-chromium.png\n"
    )
