"""Focused regressions for the Git-integration roadmap improvements."""

from __future__ import annotations

import gc
import io
import subprocess
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import haute._git as git_mod
import haute._git_core as git_core
import haute._git_history as git_history
import haute._git_remote as git_remote
from haute._git import (
    GitDomainError,
    GitHistoryReadError,
    _replay_onto,
    archive_commit,
    commit_context,
    commit_save,
    list_remotes,
    set_identity,
    set_working_branch,
    working_branch_status,
)
from haute._git_state import (
    read_prefs,
    record_trash,
    write_pref,
    write_working_branch,
)
from haute.routes._helpers import commit_pipeline_graph

WORKING = "pricing-dev"
# CreateProcess takes at most this many characters of command line, its null included.
_WINDOWS_COMMAND_LINE_LIMIT = 32_767


def _record_command_lines(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The length of every command line launched from now on, as Windows would receive it."""
    lengths: list[int] = []
    real = subprocess.Popen

    class Recording(real):  # type: ignore[valid-type, misc]
        def __init__(self, args: list[str], *rest: object, **kwargs: object) -> None:
            lengths.append(len(subprocess.list2cmdline(args)))
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    return lengths


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    (root / "rating.py").write_text("# pipeline\n")
    _git(root, "add", "rating.py")
    _git(root, "commit", "-m", "initial")
    _git(root, "checkout", "-b", WORKING)
    return root


class TestRepositoryMutationLock:
    def test_repository_identity_reuses_cached_filesystem_lookup(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import haute._git_lock as git_lock

        real_find_git_dir = git_lock._find_git_dir
        calls = 0

        def counted_find_git_dir(path: Path) -> Path | None:
            nonlocal calls
            calls += 1
            return real_find_git_dir(path)

        monkeypatch.setattr(git_lock, "_find_git_dir", counted_find_git_dir)

        first = git_lock._repository_keys(repo)
        second = git_lock._repository_keys(repo)

        assert first == second
        assert calls == 1

    def test_repository_lock_identity_stays_stable_across_git_init(
        self,
        tmp_path: Path,
    ) -> None:
        from haute._git_lock import repository_mutation

        root = tmp_path / "new-project"
        root.mkdir()
        completed = threading.Event()

        def mutate() -> None:
            with repository_mutation(root):
                completed.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with repository_mutation(root):
                _git(root, "init", "-b", "main")
                future = pool.submit(mutate)
                assert not completed.wait(timeout=0.1)
            future.result(timeout=5)

        assert completed.is_set()

    def test_linked_worktrees_share_the_common_repository_lock(
        self,
        repo: Path,
        tmp_path: Path,
    ) -> None:
        from haute._git_lock import repository_mutation

        linked = tmp_path / "linked-worktree"
        _git(repo, "worktree", "add", "-b", "linked-work", str(linked))
        completed = threading.Event()

        def mutate() -> None:
            with repository_mutation(linked):
                completed.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with repository_mutation(repo):
                future = pool.submit(mutate)
                assert not completed.wait(timeout=0.1)
            future.result(timeout=5)

        assert completed.is_set()

    def test_idle_repository_lock_is_evicted(self, tmp_path: Path) -> None:
        import haute._git_lock as git_lock

        root = tmp_path / "ephemeral-project"
        root.mkdir()
        before = set(git_lock._repository_locks)

        with git_lock.repository_mutation(root):
            created = set(git_lock._repository_locks) - before
            assert created

        gc.collect()
        assert created.isdisjoint(git_lock._repository_locks)

    def test_git_mutator_waits_for_the_same_repository_lock(self, repo: Path) -> None:
        from haute._git_lock import repository_mutation

        completed = threading.Event()

        def mutate() -> None:
            set_identity("Other User", "other@example.com", cwd=repo)
            completed.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with repository_mutation(repo):
                future = pool.submit(mutate)
                assert not completed.wait(timeout=0.1)
            future.result(timeout=5)

        assert completed.is_set()

    def test_state_read_modify_write_uses_the_repository_lock(self, repo: Path) -> None:
        from haute._git_lock import repository_mutation

        completed = threading.Event()

        def mutate() -> None:
            write_pref(repo, "skipSwitchConfirm", True)
            completed.set()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with repository_mutation(repo):
                future = pool.submit(mutate)
                assert not completed.wait(timeout=0.1)
            future.result(timeout=5)

        assert completed.is_set()

    def test_concurrent_real_saves_keep_both_successful_commits(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        (repo / "a.py").write_text("a = 1\n")
        (repo / "b.py").write_text("b = 1\n")

        with ThreadPoolExecutor(max_workers=2) as pool:
            saves = [
                pool.submit(commit_save, ["a.py"], WORKING, cwd=repo, message="save a"),
                pool.submit(commit_save, ["b.py"], WORKING, cwd=repo, message="save b"),
            ]
            shas = [save.result(timeout=10) for save in saves]

        assert all(shas)
        messages = _git(repo, "log", "-2", "--format=%s").splitlines()
        assert set(messages) == {"save a", "save b"}
        git_dir = Path(_git(repo, "rev-parse", "--git-dir"))
        if not git_dir.is_absolute():
            git_dir = repo / git_dir
        assert not (git_dir / "index.lock").exists()


class TestAtomicCloneState:
    def test_every_state_document_uses_atomic_replace(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import haute._git_state as git_state

        real_atomic_write = git_state.atomic_write_text
        written: list[str] = []

        def tracking_write(path: Path, data: str, encoding: str = "utf-8") -> None:
            written.append(path.name)
            real_atomic_write(path, data, encoding)

        monkeypatch.setattr(git_state, "atomic_write_text", tracking_write)
        write_working_branch(repo, WORKING)
        write_pref(repo, "skipSwitchConfirm", True)
        record_trash(repo, WORKING, {"branch_tip": "b" * 40})

        assert set(written) == {"state.json", "prefs.json", "trash.json"}

    def test_concurrent_preference_updates_preserve_both_keys(self, repo: Path) -> None:
        barrier = threading.Barrier(2)

        def update(key: str) -> None:
            barrier.wait()
            write_pref(repo, key, True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(update, key) for key in ("first", "second")]
            for future in futures:
                future.result(timeout=5)

        assert read_prefs(repo) == {"first": True, "second": True}


class TestRepositoryReadiness:
    def test_missing_repository_is_a_successful_distinct_state(self, tmp_path: Path) -> None:
        root = tmp_path / "not-a-repository"
        root.mkdir()

        status = working_branch_status(root, cwd=root)

        assert status.state == "no-repository"
        assert status.working_branch is None
        assert status.current_branch == ""
        assert status.head_sha is None

    def test_detached_head_is_not_reported_as_branch_head(self, repo: Path) -> None:
        sha = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "--detach", sha)

        status = working_branch_status(repo, cwd=repo)

        assert status.state == "detached"
        assert status.current_branch == ""
        assert status.head_sha == sha


class TestGitOutputIntegrity:
    def test_git_subprocesses_pin_a_stable_locale(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[dict[str, object]] = []

        def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(git_core.subprocess, "run", fake_run)
        git_mod._run_git_ok("status", cwd=repo)

        env = calls[0]["env"]
        assert isinstance(env, dict)
        assert env["LC_ALL"] == "C"
        assert env["LANG"] == "C"
        assert env["LANGUAGE"] == "C"

    def test_commit_context_preserves_tabbed_subject(self, repo: Path) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        message = "rate update\twith audit marker"
        _git(repo, "commit", "--allow-empty", "-m", message)
        sha = _git(repo, "rev-parse", "HEAD")

        assert commit_context(repo, sha, cwd=repo).message == message

    def test_commit_context_does_not_probe_each_milestone_fold_point(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        (repo / "rating.py").write_text("# save\n")
        save = commit_save(["rating.py"], WORKING, cwd=repo, message="pending")
        assert save is not None

        real_rev_parse = git_history._rev_parse

        def no_fold_point_probe(ref: str, cwd: Path | None = None) -> str | None:
            if ref.endswith("^2"):
                raise AssertionError("commit context must batch milestone parent metadata")
            return real_rev_parse(ref, cwd=cwd)

        monkeypatch.setattr(git_history, "_rev_parse", no_fold_point_probe)
        commit_context(repo, save, cwd=repo)

    def test_commit_context_reads_a_thousand_milestones_within_a_command_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every milestone SHA went into one rev-list argv, so on Windows a branch with
        about 790 milestones failed with WinError 206 and the breadcrumb answered 500."""
        project = tmp_path / "long"
        project.mkdir()
        _git(project, "init", "-q", "-b", "main")
        stream = [
            "blob\nmark :1\ndata 11\n# pipeline\n\n",
            "commit refs/heads/main\nmark :2\ncommitter T <t@e.com> 1700000000 +0000\n"
            "data 4\ninit\nM 100644 :1 rating.py\n\n",
        ]
        previous_save = previous_milestone = 2
        for number in range(1, 1001):
            save, milestone = 2 * number + 1, 2 * number + 2
            stream.append(
                f"commit refs/heads/{WORKING}-save\nmark :{save}\n"
                f"committer T <t@e.com> {1700000000 + 2 * number} +0000\n"
                f"data 7\nsave {number % 10}\nfrom :{previous_save}\n\n"
            )
            stream.append(
                f"commit refs/heads/{WORKING}\nmark :{milestone}\n"
                f"committer T <t@e.com> {1700000001 + 2 * number} +0000\n"
                f"data 12\nmilestone {number % 10}\n"
                f"from :{previous_milestone}\nmerge :{save}\n\n"
            )
            previous_save, previous_milestone = save, milestone
        stream.append(
            f"commit refs/heads/{WORKING}-save\nmark :3000\n"
            "committer T <t@e.com> 1800000000 +0000\n"
            f"data 7\npending\nfrom :{previous_save}\n\n"
        )
        subprocess.run(
            ["git", "fast-import", "--quiet"],
            cwd=project,
            input="".join(stream),
            text=True,
            check=True,
        )
        write_working_branch(project, WORKING)
        pending = _git(project, "rev-parse", f"{WORKING}-save")
        lengths = _record_command_lines(monkeypatch)

        context = commit_context(project, pending, cwd=project)

        assert context.nearest_milestone.sha == _git(project, "rev-parse", WORKING)
        assert context.distance == 1
        assert max(lengths) < _WINDOWS_COMMAND_LINE_LIMIT

    def test_replay_refuses_to_linearise_a_merge(self, repo: Path) -> None:
        base = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-b", "side")
        (repo / "side.py").write_text("side = True\n")
        _git(repo, "add", "side.py")
        _git(repo, "commit", "-m", "side")
        _git(repo, "checkout", WORKING)
        (repo / "mainline.py").write_text("mainline = True\n")
        _git(repo, "add", "mainline.py")
        _git(repo, "commit", "-m", "mainline")
        _git(repo, "merge", "--no-ff", "side", "-m", "external merge")
        merge = _git(repo, "rev-parse", "HEAD")

        with pytest.raises(GitDomainError, match="merge"):
            _replay_onto(base, [merge], cwd=repo)


class TestRoutineRemoteReads:
    def test_list_remotes_never_fetches(
        self,
        repo: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        set_working_branch(WORKING, repo, cwd=repo)
        bare = tmp_path / "origin.git"
        _git(repo, "init", "--bare", str(bare))
        _git(repo, "remote", "add", "origin", str(bare))

        def unexpected_fetch(*_args: object, **_kwargs: object) -> bool:
            raise AssertionError("routine remote listing must not fetch")

        monkeypatch.setattr(git_remote, "_fetch_refs", unexpected_fetch)
        response = list_remotes(repo, cwd=repo)

        assert [remote.name for remote in response.remotes] == ["origin"]


class TestHistoricalPipelineReads:
    @staticmethod
    def _tar_payload(entries: list[tuple[str, bytes]]) -> bytes:
        payload = io.BytesIO()
        with tarfile.TarFile(fileobj=payload, mode="w") as archive:
            for name, content in entries:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        return payload.getvalue()

    def test_archive_excludes_unneeded_large_data(self, repo: Path, tmp_path: Path) -> None:
        (repo / "haute.toml").write_text('[project]\npipeline = "rating.py"\n')
        (repo / "rating.py").write_text('import haute\n\npipeline = haute.Pipeline("rating")\n')
        (repo / "rating.haute.json").write_text("{}\n")
        (repo / "config").mkdir()
        (repo / "config" / "node.json").write_text("{}\n")
        (repo / "data").mkdir()
        (repo / "data" / "large.bin").write_bytes(b"x" * 1_000_000)
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "historical pipeline")

        dest = tmp_path / "archive"
        dest.mkdir()
        archive_commit("HEAD", dest, cwd=repo)

        assert (dest / "haute.toml").exists()
        assert (dest / "rating.py").exists()
        assert (dest / "rating.haute.json").exists()
        assert (dest / "config" / "node.json").exists()
        assert not (dest / "data").exists()

    def test_archive_extracts_a_commit_whose_paths_pass_a_command_line(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every selected path went into one git archive argv, so on Windows a commit
        with more than about 32K characters of pipeline paths could not be shown."""
        names = [
            f"config/banding/segment_banding_configuration_for_quote_book_{index:04d}.json"
            for index in range(600)
        ]
        assert sum(len(name) for name in names) > 40_000
        for name in names:
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(f'{{"index": "{name}"}}\n')
        _git(repo, "add", "config")
        _git(repo, "commit", "-q", "-m", "many configurations")
        dest = tmp_path / "archive"
        dest.mkdir()
        lengths = _record_command_lines(monkeypatch)

        archive_commit("HEAD", dest, cwd=repo)

        assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*.json")) == names
        assert max(lengths) < _WINDOWS_COMMAND_LINE_LIMIT

    def test_archive_limits_count_every_batch(
        self, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The member ceiling holds across the batches a large commit is archived in."""
        names = [f"config/item_{index:03d}.json" for index in range(40)]
        for name in names:
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text("{}\n")
        _git(repo, "add", "config")
        _git(repo, "commit", "-q", "-m", "configurations")
        monkeypatch.setattr(git_history, "_ARCHIVE_COMMAND_BUDGET", 200)
        monkeypatch.setattr(git_history, "_HISTORY_ARCHIVE_MAX_MEMBERS", 30)
        dest = tmp_path / "archive"
        dest.mkdir()

        with pytest.raises(GitHistoryReadError, match="too many archived files"):
            archive_commit("HEAD", dest, cwd=repo)
        assert list(dest.iterdir()) == []

    def test_all_parse_failures_are_typed_instead_of_empty_success(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import haute.routes._helpers as route_helpers

        (repo / "haute.toml").write_text('[project]\npipeline = "rating.py"\n')
        (repo / "rating.py").write_text('import haute\n\npipeline = haute.Pipeline("rating")\n')
        _git(repo, "add", "haute.toml", "rating.py")
        _git(repo, "commit", "-m", "broken historical pipeline")

        def fail_parse(_path: Path, **_kwargs: object) -> None:
            raise ValueError("malformed historical pipeline")

        monkeypatch.setattr(route_helpers, "parse_pipeline_to_graph", fail_parse)
        monkeypatch.chdir(repo)
        with pytest.raises(GitHistoryReadError, match="could not be parsed"):
            commit_pipeline_graph("HEAD", "rating.py")

    def test_an_unreadable_history_is_an_error_not_an_empty_branch(self, repo: Path) -> None:
        """A first-parent walk git cannot finish returned an empty, successful list, so a
        damaged repository read as a branch with no milestones and no saves."""
        set_working_branch(WORKING, repo, cwd=repo)
        for version in ("v2", "v3"):
            (repo / "rating.py").write_text(f"# {version}\n")
            assert commit_save(["rating.py"], WORKING, cwd=repo, message=version)
            git_mod.commit_milestone(version, repo, cwd=repo)
        (repo / "rating.py").write_text("# v4\n")
        assert commit_save(["rating.py"], WORKING, cwd=repo, message="v4")
        git_core._clear_content_caches()
        # The milestone before the tip, and the save before the pending one.
        for missing in (f"{WORKING}~1", f"{WORKING}-save~1"):
            sha = _git(repo, "rev-parse", missing)
            (repo / ".git" / "objects" / sha[:2] / sha[2:]).unlink()

        with pytest.raises(GitHistoryReadError, match="history could not be read"):
            git_mod.working_milestones(repo, cwd=repo)
        with pytest.raises(GitHistoryReadError, match="history could not be read"):
            git_mod.pending_ledger_saves(repo, cwd=repo)

    def test_malformed_archive_is_a_typed_history_failure(
        self,
        tmp_path: Path,
    ) -> None:
        dest = tmp_path / "malformed"
        dest.mkdir()

        with pytest.raises(GitHistoryReadError, match="could not be extracted"):
            git_mod._extract_history_tar(b"not a tar archive", dest)

    def test_archive_rejects_too_many_members_before_writing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            git_history,
            "_HISTORY_ARCHIVE_MAX_MEMBERS",
            2,
            raising=False,
        )
        payload = self._tar_payload([("first.py", b""), ("second.py", b""), ("third.py", b"")])
        dest = tmp_path / "too-many-members"
        dest.mkdir()

        with pytest.raises(GitHistoryReadError, match="too many archived files"):
            git_mod._extract_history_tar(payload, dest)

        assert list(dest.iterdir()) == []

    def test_archive_rejects_too_many_bytes_before_writing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            git_history,
            "_HISTORY_ARCHIVE_MAX_BYTES",
            4,
            raising=False,
        )
        payload = self._tar_payload([("rating.py", b"12345")])
        dest = tmp_path / "too-many-bytes"
        dest.mkdir()

        with pytest.raises(GitHistoryReadError, match="too large to extract safely"):
            git_mod._extract_history_tar(payload, dest)

        assert list(dest.iterdir()) == []

    def test_archive_accepts_member_and_byte_limits_exactly(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            git_history,
            "_HISTORY_ARCHIVE_MAX_MEMBERS",
            2,
            raising=False,
        )
        monkeypatch.setattr(
            git_history,
            "_HISTORY_ARCHIVE_MAX_BYTES",
            4,
            raising=False,
        )
        payload = self._tar_payload([("first.py", b"12"), ("second.py", b"34")])
        dest = tmp_path / "at-limits"
        dest.mkdir()

        git_mod._extract_history_tar(payload, dest)

        assert (dest / "first.py").read_bytes() == b"12"
        assert (dest / "second.py").read_bytes() == b"34"

    def test_temporary_history_cleanup_retries_windows_contention(
        self,
        repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import haute.routes._helpers as route_helpers
        from haute._types import PipelineGraph

        def fake_archive(_sha: str, root: Path, cwd: Path | None = None) -> None:
            del cwd
            (root / "rating.py").write_text("# historical\n")

        monkeypatch.setattr(git_mod, "archive_commit", fake_archive)
        monkeypatch.setattr(
            route_helpers,
            "parse_pipeline_to_graph",
            lambda _path, **_kwargs: PipelineGraph(source_file="rating.py"),
        )

        real_rmtree = route_helpers.shutil.rmtree
        attempts = 0

        def contended_rmtree(path: Path) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise PermissionError("injected sharing violation")
            real_rmtree(path)

        monkeypatch.setattr(route_helpers.shutil, "rmtree", contended_rmtree)
        monkeypatch.chdir(repo)

        result = commit_pipeline_graph("HEAD", "rating.py")
        assert result.nodes == []
        assert result.source_file == "rating.py"
        assert attempts == 3
