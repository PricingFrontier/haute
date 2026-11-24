"""Restoring a version and the working-tree fence.

An earlier version is written back as one new save on the current branch with its history recorded,
and every command that rewrites the working tree runs through one fence.
"""

import sys
from pathlib import Path

import pytest

import haute._git_core as git_core
import haute._git_operation as git_operation
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
        result = repo / "files" / "outputs" / "result.parquet"
        result.parent.mkdir(parents=True)
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
        # A restore that completes leaves no mark.
        assert not (repo / ".haute" / "operation.json").exists()
        assert working_branch_status(repo, cwd=repo).state == "ready"

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
        from haute._git_state import read_operation

        sha1, sha2 = self._saves(repo)

        def held_open(*args: str, cwd: Path | None = None, **_: object) -> tuple[int, str]:
            return 1, "error: unable to unlink old 'rating.py': Invalid argument"

        monkeypatch.setattr(git_operation, "_run_git_capture", held_open)
        (repo / ".haute_cache").mkdir()

        with pytest.raises(GitFilesInUseError, match="rating.py is open in another program"):
            restore_version(sha1, repo, cwd=repo)

        # Past the commit point: history keeps the restore, and the record stays.
        assert _parents(repo, LEDGER) == [sha2]
        assert _tree(repo, LEDGER) == _tree(repo, sha1)
        assert (repo / ".haute_cache").is_dir()
        record = read_operation(repo)
        assert record is not None and record.operation == "restore"
        assert set(record.files) == {"rating.py", "added.txt", "gone.txt"}
        status = working_branch_status(repo, cwd=repo)
        assert status.state == "interrupted"
        assert status.errors == [
            "Restoring a version stopped part-way: added.txt, gone.txt, rating.py."
        ]

        monkeypatch.undo()
        retry_interrupted(repo, cwd=repo)

        assert read_operation(repo) is None
        assert (repo / "rating.py").read_text() == "# v1\n"
        assert (repo / "gone.txt").read_text() == "old\n"
        assert not (repo / "added.txt").exists()
        assert not (repo / ".haute_cache").exists()
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""


class TestTreeFence:
    """Every command that rewrites the working tree runs through one fence. On
    Windows git cannot replace a file another program holds open, and a checkout
    still exits 0 with that file left at the old version; the fence finds the file
    out of step and refuses by name. It puts nothing back itself: the operation it
    is a step of puts everything back from its record, and keeps the record when it
    cannot."""

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

    @classmethod
    def _hold_unreadable(
        cls, monkeypatch: pytest.MonkeyPatch, repo: Path, held: str, content: str
    ) -> None:
        """As :meth:`_hold`, and from the checkout on git cannot read *held* either, so
        it reads as changed whatever it holds, as an Excel-held CSV does."""
        cls._hold(monkeypatch, repo, held, content)
        held_since: list[bool] = []
        checkout = git_core._run_git_capture

        def checkout_then_unreadable(*args: str, cwd: Path | None = None) -> tuple[int, str]:
            if args[0] == "checkout":
                held_since.append(True)
            return checkout(*args, cwd=cwd)

        monkeypatch.setattr(git_core, "_run_git_capture", checkout_then_unreadable)
        changes = git_operation._tracked_changes
        monkeypatch.setattr(
            git_operation,
            "_tracked_changes",
            lambda cwd=None: changes(cwd) | ({held} if held_since else set()),
        )

    @staticmethod
    def _fence(*args: str, repo: Path) -> None:
        from haute._git_core import _ref_moves_permitted, _rewrite_tree

        with _ref_moves_permitted():
            _rewrite_tree(*args, cwd=repo)

    def test_a_checkout_leaving_a_file_stale_is_refused_by_name(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_core import GitFilesInUseError

        self._two_branches(repo)
        self._hold(monkeypatch, repo, "b.txt", "b.txt on main\n")

        with pytest.raises(GitFilesInUseError, match="b.txt is open in another program"):
            self._fence("checkout", "other", repo=repo)

    def test_a_file_the_checkout_could_not_remove_is_refused_by_name(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The new HEAD drops c.txt but git cannot remove it: it stays behind,
        untracked, where the status comparison never looks. git's own message
        names it."""
        from haute._git_core import GitFilesInUseError

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
            self._fence("checkout", "other", repo=repo)

    def test_a_forced_checkout_over_a_changed_file_it_could_not_replace_is_refused(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """b.txt was already changed, so a status comparison sees nothing new when
        a forced checkout leaves it as it was; git's own message names it."""
        from haute._git_core import GitFilesInUseError

        self._two_branches(repo)
        (repo / "b.txt").write_text("edited, not saved\n")
        self._hold(monkeypatch, repo, "b.txt", "edited, not saved\n")

        with pytest.raises(GitFilesInUseError, match="b.txt"):
            self._fence("checkout", "-f", "other", repo=repo)

    def test_a_fast_forward_stopping_part_way_is_refused_by_name(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_core import GitFilesInUseError

        self._two_branches(repo)

        def merge_stopping_part_way(*args: str, cwd: Path | None = None) -> tuple[int, str]:
            (repo / "a.txt").write_text("a.txt on other\n")  # written before b.txt refused
            return 1, "error: unable to unlink old 'b.txt': Invalid argument"

        monkeypatch.setattr(git_core, "_run_git_capture", merge_stopping_part_way)

        with pytest.raises(GitFilesInUseError, match="b.txt"):
            self._fence("merge", "--ff-only", "other", repo=repo)

    def test_a_switch_through_set_working_branch_is_refused_and_left_as_it_was(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_core import GitFilesInUseError
        from haute._git_state import read_operation, read_working_branch

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# on dev\n"})
        create_working_branch("other", repo, cwd=repo)
        self._hold(monkeypatch, repo, "rating.py", "# on dev\n")

        with pytest.raises(GitFilesInUseError, match="rating.py"):
            set_working_branch("other", repo, cwd=repo)

        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert read_working_branch(repo) == WORKING
        assert (repo / "rating.py").read_text() == "# on dev\n"
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""
        assert read_operation(repo) is None

    def test_a_file_held_too_tightly_to_check_leaves_the_clone_interrupted_until_retry(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Excel holds an open CSV so tightly git cannot read it: the put-back cannot
        write it nor check it, so the record stays. Readiness names the operation and
        the file, every other git action refuses, and Retry puts the switch back once
        the file is closed."""
        from haute._git_core import GitFilesInUseError
        from haute._git_state import read_operation, read_working_branch

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# on dev\n"})
        create_working_branch("other", repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# on dev, saved again\n"})
        self._hold_unreadable(monkeypatch, repo, "rating.py", "# on dev, saved again\n")

        with pytest.raises(GitFilesInUseError, match="rating.py"):
            set_working_branch("other", repo, cwd=repo)

        record = read_operation(repo)
        assert record is not None and record.operation == "select-branch"
        status = working_branch_status(repo, cwd=repo)
        assert status.state == "interrupted"
        assert status.errors == ["Choosing a branch stopped part-way: rating.py."]
        refusal = "Choosing a branch stopped part-way. Choose Retry first."
        with pytest.raises(GitDomainError, match=refusal):
            commit_milestone("Not now", repo, cwd=repo)
        with pytest.raises(GitDomainError, match=refusal):
            restore_version("HEAD", repo, cwd=repo)
        with pytest.raises(GitDomainError, match=refusal):
            commit_save(["rating.py"], WORKING, cwd=repo)

        monkeypatch.undo()
        retry_interrupted(repo, cwd=repo)

        assert read_operation(repo) is None
        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert read_working_branch(repo) == WORKING
        assert (repo / "rating.py").read_text() == "# on dev, saved again\n"
        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""

    def test_retry_refuses_while_the_file_is_still_held(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute._git_core import GitFilesInUseError
        from haute._git_state import read_operation

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# on dev\n"})
        create_working_branch("other", repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# on dev, saved again\n"})
        self._hold_unreadable(monkeypatch, repo, "rating.py", "# on dev, saved again\n")
        with pytest.raises(GitFilesInUseError):
            set_working_branch("other", repo, cwd=repo)

        with pytest.raises(GitFilesInUseError, match="rating.py"):
            retry_interrupted(repo, cwd=repo)

        assert read_operation(repo) is not None

    @pytest.mark.skipif(
        sys.platform != "win32", reason="Windows holds open files against replacement"
    )
    def test_a_file_held_open_on_windows_refuses_the_switch(self, repo: Path) -> None:
        from haute._git_core import GitFilesInUseError
        from haute._git_state import read_operation

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# on dev\n"})
        create_working_branch("other", repo, cwd=repo)

        with open(repo / "rating.py", "rb"), pytest.raises(GitFilesInUseError, match="rating.py"):
            set_working_branch("other", repo, cwd=repo)

        assert _git(repo, "symbolic-ref", "--short", "HEAD") == LEDGER
        assert (repo / "rating.py").read_text() == "# on dev\n"
        assert read_operation(repo) is None


class TestFilesChangedOutsideHaute:
    """A file edited by hand (a CSV saved in Excel) or copied in where the destination has
    one: every operation that replaces the project's files names it and is asked again once
    it is recorded as a save or put back (``GitUnrecordedFilesError``, ``settle_unrecorded``).
    """

    def _arrange(self, repo: Path, tmp_path: Path, operation: str) -> None:
        """The pair on WORKING with one save; *other* holds another rating.py; for the remote
        operations a teammate has pushed a save the clone is behind on."""
        from haute._git import push_working_pair

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"rating.py": "# on dev\n"})
        if operation in ("switch", "archive"):
            create_working_branch("other", repo, cwd=repo)
        if operation in ("catch-up", "spin-off"):
            bare = tmp_path / "origin.git"
            _git(repo, "init", "--bare", "-q", str(bare))
            _git(repo, "remote", "add", "origin", str(bare))
            push_working_pair("origin", repo, cwd=repo)
            teammate = tmp_path / "teammate"
            _git(repo, "clone", "-q", str(bare), str(teammate))
            _git(teammate, "config", "user.name", "Teammate")
            _git(teammate, "config", "user.email", "teammate@example.com")
            _git(teammate, "checkout", "-q", LEDGER)
            (teammate / "rating.py").write_text("# teammate's\n")
            _git(teammate, "commit", "-q", "-am", "teammate's save")
            if operation == "spin-off":
                # The clone saved too: the two lines have forked.
                _write_and_save(repo, WORKING, {"notes.txt": "local\n"})
            _git(teammate, "push", "-q", "origin", LEDGER)

    def _run(self, repo: Path, operation: str) -> object:
        from haute._git import archive_working_pair, branch_away, fast_forward_pair

        if operation == "switch":
            return set_working_branch("other", repo, cwd=repo)
        if operation == "create-and-open":
            return create_working_branch("opened", repo, switch=True, cwd=repo)
        if operation == "archive":
            return archive_working_pair(WORKING, repo, cwd=repo)
        if operation == "catch-up":
            return fast_forward_pair("origin", repo, cwd=repo)
        return branch_away("origin", repo, cwd=repo)

    def _settle(self, repo: Path, operation: str, choice: str) -> None:
        """Answer the refusal the operation meets as the editor does: its files and token."""
        from haute._git import GitUnrecordedFilesError, settle_unrecorded

        with pytest.raises(GitUnrecordedFilesError) as refused:
            self._run(repo, operation)
        settle_unrecorded(
            choice,  # type: ignore[arg-type]
            refused.value.files,
            refused.value.token,
            repo,
            cwd=repo,
        )

    _OPERATIONS = ["switch", "create-and-open", "archive", "catch-up", "spin-off"]
    _WORDS = {
        "switch": "switching to 'other'",
        "create-and-open": "opening 'opened'",
        "archive": f"archiving '{WORKING}'",
        "catch-up": "catching up",
        "spin-off": "spinning off a copy",
    }

    @pytest.mark.parametrize("operation", _OPERATIONS)
    def test_a_file_edited_by_hand_is_named_and_nothing_moves(
        self, repo: Path, tmp_path: Path, operation: str
    ) -> None:
        from haute._git import GitUnrecordedFilesError

        self._arrange(repo, tmp_path, operation)
        (repo / "rating.py").write_text("# edited in another program\n")
        refs = _git(repo, "for-each-ref", "refs/heads/")
        head = _git(repo, "symbolic-ref", "HEAD")

        with pytest.raises(GitUnrecordedFilesError) as refused:
            self._run(repo, operation)

        assert refused.value.files == ["rating.py"]
        assert str(refused.value) == (
            "rating.py has changed on disk outside Haute since the last save. Before "
            f"{self._WORDS[operation]}, record it as a save on this branch, or put it back as "
            "version history has it."
        )
        assert "Save or discard" not in str(refused.value)
        assert _git(repo, "for-each-ref", "refs/heads/") == refs
        assert _git(repo, "symbolic-ref", "HEAD") == head
        assert (repo / "rating.py").read_text() == "# edited in another program\n"

    @pytest.mark.parametrize("operation", _OPERATIONS)
    def test_put_back_the_operation_completes_with_the_file_as_history_has_it(
        self, repo: Path, tmp_path: Path, operation: str
    ) -> None:
        self._arrange(repo, tmp_path, operation)
        (repo / "rating.py").write_text("# edited in another program\n")

        self._settle(repo, operation, "put-back")
        assert (repo / "rating.py").read_text() == "# on dev\n"
        self._run(repo, operation)

        assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""

    @pytest.mark.parametrize("operation", ["switch", "create-and-open", "archive", "spin-off"])
    def test_recorded_the_operation_completes_and_the_edit_is_among_the_branch_s_saves(
        self, repo: Path, tmp_path: Path, operation: str
    ) -> None:
        self._arrange(repo, tmp_path, operation)
        (repo / "rating.py").write_text("# edited in another program\n")

        self._settle(repo, operation, "record")
        recorded = _git(repo, "rev-parse", LEDGER)
        result = self._run(repo, operation)

        assert _git(repo, "log", "-1", "--format=%s", recorded) == "Updated project files"
        assert _git(repo, "show", f"{recorded}:rating.py") == "# edited in another program"
        # The save stays with the branch it was made on, set aside or archived with it.
        kept = {
            "switch": LEDGER,
            "create-and-open": LEDGER,
            "archive": f"archive/{LEDGER}",
            "spin-off": f"{getattr(result, 'set_aside_as', '')}-save",
        }[operation]
        assert _git(repo, "rev-parse", kept) == recorded

    def test_recorded_before_a_catch_up_the_save_asks_for_a_copy_to_be_spun_off(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """A save the remote does not have makes the lines fork: Catch up never merges, so
        it refuses as for any local save, and Spin off a copy keeps the edit."""
        self._arrange(repo, tmp_path, "catch-up")
        (repo / "rating.py").write_text("# edited in another program\n")

        self._settle(repo, "catch-up", "record")
        with pytest.raises(GitDomainError, match="Spin off a copy"):
            self._run(repo, "catch-up")

    def test_a_file_git_does_not_track_where_the_destination_has_one_is_named(
        self, repo: Path
    ) -> None:
        from haute._git import GitUnrecordedFilesError, settle_unrecorded

        set_working_branch(WORKING, repo, cwd=repo)
        create_working_branch("other", repo, cwd=repo)
        set_working_branch("other", repo, cwd=repo)
        _write_and_save(repo, "other", {"rates.csv": "rate\n1\n"})
        set_working_branch(WORKING, repo, cwd=repo)
        (repo / "rates.csv").write_text("copied in by hand\n")

        with pytest.raises(GitUnrecordedFilesError) as refused:
            set_working_branch("other", repo, cwd=repo)
        assert refused.value.files == ["rates.csv"]

        settle_unrecorded("put-back", ["rates.csv"], refused.value.token, repo, cwd=repo)
        assert not (repo / "rates.csv").exists()
        set_working_branch("other", repo, cwd=repo)
        assert (repo / "rates.csv").read_text() == "rate\n1\n"

    def test_recording_needs_a_working_branch_and_an_identity(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute._git_transactions as transactions
        from haute._git import settle_unrecorded
        from haute._git_core import _unrecorded_token

        (repo / "rating.py").write_text("# edited\n")
        token = _unrecorded_token(["rating.py"], repo)
        with pytest.raises(GitDomainError, match="No working branch is chosen"):
            settle_unrecorded("record", ["rating.py"], token, repo, cwd=repo)
        set_working_branch(WORKING, repo, cwd=repo)
        token = _unrecorded_token(["rating.py"], repo)
        monkeypatch.setattr(transactions, "get_identity", lambda _cwd=None: (None, None))
        with pytest.raises(GitDomainError, match="needs a git identity"):
            settle_unrecorded("record", ["rating.py"], token, repo, cwd=repo)

    def test_an_answer_given_after_the_files_changed_again_touches_nothing(
        self, repo: Path
    ) -> None:
        """Asked about an edit, then another tab recorded it, switched branch and edited the
        same file there: the first tab's Put back must not discard the other branch's edit."""
        from haute._git import GitUnrecordedFilesError, settle_unrecorded

        set_working_branch(WORKING, repo, cwd=repo)
        create_working_branch("other", repo, cwd=repo)
        (repo / "rating.py").write_text("# edited by hand\n")
        with pytest.raises(GitUnrecordedFilesError) as refused:
            set_working_branch("other", repo, cwd=repo)
        # Meanwhile, another tab: Record, the switch, and an edit on 'other'.
        settle_unrecorded("record", refused.value.files, refused.value.token, repo, cwd=repo)
        set_working_branch("other", repo, cwd=repo)
        (repo / "rating.py").write_text("# the other branch's own edit\n")

        with pytest.raises(GitDomainError, match="changed since you were asked about it"):
            settle_unrecorded("put-back", refused.value.files, refused.value.token, repo, cwd=repo)

        assert (repo / "rating.py").read_text() == "# the other branch's own edit\n"

    @pytest.mark.parametrize("choice", ["record", "put-back"])
    def test_a_rename_staged_by_hand_is_named_by_both_its_names_and_settled_whole(
        self, repo: Path, choice: str
    ) -> None:
        from haute._git import GitUnrecordedFilesError, settle_unrecorded

        set_working_branch(WORKING, repo, cwd=repo)
        _write_and_save(repo, WORKING, {"old.csv": "a\n"})
        create_working_branch("other", repo, cwd=repo)
        _git(repo, "mv", "old.csv", "new.csv")

        with pytest.raises(GitUnrecordedFilesError) as refused:
            set_working_branch("other", repo, cwd=repo)
        assert refused.value.files == ["new.csv", "old.csv"]
        settle_unrecorded(choice, refused.value.files, refused.value.token, repo, cwd=repo)  # type: ignore[arg-type]
        set_working_branch("other", repo, cwd=repo)

        held = _git(repo, "ls-tree", "-r", "--name-only", LEDGER).splitlines()
        if choice == "record":
            assert "new.csv" in held and "old.csv" not in held
        else:
            assert "old.csv" in held and not (repo / "new.csv").exists()
