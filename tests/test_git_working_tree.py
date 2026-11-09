"""Restoring a version and the working-tree fence.

An earlier version is written back as one new save on the current branch with its history recorded,
and every command that rewrites the working tree runs through one fence.
"""

import sys
from pathlib import Path

import pytest

import haute._git_core as git_core
import haute._git_transactions as git_transactions
from haute._git import (
    GitDomainError,
    commit_milestone,
    commit_save,
    create_working_branch,
    restore_version,
    retry_interrupted,
    set_working_branch,
    working_branch_status,
)
from tests import _git_engine_helpers
from tests._git_engine_helpers import (
    LEDGER,
    WORKING,
    _git,
    _parents,
    _tree,
    _write_and_save,
)

# Fixtures shared with the other modules split from one suite.
repo = _git_engine_helpers.repo


class TestRestoreVersion:
    """Restore: an earlier version written back as one new save on the current
    branch, history recorded before the files are written."""

    def _saves(self, repo: Path) -> tuple[str, str]:
        """Adopt WORKING and land two saves; the second changes, deletes and adds a
        file. Return (first_sha, second_sha)."""
        set_working_branch(WORKING, repo, cwd=repo)
        sha1 = _write_and_save(
            repo, WORKING, {"rating.py": "# v1\n", "keep.txt": "keep\n", "gone.txt": "old\n"}
        )
        (repo / "rating.py").write_text("# v2\n")
        (repo / "added.txt").write_text("new\n")
        (repo / "gone.txt").unlink()
        sha2 = commit_save(["rating.py", "added.txt", "gone.txt"], WORKING, cwd=repo)
        assert sha1 and sha2
        assert "gone.txt" not in _git(repo, "ls-tree", "--name-only", sha2)
        return sha1, sha2

    def test_restores_a_save_as_one_new_save_on_the_branch(self, repo: Path) -> None:
        from haute._git_state import read_working_branch

        sha1, sha2 = self._saves(repo)
        working_tip = _git(repo, "rev-parse", WORKING)
        (repo / "notes.txt").write_text("mine\n")  # untracked, outside any sweep
        (repo / ".haute_cache").mkdir()
        result = repo / "outputs" / "result.parquet"
        result.parent.mkdir()
        result.write_text("kept")

        resp = restore_version(sha1, repo, cwd=repo)

        assert resp.restored_from == sha1
        assert resp.working_branch == WORKING
        assert resp.sha == _git(repo, "rev-parse", LEDGER)
        assert resp.short_sha == resp.sha[:8]
        # One new save on the same ledger, the later saves kept beneath it.
        assert _parents(repo, LEDGER) == [sha2]
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert read_working_branch(repo) == WORKING
        assert _git(repo, "rev-parse", WORKING) == working_tip
        assert _tree(repo, LEDGER) == _tree(repo, sha1)
        subject = _git(repo, "log", "-1", "--format=%s", LEDGER)
        original = _git(repo, "log", "-1", "--format=%s", sha1)
        assert subject == f"Restored {sha1[:8]}: {original}"
        assert f"Restored-from: {sha1}" in _git(repo, "log", "-1", "--format=%B", LEDGER)
        # The files are the earlier version's: changed back, deleted ones returned,
        # ones added since removed, and nothing left unrecorded.
        assert (repo / "rating.py").read_text() == "# v1\n"
        assert (repo / "gone.txt").read_text() == "old\n"
        assert not (repo / "added.txt").exists()
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""
        # Untracked files, .haute/ and the analyst's results stay; caches go.
        assert (repo / "notes.txt").read_text() == "mine\n"
        assert (repo / ".haute" / "state.json").exists()
        assert result.read_text() == "kept"
        assert not (repo / ".haute_cache").exists()

    def test_restores_a_milestone_and_a_save_folded_into_one(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v1\n"})
        first = commit_milestone("First", repo, cwd=repo).sha
        folded = _write_and_save(repo, WORKING, {"rating.py": "# v2\n"})
        _write_and_save(repo, WORKING, {"rating.py": "# v3\n"})
        commit_milestone("Second", repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# v4\n"})
        assert folded is not None

        restore_version(first, repo, cwd=repo)
        assert (repo / "rating.py").read_text() == "# v1\n"
        assert _tree(repo, LEDGER) == _tree(repo, first)

        restore_version(folded, repo, cwd=repo)
        assert (repo / "rating.py").read_text() == "# v2\n"
        assert _tree(repo, LEDGER) == _tree(repo, folded)

    def test_records_unrecorded_work_first(self, repo: Path) -> None:
        sha1, sha2 = self._saves(repo)
        (repo / "rating.py").write_text("# edited outside Haute\n")
        new_file = repo / "rating" / "files" / "rates.csv"
        new_file.parent.mkdir(parents=True)
        new_file.write_text("a,b\n")

        restore_version(sha1, repo, cwd=repo, pipeline_folder="rating")

        swept = _parents(repo, LEDGER)[0]
        assert _parents(repo, swept) == [sha2]
        assert _git(repo, "log", "-1", "--format=%s", swept) == "Updated project files"
        assert _git(repo, "show", f"{swept}:rating.py") == "# edited outside Haute"
        assert _git(repo, "show", f"{swept}:rating/files/rates.csv") == "a,b"
        assert not new_file.exists()
        assert (repo / "rating.py").read_text() == "# v1\n"

    def test_current_version_is_refused(self, repo: Path) -> None:
        _, sha2 = self._saves(repo)

        with pytest.raises(GitDomainError, match="already the current version"):
            restore_version(sha2, repo, cwd=repo)

        assert _git(repo, "rev-parse", LEDGER) == sha2

    def test_current_version_with_edits_on_disk_records_and_clears_them(self, repo: Path) -> None:
        _, sha2 = self._saves(repo)
        (repo / "rating.py").write_text("# edited outside Haute\n")

        restore_version(sha2, repo, cwd=repo)

        assert _git(repo, "log", "-1", "--format=%s", f"{LEDGER}~1") == "Updated project files"
        assert (repo / "rating.py").read_text() == "# v2\n"
        assert _tree(repo, LEDGER) == _tree(repo, sha2)

    def test_commit_outside_the_branch_is_refused(self, repo: Path) -> None:
        _, sha2 = self._saves(repo)
        _git(repo, "checkout", "-q", "-b", "elsewhere", "main")
        (repo / "other.txt").write_text("x\n")
        _git(repo, "add", "other.txt")
        _git(repo, "commit", "-q", "-m", "elsewhere")
        elsewhere = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", LEDGER)

        with pytest.raises(GitDomainError, match="this branch's history"):
            restore_version(elsewhere, repo, cwd=repo)

        assert _git(repo, "rev-parse", LEDGER) == sha2

    def test_file_in_the_way_is_refused_by_name(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        sha1 = _write_and_save(repo, WORKING, {"extra.txt": "versioned\n"})
        (repo / "extra.txt").unlink()
        sha2 = commit_save(["extra.txt"], WORKING, cwd=repo)
        assert sha1 and sha2
        (repo / "extra.txt").write_text("local copy\n")

        with pytest.raises(GitDomainError, match="extra.txt"):
            restore_version(sha1, repo, cwd=repo)

        assert (repo / "extra.txt").read_text() == "local copy\n"
        assert _git(repo, "rev-parse", LEDGER) == sha2

    def test_refused_without_identity(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sha1, sha2 = self._saves(repo)
        monkeypatch.setattr(git_transactions, "get_identity", lambda cwd=None: (None, None))

        with pytest.raises(GitDomainError, match="git identity"):
            restore_version(sha1, repo, cwd=repo)

        assert _git(repo, "rev-parse", LEDGER) == sha2

    def test_refused_while_a_git_operation_is_in_progress(self, repo: Path) -> None:
        sha1, _ = self._saves(repo)
        (repo / ".git" / "MERGE_HEAD").write_text(sha1 + "\n")

        with pytest.raises(GitDomainError, match="in progress"):
            restore_version(sha1, repo, cwd=repo)

    def test_refused_without_a_working_branch(self, repo: Path) -> None:
        with pytest.raises(GitDomainError, match="No working branch"):
            restore_version("HEAD", repo, cwd=repo)

    @pytest.mark.parametrize("sha", ["--hard", "0" * 40])
    def test_refuses_a_ref_that_is_not_a_commit(self, repo: Path, sha: str) -> None:
        self._saves(repo)
        with pytest.raises(GitDomainError):
            restore_version(sha, repo, cwd=repo)

    def test_failure_writing_the_files_leaves_the_restore_recorded_and_retry_finishes_it(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_core import GitFilesInUseError
        from haute._git_state import read_interrupted

        sha1, sha2 = self._saves(repo)

        def held_open(*args: str, cwd: Path | None = None) -> tuple[int, str]:
            return 1, "error: unable to unlink old 'rating.py': Invalid argument"

        monkeypatch.setattr(git_transactions, "_run_git_capture", held_open)
        (repo / ".haute_cache").mkdir()

        with pytest.raises(GitFilesInUseError, match="rating.py is open in another program"):
            restore_version(sha1, repo, cwd=repo)

        assert _parents(repo, LEDGER) == [sha2]
        assert _tree(repo, LEDGER) == _tree(repo, sha1)
        assert (repo / ".haute_cache").is_dir()
        record = read_interrupted(repo)
        assert record is not None and "rating.py" in record["files"]  # type: ignore[operator]

        monkeypatch.undo()
        retry_interrupted(repo, cwd=repo)

        assert read_interrupted(repo) is None
        assert (repo / "rating.py").read_text() == "# v1\n"
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""


class TestTreeFence:
    """Every command that rewrites the working tree runs through one fence. On
    Windows git cannot replace a file another program holds open, and a checkout
    still exits 0 with that file left at the old version; the fence finds the file
    out of step, puts HEAD back and refuses by name, or records the clone
    interrupted when it cannot."""

    @staticmethod
    def _two_branches(repo: Path) -> None:
        """``main`` and ``other`` differ in a.txt and b.txt; HEAD on main."""
        _git(repo, "checkout", "-q", "main")
        for name in ("a.txt", "b.txt"):
            (repo / name).write_text(f"{name} on main\n")
        _git(repo, "add", "a.txt", "b.txt")
        _git(repo, "commit", "-q", "-m", "main files")
        _git(repo, "checkout", "-q", "-b", "other")
        for name in ("a.txt", "b.txt"):
            (repo / name).write_text(f"{name} on other\n")
        _git(repo, "commit", "-q", "-am", "other files")
        _git(repo, "checkout", "-q", "main")

    @staticmethod
    def _hold(monkeypatch: pytest.MonkeyPatch, repo: Path, held: str, content: str) -> None:
        """A checkout that exits 0 with *held* left at *content*, as git does on
        Windows for a file another program holds open."""
        real = git_core._run_git_capture

        def checkout_leaving_a_file(*args: str, cwd: Path | None = None) -> tuple[int, str]:
            code, stderr = real(*args, cwd=cwd)
            if args[0] == "checkout":
                (repo / held).write_text(content)
                return 0, f"error: unable to unlink old '{held}': Invalid argument"
            return code, stderr

        monkeypatch.setattr(git_core, "_run_git_capture", checkout_leaving_a_file)

    def test_a_checkout_leaving_a_file_stale_is_put_back_and_refused_by_name(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_core import GitFilesInUseError, _rewrite_tree
        from haute._git_state import read_interrupted

        self._two_branches(repo)
        self._hold(monkeypatch, repo, "b.txt", "b.txt on main\n")

        with pytest.raises(GitFilesInUseError, match="b.txt is open in another program"):
            _rewrite_tree("checkout", "other", cwd=repo)

        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
        assert (repo / "a.txt").read_text() == "a.txt on main\n"
        assert (repo / "b.txt").read_text() == "b.txt on main\n"
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""
        assert read_interrupted(repo) is None

    def test_a_file_held_too_tightly_to_read_is_put_back_by_force(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Excel holds an open CSV with no sharing, so git cannot even read it to
        stage it for the put-back, and a plain checkout back then refuses over it:
        HEAD stayed on the new branch. With nothing changed before the command the
        put-back is forced, and HEAD goes back."""
        from haute._git_core import GitFilesInUseError, _rewrite_tree

        self._two_branches(repo)
        self._hold(monkeypatch, repo, "b.txt", "b.txt on main\n")
        real_ok = git_core._run_git_ok

        def add_unreadable(*args: str, cwd: Path | None = None) -> tuple[bool, str]:
            if args[0] == "add":
                return False, 'error: open("b.txt"): Permission denied'
            return real_ok(*args, cwd=cwd)

        monkeypatch.setattr(git_core, "_run_git_ok", add_unreadable)

        with pytest.raises(GitFilesInUseError, match="b.txt is open in another program"):
            _rewrite_tree("checkout", "other", cwd=repo)

        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
        assert (repo / "a.txt").read_text() == "a.txt on main\n"
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""

    def test_a_file_the_checkout_could_not_remove_is_put_back_and_refused(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The new HEAD drops c.txt but git cannot remove it: it stays behind,
        untracked, where the status comparison never looks. git's own message
        names it."""
        from haute._git_core import GitFilesInUseError, _rewrite_tree

        self._two_branches(repo)
        (repo / "c.txt").write_text("c.txt on main\n")
        _git(repo, "add", "c.txt")
        _git(repo, "commit", "-q", "-m", "c on main")
        real = git_core._run_git_capture

        def checkout_leaving_a_removed_file(*args: str, cwd: Path | None = None) -> tuple[int, str]:
            code, stderr = real(*args, cwd=cwd)
            if args[0] == "checkout":
                (repo / "c.txt").write_text("c.txt on main\n")
                return 0, "warning: unable to unlink 'c.txt': Permission denied"
            return code, stderr

        monkeypatch.setattr(git_core, "_run_git_capture", checkout_leaving_a_removed_file)

        with pytest.raises(GitFilesInUseError, match="c.txt"):
            _rewrite_tree("checkout", "other", cwd=repo)

        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
        assert (repo / "c.txt").read_text() == "c.txt on main\n"
        assert _git(repo, "status", "--porcelain") == ""

    def test_a_forced_checkout_over_a_changed_file_it_could_not_replace_is_refused(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """b.txt was already changed, so a status comparison sees nothing new when
        a forced checkout leaves it as it was; git's own message names it. The
        put-back is forced too, since the changes were to be discarded."""
        from haute._git_core import GitFilesInUseError, _rewrite_tree

        self._two_branches(repo)
        (repo / "b.txt").write_text("edited, not saved\n")
        self._hold(monkeypatch, repo, "b.txt", "edited, not saved\n")

        with pytest.raises(GitFilesInUseError, match="b.txt"):
            _rewrite_tree("checkout", "-f", "other", cwd=repo)

        assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
        assert (repo / "a.txt").read_text() == "a.txt on main\n"
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""

    def test_a_failed_fast_forward_puts_the_files_it_wrote_back(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_core import GitFilesInUseError, _rewrite_tree

        self._two_branches(repo)

        def merge_stopping_part_way(*args: str, cwd: Path | None = None) -> tuple[int, str]:
            (repo / "a.txt").write_text("a.txt on other\n")  # written before b.txt refused
            return 1, "error: unable to unlink old 'b.txt': Invalid argument"

        monkeypatch.setattr(git_core, "_run_git_capture", merge_stopping_part_way)

        with pytest.raises(GitFilesInUseError, match="b.txt"):
            _rewrite_tree("merge", "--ff-only", "other", cwd=repo)

        assert (repo / "a.txt").read_text() == "a.txt on main\n"
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""

    def test_a_put_back_that_cannot_finish_interrupts_the_clone_until_retry(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_core import GitFilesInUseError, _rewrite_tree
        from haute._git_state import read_interrupted

        self._two_branches(repo)
        set_working_branch(WORKING, repo, cwd=repo)
        _git(repo, "checkout", "-q", "main")
        self._hold(monkeypatch, repo, "b.txt", "b.txt on main\n")
        real_ok = git_core._run_git_ok

        def put_back_refused(*args: str, cwd: Path | None = None) -> tuple[bool, str]:
            if args[0] in ("checkout", "restore"):
                return False, ""
            return real_ok(*args, cwd=cwd)

        monkeypatch.setattr(git_core, "_run_git_ok", put_back_refused)

        with pytest.raises(GitFilesInUseError):
            _rewrite_tree("checkout", "other", cwd=repo)

        record = read_interrupted(repo)
        assert record is not None and record["files"] == ["b.txt"]
        status = working_branch_status(repo, cwd=repo)
        assert status.state == "interrupted"
        assert "b.txt" in status.errors[0]
        with pytest.raises(GitDomainError, match="out of step with version history: b.txt"):
            commit_milestone("Not now", repo, cwd=repo)
        with pytest.raises(GitDomainError, match="out of step with version history: b.txt"):
            restore_version("HEAD", repo, cwd=repo)

        monkeypatch.undo()
        retry_interrupted(repo, cwd=repo)

        assert read_interrupted(repo) is None
        assert (repo / "b.txt").read_text() == "b.txt on other\n"

    def test_retry_refuses_while_the_file_is_still_held(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_core import GitFilesInUseError
        from haute._git_state import read_interrupted, record_interrupted

        self._two_branches(repo)
        (repo / "b.txt").write_text("b.txt left behind\n")
        record_interrupted(repo, "checkout", ["b.txt"])
        monkeypatch.setattr(
            git_transactions,
            "_run_git_capture",
            lambda *a, cwd=None: (1, "error: unable to unlink old 'b.txt': Invalid argument"),
        )

        with pytest.raises(GitFilesInUseError, match="b.txt"):
            retry_interrupted(repo, cwd=repo)

        assert read_interrupted(repo) is not None

    def test_a_switch_through_set_working_branch_is_refused_and_left_as_it_was(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_core import GitFilesInUseError
        from haute._git_state import read_working_branch

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# on dev\n"})
        create_working_branch("other", repo, cwd=repo)
        self._hold(monkeypatch, repo, "rating.py", "# on dev\n")

        with pytest.raises(GitFilesInUseError, match="rating.py"):
            set_working_branch("other", repo, cwd=repo)

        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert read_working_branch(repo) == WORKING
        assert (repo / "rating.py").read_text() == "# on dev\n"

    @pytest.mark.skipif(
        sys.platform != "win32", reason="Windows holds open files against replacement"
    )
    def test_a_file_held_open_on_windows_refuses_the_switch(self, repo: Path) -> None:
        from haute._git_core import GitFilesInUseError

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# on dev\n"})
        create_working_branch("other", repo, cwd=repo)

        with open(repo / "rating.py", "rb"), pytest.raises(GitFilesInUseError, match="rating.py"):
            set_working_branch("other", repo, cwd=repo)

        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert (repo / "rating.py").read_text() == "# on dev\n"
