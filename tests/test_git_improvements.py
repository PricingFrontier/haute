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
    set_fork,
    write_pref,
    write_working_branch,
)
from haute.routes._helpers import commit_pipeline_graph

WORKING = "pricing-dev"


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

        first = git_lock.repository_identity(repo)
        second = git_lock.repository_identity(repo)

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
        set_fork(repo, WORKING, "a" * 40)
        record_trash(repo, WORKING, {"branch_tip": "b" * 40})

        assert set(written) == {"state.json", "prefs.json", "forks.json", "trash.json"}

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

        monkeypatch.setattr(git_mod.subprocess, "run", fake_run)
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

        def per_item_probe(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("commit context must batch milestone parent metadata")

        monkeypatch.setattr(git_mod, "_ledger_point", per_item_probe)
        commit_context(repo, save, cwd=repo)

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

        monkeypatch.setattr(git_mod, "fetch_pair", unexpected_fetch)
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

        def fail_parse(_path: Path) -> None:
            raise ValueError("malformed historical pipeline")

        monkeypatch.setattr(route_helpers, "parse_pipeline_to_graph", fail_parse)
        monkeypatch.chdir(repo)
        with pytest.raises(GitHistoryReadError):
            commit_pipeline_graph("HEAD")

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
            git_mod,
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
            git_mod,
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
            git_mod,
            "_HISTORY_ARCHIVE_MAX_MEMBERS",
            2,
            raising=False,
        )
        monkeypatch.setattr(
            git_mod,
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
        import haute.discovery as discovery
        import haute.routes._helpers as route_helpers
        from haute._types import PipelineGraph

        def fake_archive(_sha: str, root: Path, cwd: Path | None = None) -> None:
            del cwd
            (root / "rating.py").write_text("# historical\n")

        monkeypatch.setattr(git_mod, "archive_commit", fake_archive)
        monkeypatch.setattr(
            discovery,
            "discover_pipelines",
            lambda root: [root / "rating.py"],
        )
        monkeypatch.setattr(
            route_helpers,
            "parse_pipeline_to_graph",
            lambda _path: PipelineGraph(),
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

        result = commit_pipeline_graph("HEAD")
        assert result.nodes == []
        assert result.source_file == "rating.py"
        assert attempts == 3
