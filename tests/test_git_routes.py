"""Tests for git panel API endpoints (routes/git.py).

Uses real git repos in tmp_path for integration testing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException

from tests._git_helpers import git_run as _git
from tests._git_helpers import init_repo as _init_repo

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test runs in an isolated git repo."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    return repo


# ---------------------------------------------------------------------------
# POST /api/git/archive
# ---------------------------------------------------------------------------


class TestGitArchive:
    def test_archives(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/old")
        _git(tmp_path, "checkout", "main")

        res = client.post("/api/git/archive", json={"branch": "pricing/test-user/old"})
        assert res.status_code == 200
        assert res.json()["archived_as"].startswith("archive/")

    def test_blocked_on_protected(self, client: TestClient) -> None:
        res = client.post("/api/git/archive", json={"branch": "main"})
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/git/branches
# ---------------------------------------------------------------------------


class TestGitDeleteBranch:
    def test_deletes(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/old")
        _git(tmp_path, "checkout", "main")

        res = client.request(
            "DELETE", "/api/git/branches", json={"branch": "pricing/test-user/old"}
        )
        assert res.status_code == 200
        branches = _git(tmp_path, "branch")
        assert "old" not in branches

    def test_blocked_on_protected(self, client: TestClient) -> None:
        res = client.request("DELETE", "/api/git/branches", json={"branch": "main"})
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/git/undelete — trash-preserving delete + restore roundtrip
# ---------------------------------------------------------------------------


class TestGitUndelete:
    """Delete pins the pair under ``refs/haute/trash/`` + a ``.haute/trash.json``
    tombstone; undelete rebuilds the pair exactly and consumes both."""

    _BRANCH = "pricing/test-user/dev"
    _FORK = "pricing/test-user/feat"

    def _seed_fork_with_history(self, tmp_path: Path) -> None:
        """A fork with real history: dev → milestone → fork feat AT the
        milestone → a milestone on feat →
        one PENDING save (so an unconfirmed delete refuses) → back on dev."""
        from haute._git import (
            commit_milestone,
            commit_save,
            create_working_branch,
            set_working_branch,
        )

        set_working_branch(self._BRANCH, tmp_path, create=True, cwd=tmp_path)
        (tmp_path / "f.txt").write_text("one\n")
        commit_save(["f.txt"], self._BRANCH, cwd=tmp_path)
        ms = commit_milestone("Base", tmp_path, cwd=tmp_path).sha
        create_working_branch(self._FORK, tmp_path, at=ms, cwd=tmp_path)
        set_working_branch(self._FORK, tmp_path, cwd=tmp_path)
        (tmp_path / "f.txt").write_text("two\n")
        commit_save(["f.txt"], self._FORK, cwd=tmp_path)
        commit_milestone("Fork work", tmp_path, cwd=tmp_path)
        (tmp_path / "f.txt").write_text("three\n")
        commit_save(["f.txt"], self._FORK, cwd=tmp_path)  # left pending
        set_working_branch(self._BRANCH, tmp_path, cwd=tmp_path)

    def test_delete_then_undelete_roundtrip(self, client: TestClient, tmp_path: Path) -> None:
        from haute._git_state import read_trash

        self._seed_fork_with_history(tmp_path)
        ledger = f"{self._FORK}-save"
        branch_tip = _git(tmp_path, "rev-parse", self._FORK)
        ledger_tip = _git(tmp_path, "rev-parse", ledger)

        refused = client.request("DELETE", "/api/git/branches", json={"branch": self._FORK})
        assert refused.status_code == 403  # the pending save still gates deletion
        res = client.request(
            "DELETE", "/api/git/branches", json={"branch": self._FORK, "confirm": True}
        )
        assert res.status_code == 200

        # Branch refs gone; trash pins + tombstone preserve the pair.
        assert self._FORK not in _git(tmp_path, "branch")
        assert _git(tmp_path, "rev-parse", f"refs/haute/trash/{self._FORK}") == branch_tip
        assert _git(tmp_path, "rev-parse", f"refs/haute/trash/{ledger}") == ledger_tip
        tombstone = read_trash(tmp_path)[self._FORK]
        assert tombstone["branch_tip"] == branch_tip
        assert tombstone["ledger_tip"] == ledger_tip
        assert tombstone["was_archived"] is False
        assert tombstone["deleted_at"]

        res = client.post("/api/git/undelete", json={"branch": self._FORK})
        assert res.status_code == 200
        assert res.json() == {"status": "restored", "branch": self._FORK}
        # Tips identical to before the delete; trash consumed.
        assert _git(tmp_path, "rev-parse", self._FORK) == branch_tip
        assert _git(tmp_path, "rev-parse", ledger) == ledger_tip
        assert read_trash(tmp_path) == {}
        assert _git(tmp_path, "rev-parse", "--verify", f"refs/haute/trash/{self._FORK}") == ""
        assert _git(tmp_path, "rev-parse", "--verify", f"refs/haute/trash/{ledger}") == ""

    def test_undelete_unknown_name_is_a_domain_error(self, client: TestClient) -> None:
        res = client.post("/api/git/undelete", json={"branch": "pricing/test-user/ghost"})
        assert res.status_code == 400
        assert (
            res.json()["detail"] == "No deleted branch named 'pricing/test-user/ghost' to restore."
        )

    def test_undelete_refuses_when_name_reoccupied(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._seed_fork_with_history(tmp_path)
        res = client.request(
            "DELETE", "/api/git/branches", json={"branch": self._FORK, "confirm": True}
        )
        assert res.status_code == 200
        _git(tmp_path, "branch", self._FORK)  # a NEW branch reclaims the name
        res = client.post("/api/git/undelete", json={"branch": self._FORK})
        assert res.status_code == 400
        assert "already exists" in res.json()["detail"]

    def test_archived_pair_roundtrip_restores_archived_state(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        from haute._git import (
            archive_working_pair,
            commit_milestone,
            commit_save,
            set_working_branch,
        )
        from haute._git_state import read_trash

        set_working_branch(self._BRANCH, tmp_path, create=True, cwd=tmp_path)
        (tmp_path / "f.txt").write_text("one\n")
        commit_save(["f.txt"], self._BRANCH, cwd=tmp_path)
        commit_milestone("Base", tmp_path, cwd=tmp_path)
        archived = archive_working_pair(self._BRANCH, tmp_path, cwd=tmp_path).archived_as
        tip = _git(tmp_path, "rev-parse", archived)
        ledger_tip = _git(tmp_path, "rev-parse", f"{archived}-save")

        res = client.request(
            "DELETE", "/api/git/branches", json={"branch": archived, "confirm": True}
        )
        assert res.status_code == 200
        assert read_trash(tmp_path)[archived]["was_archived"] is True

        res = client.post("/api/git/undelete", json={"branch": archived})
        assert res.status_code == 200
        assert _git(tmp_path, "rev-parse", archived) == tip
        assert _git(tmp_path, "rev-parse", f"{archived}-save") == ledger_tip
        # Archived state IS the name prefix — the branch manager sees it again.
        listing = client.get("/api/git/working-branches").json()
        row = next(b for b in listing["branches"] if b["name"] == archived)
        assert row["is_archived"] is True

    def test_trash_tombstones_cap_at_newest_twenty(self, tmp_path: Path) -> None:
        from haute._git_state import read_trash, record_trash

        for i in range(25):
            record_trash(tmp_path, f"pricing/test-user/b{i:02d}", {"branch_tip": "x"})
        trash = read_trash(tmp_path)
        assert len(trash) == 20
        assert "pricing/test-user/b04" not in trash  # oldest five dropped
        assert "pricing/test-user/b05" in trash
        assert "pricing/test-user/b24" in trash


# ---------------------------------------------------------------------------
# POST /api/git/working-branches — fork a new working branch (P5d)
# ---------------------------------------------------------------------------


class TestGitCreateWorkingBranch:
    def _adopt(self, client: TestClient) -> None:
        res = client.post(
            "/api/git/working-branch",
            json={"branch": "pricing/test-user/dev", "create": True},
        )
        assert res.status_code == 200

    def test_creates_parallel_line(self, client: TestClient, tmp_path: Path) -> None:
        self._adopt(client)
        res = client.post("/api/git/working-branches", json={"name": "pricing/test-user/feature"})
        assert res.status_code == 200
        body = res.json()
        assert body["switched"] is False and body["moved"] is False
        assert "pricing/test-user/feature" in _git(tmp_path, "branch")

    def test_duplicate_name_rejected(self, client: TestClient) -> None:
        self._adopt(client)
        client.post("/api/git/working-branches", json={"name": "pricing/test-user/x"})
        res = client.post("/api/git/working-branches", json={"name": "pricing/test-user/x"})
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# GET/POST /api/git/prefs — per-clone UI preferences (P5d)
# ---------------------------------------------------------------------------


class TestGitPrefs:
    def test_defaults_to_false(self, client: TestClient) -> None:
        res = client.get("/api/git/prefs")
        assert res.status_code == 200
        assert res.json()["skip_switch_confirm"] is False

    def test_set_and_get(self, client: TestClient) -> None:
        res = client.post("/api/git/prefs", json={"skip_switch_confirm": True})
        assert res.status_code == 200 and res.json()["skip_switch_confirm"] is True
        assert client.get("/api/git/prefs").json()["skip_switch_confirm"] is True


# ---------------------------------------------------------------------------
# B16: Handlers must be sync (not async) so FastAPI threads them
# ---------------------------------------------------------------------------


class TestHandlersAreSync:
    """All git route handlers must be plain ``def`` so FastAPI runs them in
    ``run_in_threadpool``, avoiding event-loop blocking on slow git ops."""

    def test_all_handlers_are_sync(self) -> None:
        import asyncio
        import inspect

        from haute.routes.git import router

        for route in router.routes:
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None:
                continue
            assert not asyncio.iscoroutinefunction(endpoint), (
                f"{endpoint.__name__} should be def, not async def"
            )
            assert not inspect.isawaitable(endpoint), f"{endpoint.__name__} should not be awaitable"


# ---------------------------------------------------------------------------
# E1: Non-GitError exceptions return 500 with safe (non-leaking) message
# ---------------------------------------------------------------------------


class TestGeneralExceptionHandling:
    """Non-GitError exceptions must return 500 with a safe detail message
    (not the raw exception string) and log the actual error."""

    _SAFE_DETAIL = "Operation failed. Check the server logs for details."

    # Map of (endpoint_function_to_patch, request_method, path, body)
    _ENDPOINTS: list[tuple[str, str, str, dict | None]] = [
        ("archive_working_pair", "POST", "/api/git/archive", {"branch": "x"}),
        ("delete_working_pair", "DELETE", "/api/git/branches", {"branch": "x"}),
        ("push_working_pair", "POST", "/api/git/push", {"remote": "origin"}),
        ("list_remotes", "GET", "/api/git/remotes", None),
    ]

    @pytest.mark.parametrize(
        "git_func,method,path,body",
        _ENDPOINTS,
        ids=[e[0] for e in _ENDPOINTS],
    )
    def test_non_git_error_returns_500_safe_message(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        git_func: str,
        method: str,
        path: str,
        body: dict | None,
    ) -> None:
        """Patch the underlying _git function to raise a RuntimeError and
        verify the route returns 500 with a safe detail message."""
        import haute.routes.git as git_routes

        monkeypatch.setattr(
            git_routes,
            git_func,
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("disk on fire")),
        )

        if method == "GET":
            res = client.get(path)
        elif method == "POST":
            res = client.post(path, json=body or {})
        elif method == "DELETE":
            res = client.request("DELETE", path, json=body or {})
        else:
            raise AssertionError(f"Unknown method {method}")

        assert res.status_code == 500
        detail = res.json()["detail"]
        assert detail == self._SAFE_DETAIL
        # Must NOT leak the raw exception message
        assert "disk on fire" not in detail


# ---------------------------------------------------------------------------
# E6: _handle_git_error logs warnings
# ---------------------------------------------------------------------------


class TestHandleGitErrorLogging:
    """_handle_git_error should log warnings for GitError and GitGuardrailError."""

    def test_logs_git_error(self) -> None:
        from unittest.mock import patch

        from haute._git import GitError
        from haute.routes.git import _handle_git_error

        with patch("haute.routes.git.logger") as mock_logger:
            with pytest.raises(HTTPException) as exc_info:
                _handle_git_error(GitError("something broke"))
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert call_args[0][0] == "git_error"
        assert exc_info.value.status_code == 400

    def test_logs_guardrail_error(self) -> None:
        from unittest.mock import patch

        from haute._git import GitGuardrailError
        from haute.routes.git import _handle_git_error

        with patch("haute.routes.git.logger") as mock_logger:
            with pytest.raises(HTTPException) as exc_info:
                _handle_git_error(GitGuardrailError("not allowed"))
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert call_args[0][0] == "git_guardrail_error"
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/git/show/{sha} — read-only view of a commit's pipeline (S11)
# ---------------------------------------------------------------------------


class TestGitShow:
    _V1 = (
        "import polars as pl\n"
        "import haute\n\n"
        'pipeline = haute.Pipeline("hist", description="")\n\n\n'
        "@pipeline.polars\n"
        "def base() -> pl.DataFrame:\n"
        '    return pl.DataFrame({"x": [1]})\n'
    )
    _V2 = _V1 + (
        "\n\n@pipeline.polars\ndef doubled(base: pl.DataFrame) -> pl.DataFrame:\n    return base\n"
    )

    def test_shows_the_pipeline_as_it_was_at_a_past_commit(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        (tmp_path / "pipeline.py").write_text(self._V1)
        _git(tmp_path, "add", "pipeline.py")
        _git(tmp_path, "commit", "-m", "v1")
        sha1 = _git(tmp_path, "rev-parse", "HEAD")
        # Advance: v2 adds a second node. The past commit must still show only v1.
        (tmp_path / "pipeline.py").write_text(self._V2)
        _git(tmp_path, "add", "pipeline.py")
        _git(tmp_path, "commit", "-m", "v2")

        res = client.get(f"/api/git/show/{sha1}")
        assert res.status_code == 200
        labels = {n["data"]["label"] for n in res.json()["nodes"]}
        assert labels == {"base"}

    def test_unknown_commit_returns_400(self, client: TestClient) -> None:
        res = client.get(f"/api/git/show/{'0' * 40}")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/git/move — move to a historical commit (detached checkout, §3.4)
# ---------------------------------------------------------------------------


class TestGitMove:
    def _adopt(self, client: TestClient) -> None:
        res = client.post(
            "/api/git/working-branch",
            json={"branch": "pricing/test-user/dev", "create": True},
        )
        assert res.status_code == 200

    def _commit(self, tmp_path: Path, content: str) -> str:
        (tmp_path / "rating.py").write_text(content)
        _git(tmp_path, "add", "rating.py")
        _git(tmp_path, "commit", "-m", "save")
        return _git(tmp_path, "rev-parse", "HEAD")

    def test_move_detaches_and_clears_working_branch(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._adopt(client)
        sha1 = self._commit(tmp_path, "# v1\n")
        self._commit(tmp_path, "# v2\n")

        res = client.post("/api/git/move", json={"sha": sha1})
        assert res.status_code == 200
        body = res.json()
        assert body["sha"] == sha1
        assert body["short_sha"] == sha1[:8]
        assert body["is_detached"] is True
        assert body["prior_branch"] == "pricing/test-user/dev-save"
        # HEAD detached at the old commit; working tree restored to that version.
        assert _git(tmp_path, "rev-parse", "HEAD") == sha1
        assert (tmp_path / "rating.py").read_text() == "# v1\n"
        # Working branch cleared → the next save re-prompts (S13).
        status = client.get("/api/git/working-branch").json()
        assert status["working_branch"] is None

    def test_move_refuses_dirty_tree_returns_400(self, client: TestClient, tmp_path: Path) -> None:
        self._adopt(client)
        sha1 = self._commit(tmp_path, "# v1\n")
        self._commit(tmp_path, "# v2\n")
        (tmp_path / "rating.py").write_text("# uncommitted edit\n")

        res = client.post("/api/git/move", json={"sha": sha1})
        assert res.status_code == 400
        assert "unsaved changes" in res.json()["detail"]

    def test_move_unknown_sha_returns_400(self, client: TestClient) -> None:
        self._adopt(client)
        res = client.post("/api/git/move", json={"sha": "0" * 40})
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/git/remotes + POST /api/git/push (deliberate push, S16/S33)
# ---------------------------------------------------------------------------


class TestGitRemotesAndPush:
    def _adopt(self, client: TestClient) -> None:
        res = client.post(
            "/api/git/working-branch",
            json={"branch": "pricing/test-user/dev", "create": True},
        )
        assert res.status_code == 200

    def _add_bare_remote(self, tmp_path: Path) -> Path:
        bare = tmp_path / "origin.git"
        _git(tmp_path, "init", "--bare", str(bare))
        _git(tmp_path, "remote", "add", "origin", str(bare))
        return bare

    def test_remotes_empty_when_offline(self, client: TestClient) -> None:
        self._adopt(client)
        res = client.get("/api/git/remotes")
        assert res.status_code == 200
        assert res.json()["remotes"] == []

    def test_remotes_lists_configured_remote(self, client: TestClient, tmp_path: Path) -> None:
        self._adopt(client)
        self._add_bare_remote(tmp_path)
        res = client.get("/api/git/remotes")
        assert res.status_code == 200
        assert [r["name"] for r in res.json()["remotes"]] == ["origin"]

    def test_push_pushes_the_pair(self, client: TestClient, tmp_path: Path) -> None:
        self._adopt(client)
        self._add_bare_remote(tmp_path)
        res = client.post("/api/git/push", json={"remote": "origin"})
        assert res.status_code == 200
        body = res.json()
        assert body["remote"] == "origin"
        assert body["default_branch"] == "main"
        assert body["bootstrapped_default"] is True
        assert body["pushed_refs"][0] == "main"
        assert "pricing/test-user/dev" in body["pushed_refs"]
        assert "refs/heads/pricing/test-user/dev" in _git(tmp_path, "ls-remote", "origin")

        established = client.post("/api/git/push", json={"remote": "origin"})
        assert established.status_code == 200
        established_body = established.json()
        assert established_body["default_branch"] == "main"
        assert established_body["bootstrapped_default"] is False
        assert established_body["pushed_refs"] == [
            "pricing/test-user/dev",
            "pricing/test-user/dev-save",
        ]

    def test_push_sanitizes_raw_git_error_detail(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import haute.routes.git as git_routes
        from haute._git import GitError
        from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

        raw_detail = "authentication failed for https://token-secret@example.test/repo.git"
        monkeypatch.setattr(
            git_routes,
            "push_working_pair",
            lambda *args, **kwargs: (_ for _ in ()).throw(GitError(raw_detail)),
        )

        response = client.post("/api/git/push", json={"remote": "origin"})

        assert response.status_code == 400
        assert response.json()["detail"] == _INTERNAL_ERROR_DETAIL
        assert "token-secret" not in response.text

    def test_push_to_unknown_remote_returns_400(self, client: TestClient, tmp_path: Path) -> None:
        self._adopt(client)
        self._add_bare_remote(tmp_path)
        res = client.post("/api/git/push", json={"remote": "nope"})
        assert res.status_code == 400

    def test_push_without_working_branch_returns_400(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        # No working branch adopted for this clone — a deliberate push has nothing
        # to send, and the domain error surfaces as a 400.
        self._add_bare_remote(tmp_path)
        res = client.post("/api/git/push", json={"remote": "origin"})
        assert res.status_code == 400

    def test_fast_forward_route_catches_up_to_remote(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        # D1: the route fast-forwards the working pair to the remote's tips.
        from haute._git import commit_milestone, commit_save, push_working_pair, resolve_ledger
        from haute._git_state import write_working_branch

        self._adopt(client)
        bare = self._add_bare_remote(tmp_path)
        assert client.post("/api/git/push", json={"remote": "origin"}).status_code == 200

        other = tmp_path / "other"
        _git(tmp_path, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", "pricing/test-user/dev")
        write_working_branch(other, "pricing/test-user/dev")
        resolve_ledger("pricing/test-user/dev", cwd=other)
        (other / "r.txt").write_text("teammate\n")
        commit_save(["r.txt"], "pricing/test-user/dev", cwd=other)
        commit_milestone("teammate", other, cwd=other)
        push_working_pair("origin", other, cwd=other)

        res = client.post("/api/git/fast-forward", json={"remote": "origin"})
        assert res.status_code == 200
        assert set(res.json()["fast_forwarded"]) == {
            "pricing/test-user/dev",
            "pricing/test-user/dev-save",
        }

    def test_fast_forward_route_refuses_when_already_synced(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._adopt(client)
        self._add_bare_remote(tmp_path)
        assert client.post("/api/git/push", json={"remote": "origin"}).status_code == 200
        res = client.post("/api/git/fast-forward", json={"remote": "origin"})
        assert res.status_code == 400  # "Already up to date"

    def test_branch_away_route_sets_aside_and_adopts(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        # M3: a forked push routes here — the local pair is set aside, the canonical
        # name adopts the remote.
        from haute._git import commit_milestone, commit_save, push_working_pair, resolve_ledger
        from haute._git_state import write_working_branch

        self._adopt(client)
        bare = self._add_bare_remote(tmp_path)
        assert client.post("/api/git/push", json={"remote": "origin"}).status_code == 200

        other = tmp_path / "other"
        _git(tmp_path, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", "pricing/test-user/dev")
        write_working_branch(other, "pricing/test-user/dev")
        resolve_ledger("pricing/test-user/dev", cwd=other)
        (other / "r.txt").write_text("remote\n")
        commit_save(["r.txt"], "pricing/test-user/dev", cwd=other)
        commit_milestone("remote m", other, cwd=other)
        push_working_pair("origin", other, cwd=other)

        (tmp_path / "local.py").write_text("x\n")
        commit_save(["local.py"], "pricing/test-user/dev", cwd=tmp_path)
        commit_milestone("local m", tmp_path, cwd=tmp_path, allow_fork=True)

        res = client.post("/api/git/branch-away", json={"remote": "origin"})
        assert res.status_code == 200
        body = res.json()
        assert body["working_branch"] == "pricing/test-user/dev"
        assert body["set_aside_as"].startswith("pricing/test-user/dev-local-")

    def test_non_ff_push_returns_409_with_structured_rejection(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        # M7: a non-fast-forward rejection is a 409 carrying the per-leg fork data,
        # not a generic 400 string the UI can only print.
        from haute._git import commit_milestone, commit_save

        self._adopt(client)
        bare = self._add_bare_remote(tmp_path)
        assert client.post("/api/git/push", json={"remote": "origin"}).status_code == 200

        # A teammate diverges the working branch on the remote.
        other = tmp_path / "other"
        _git(tmp_path, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", "pricing/test-user/dev")
        (other / "f.txt").write_text("remote\n")
        _git(other, "add", "f.txt")
        _git(other, "commit", "-m", "remote change")
        _git(other, "push", "origin", "pricing/test-user/dev")

        # Advance locally on a different line (save → milestone) so we diverge.
        (tmp_path / "g.txt").write_text("local\n")
        commit_save(["g.txt"], "pricing/test-user/dev", cwd=tmp_path)
        commit_milestone("local milestone", tmp_path, cwd=tmp_path)

        res = client.post("/api/git/push", json={"remote": "origin"})
        assert res.status_code == 409
        body = res.json()["detail"]
        assert body["status"] == "rejected_diverged"
        assert body["remote"] == "origin"
        assert body["working"]["status"] == "diverged"
        assert "never force-pushes" in body["message"]


# ---------------------------------------------------------------------------
# _handle_git_error HTTP status codes
# ---------------------------------------------------------------------------


class TestHandleGitErrorStatusCodes:
    """_handle_git_error must return 400 for GitError and 403 for GitGuardrailError."""

    def test_git_error_raises_400(self) -> None:
        """Phase 1C #11: ``GitError`` messages may contain raw git stderr
        (absolute paths, remote URLs, SSL errors, credentials) so they
        are no longer echoed to the HTTP body.  The handler returns a
        400 with the sanitized ``_INTERNAL_ERROR_DETAIL`` constant; the
        full exception text stays in the structured log.
        """
        from haute._git import GitError
        from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
        from haute.routes.git import _handle_git_error

        with pytest.raises(HTTPException) as exc_info:
            _handle_git_error(GitError("bad ref"))
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == _INTERNAL_ERROR_DETAIL

    def test_guardrail_error_raises_403(self) -> None:
        """Guardrail errors are hand-written, user-facing, and preserved
        verbatim (they describe intentional blocks rather than internal
        failures).
        """
        from haute._git import GitGuardrailError
        from haute.routes.git import _handle_git_error

        with pytest.raises(HTTPException) as exc_info:
            _handle_git_error(GitGuardrailError("protected branch"))
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "protected branch"


# ---------------------------------------------------------------------------
# GitError (non-guardrail) through endpoints
# ---------------------------------------------------------------------------


class TestGitErrorEndpointResponses:
    """Endpoints that receive a GitError (non-guardrail) must return 400."""

    _ENDPOINTS_FOR_GIT_ERROR: list[tuple[str, str, str, dict | None]] = [
        ("archive_working_pair", "POST", "/api/git/archive", {"branch": "x"}),
        ("delete_working_pair", "DELETE", "/api/git/branches", {"branch": "x"}),
    ]

    @pytest.mark.parametrize(
        "git_func,method,path,body",
        _ENDPOINTS_FOR_GIT_ERROR,
        ids=[e[0] for e in _ENDPOINTS_FOR_GIT_ERROR],
    )
    def test_git_error_returns_400(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        git_func: str,
        method: str,
        path: str,
        body: dict | None,
    ) -> None:
        """Patch the underlying _git function to raise a GitError and verify 400."""
        import haute.routes.git as git_routes
        from haute._git import GitError

        monkeypatch.setattr(
            git_routes,
            git_func,
            lambda *a, **kw: (_ for _ in ()).throw(GitError("invalid operation")),
        )

        if method == "GET":
            res = client.get(path)
        elif method == "POST":
            res = client.post(path, json=body or {})
        elif method == "DELETE":
            res = client.request("DELETE", path, json=body or {})
        else:
            raise AssertionError(f"Unknown method {method}")

        # Phase 1C #11: raw GitError detail is sanitized to a constant
        # before reaching the HTTP body.  Full detail is logged.
        from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

        assert res.status_code == 400
        assert res.json()["detail"] == _INTERNAL_ERROR_DETAIL

    @pytest.mark.parametrize(
        "git_func,method,path,body",
        _ENDPOINTS_FOR_GIT_ERROR,
        ids=[f"{e[0]}_guardrail" for e in _ENDPOINTS_FOR_GIT_ERROR],
    )
    def test_guardrail_error_returns_403(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        git_func: str,
        method: str,
        path: str,
        body: dict | None,
    ) -> None:
        """Patch the underlying _git function to raise a GitGuardrailError and verify 403."""
        import haute.routes.git as git_routes
        from haute._git import GitGuardrailError

        monkeypatch.setattr(
            git_routes,
            git_func,
            lambda *a, **kw: (_ for _ in ()).throw(GitGuardrailError("not allowed")),
        )

        if method == "GET":
            res = client.get(path)
        elif method == "POST":
            res = client.post(path, json=body or {})
        elif method == "DELETE":
            res = client.request("DELETE", path, json=body or {})
        else:
            raise AssertionError(f"Unknown method {method}")

        assert res.status_code == 403
        assert res.json()["detail"] == "not allowed"


# ---------------------------------------------------------------------------
# GET/POST /api/git/working-branch and POST /api/git/identity (P2)
# ---------------------------------------------------------------------------


class TestWorkingBranchRoutes:
    def test_status_unset(self, client: TestClient) -> None:
        res = client.get("/api/git/working-branch")
        assert res.status_code == 200
        body = res.json()
        assert body["state"] == "unset"
        assert body["working_branch"] is None
        assert "main" not in body["eligible_branches"]

    def test_set_and_read_back(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "branch", "pricing-dev")
        res = client.post("/api/git/working-branch", json={"branch": "pricing-dev"})
        assert res.status_code == 200
        assert res.json()["working_branch"] == "pricing-dev"
        assert res.json()["state"] == "ready"

        status = client.get("/api/git/working-branch")
        assert status.json()["state"] == "ready"
        assert status.json()["working_branch"] == "pricing-dev"
        # HEAD moved onto the ledger
        assert status.json()["current_branch"] == "pricing-dev-save"

    def test_set_create_new(self, client: TestClient) -> None:
        res = client.post("/api/git/working-branch", json={"branch": "fresh", "create": True})
        assert res.status_code == 200
        assert res.json()["working_branch"] == "fresh"

    def test_set_protected_refused_403(self, client: TestClient) -> None:
        res = client.post("/api/git/working-branch", json={"branch": "main"})
        assert res.status_code == 403

    def test_set_missing_branch_400(self, client: TestClient) -> None:
        res = client.post("/api/git/working-branch", json={"branch": "ghost"})
        assert res.status_code == 400


class TestWorkingBranchSeedGuards:
    def test_create_on_foreign_unborn_repo_keeps_env_out_of_history(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Through the real route: a bare `git init` repo (no haute scaffold,
        no .gitignore) with a planted .env — the seeded root commit must not
        publish the secret into git history."""
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        _git(foreign, "init", "-b", "main")
        _git(foreign, "config", "user.name", "Test User")
        _git(foreign, "config", "user.email", "test@example.com")
        (foreign / ".env").write_text("TOKEN=hunter2\n")
        (foreign / "main.py").write_text("x = 1\n")
        monkeypatch.chdir(foreign)

        res = client.post("/api/git/working-branch", json={"branch": "fresh", "create": True})
        assert res.status_code == 200
        assert res.json()["state"] == "ready"

        committed = _git(foreign, "show", "--name-only", "--format=", "main").splitlines()
        assert "main.py" in committed
        assert ".env" not in committed, f".env leaked into history: {committed}"
        # The asserted guards were captured, so clones inherit them.
        assert ".gitignore" in committed


class TestIdentityRoute:
    def test_set_identity_local(self, client: TestClient, tmp_path: Path) -> None:
        res = client.post(
            "/api/git/identity",
            json={"user_name": "Jane Doe", "user_email": "jane@example.com"},
        )
        assert res.status_code == 200
        assert res.json()["scope"] == "local"
        assert _git(tmp_path, "config", "--local", "user.name") == "Jane Doe"

    def test_set_identity_blank_400(self, client: TestClient) -> None:
        res = client.post("/api/git/identity", json={"user_name": "", "user_email": "x@y.z"})
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/git/commit and GET /api/git/milestones (P3)
# ---------------------------------------------------------------------------


class TestCommitAndMilestonesRoutes:
    def _set_branch(self, client: TestClient) -> None:
        res = client.post("/api/git/working-branch", json={"branch": "pricing-dev", "create": True})
        assert res.status_code == 200

    def test_commit_requires_working_branch(self, client: TestClient) -> None:
        res = client.post("/api/git/commit", json={"message": "x"})
        assert res.status_code == 400  # no working branch set

    def test_commit_after_save(self, client: TestClient, tmp_path: Path) -> None:
        self._set_branch(client)
        # a ledger save: write a file and commit it on the ledger via the engine
        (tmp_path / "thing.py").write_text("x = 1\n")
        from haute._git import commit_save

        commit_save(["thing.py"], "pricing-dev", cwd=tmp_path)

        res = client.post(
            "/api/git/commit", json={"message": "Milestone one", "version_label": "1.0"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["working_branch"] == "pricing-dev"
        assert body["version_label"] == "1.0"
        assert body["short_sha"]

    def test_commit_behind_remote_returns_409_then_override_commits(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        # U4/D4: a milestone while behind the remote forks it → 409 with the fork
        # data; the deliberate allow_fork override then commits.
        import haute._git as git_mod
        from haute._git import commit_milestone, commit_save, fetch_pair, resolve_ledger
        from haute._git_state import write_working_branch

        self._set_branch(client)
        bare = tmp_path / "origin.git"
        _git(tmp_path, "init", "--bare", str(bare))
        _git(tmp_path, "remote", "add", "origin", str(bare))
        assert client.post("/api/git/push", json={"remote": "origin"}).status_code == 200

        # Teammate publishes a milestone on the working branch → remote moves ahead.
        other = tmp_path / "other"
        _git(tmp_path, "clone", str(bare), str(other))
        _git(other, "config", "user.name", "Other")
        _git(other, "config", "user.email", "other@example.com")
        _git(other, "checkout", "pricing-dev")
        write_working_branch(other, "pricing-dev")
        resolve_ledger("pricing-dev", cwd=other)
        (other / "r.txt").write_text("teammate\n")
        commit_save(["r.txt"], "pricing-dev", cwd=other)
        commit_milestone("teammate milestone", other, cwd=other)
        _git(other, "push", "origin", "pricing-dev")

        git_mod._fetch_cooldowns.clear()
        fetch_pair("origin", "pricing-dev", cwd=tmp_path)

        # A local pending save, then save&commit would fork.
        (tmp_path / "local.py").write_text("x = 1\n")
        commit_save(["local.py"], "pricing-dev", cwd=tmp_path)

        res = client.post("/api/git/commit", json={"message": "mine"})
        assert res.status_code == 409
        body = res.json()["detail"]
        assert body["status"] == "would_fork"
        assert body["working"]["status"] in ("behind", "diverged")
        assert "fork" in body["message"]

        res2 = client.post("/api/git/commit", json={"message": "mine", "allow_fork": True})
        assert res2.status_code == 200

    def test_commit_no_new_saves_400(self, client: TestClient) -> None:
        self._set_branch(client)
        res = client.post("/api/git/commit", json={"message": "nothing"})
        assert res.status_code == 400

    def test_milestones_empty_without_branch(self, client: TestClient) -> None:
        res = client.get("/api/git/milestones")
        assert res.status_code == 200
        assert res.json()["entries"] == []

    def test_milestones_lists_after_commit(self, client: TestClient, tmp_path: Path) -> None:
        self._set_branch(client)
        (tmp_path / "thing.py").write_text("x = 1\n")
        from haute._git import commit_save

        commit_save(["thing.py"], "pricing-dev", cwd=tmp_path)
        client.post("/api/git/commit", json={"message": "Milestone one"})

        res = client.get("/api/git/milestones")
        assert res.status_code == 200
        body = res.json()
        assert body["working_branch"] == "pricing-dev"
        assert any(e["message"] == "Milestone one" for e in body["entries"])


# ---------------------------------------------------------------------------
# GET /api/git/pending-saves and /api/git/milestones/{sha}/saves (P5 ledger view)
# ---------------------------------------------------------------------------


class TestLedgerSaveRoutes:
    def _set_branch(self, client: TestClient) -> None:
        res = client.post("/api/git/working-branch", json={"branch": "pricing-dev", "create": True})
        assert res.status_code == 200

    def test_pending_saves_lists_unmilestoned(self, client: TestClient, tmp_path: Path) -> None:
        self._set_branch(client)
        (tmp_path / "thing.py").write_text("x = 1\n")
        from haute._git import commit_save

        commit_save(["thing.py"], "pricing-dev", cwd=tmp_path)
        res = client.get("/api/git/pending-saves")
        assert res.status_code == 200
        saves = res.json()["saves"]
        assert len(saves) == 1
        assert saves[0]["files"]  # carries the file changes

    def test_pending_saves_empty_without_branch(self, client: TestClient) -> None:
        res = client.get("/api/git/pending-saves")
        assert res.status_code == 200
        assert res.json()["saves"] == []

    def test_milestone_saves_returns_folded_and_clears_pending(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._set_branch(client)
        (tmp_path / "thing.py").write_text("x = 1\n")
        from haute._git import commit_save

        commit_save(["thing.py"], "pricing-dev", cwd=tmp_path)
        commit = client.post("/api/git/commit", json={"message": "MS"}).json()
        res = client.get(f"/api/git/milestones/{commit['sha']}/saves")
        assert res.status_code == 200
        assert len(res.json()["saves"]) == 1
        # folded in → nothing pending now
        assert client.get("/api/git/pending-saves").json()["saves"] == []

    def test_milestone_saves_unknown_sha_400(self, client: TestClient) -> None:
        res = client.get(f"/api/git/milestones/{'0' * 40}/saves")
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# Branch manager (P5b): GET /working-branches, pair-aware archive + delete
# ---------------------------------------------------------------------------


class TestBranchManagerRoutes:
    def _set_branch(self, client: TestClient) -> None:
        res = client.post("/api/git/working-branch", json={"branch": "pricing-dev", "create": True})
        assert res.status_code == 200

    def _save(self, tmp_path: Path) -> None:
        from haute._git import commit_save

        (tmp_path / "thing.py").write_text("x = 1\n")
        commit_save(["thing.py"], "pricing-dev", cwd=tmp_path)

    def test_working_branches_lists_current(self, client: TestClient) -> None:
        self._set_branch(client)
        res = client.get("/api/git/working-branches")
        assert res.status_code == 200
        body = res.json()
        assert body["current"] == "pricing-dev"
        assert any(b["name"] == "pricing-dev" and b["is_current"] for b in body["branches"])

    def test_archive_pair(self, client: TestClient, tmp_path: Path) -> None:
        self._set_branch(client)
        self._save(tmp_path)  # spawn the ledger
        res = client.post("/api/git/archive", json={"branch": "pricing-dev"})
        assert res.status_code == 200
        assert res.json()["archived_as"] == "archive/pricing-dev"
        branches = _git(tmp_path, "branch")
        assert "archive/pricing-dev" in branches
        assert "archive/pricing-dev-save" in branches

    def test_delete_refuses_unmerged_then_confirms(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._set_branch(client)
        self._save(tmp_path)  # unmerged ledger save
        refused = client.request("DELETE", "/api/git/branches", json={"branch": "pricing-dev"})
        assert refused.status_code == 403  # guardrail refusal
        confirmed = client.request(
            "DELETE", "/api/git/branches", json={"branch": "pricing-dev", "confirm": True}
        )
        assert confirmed.status_code == 200
        assert "pricing-dev" not in _git(tmp_path, "branch")

    def test_working_branches_flags_uncommitted_changes(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._set_branch(client)
        (tmp_path / "README.md").write_text("# tracked edit\n")  # tracked, uncommitted
        res = client.get("/api/git/working-branches")
        assert res.status_code == 200
        current = next(b for b in res.json()["branches"] if b["name"] == "pricing-dev")
        assert current["has_uncommitted_changes"] is True

    def test_restore_unarchives(self, client: TestClient, tmp_path: Path) -> None:
        self._set_branch(client)
        self._save(tmp_path)
        archived = client.post("/api/git/archive", json={"branch": "pricing-dev"}).json()[
            "archived_as"
        ]
        res = client.post("/api/git/restore", json={"branch": archived})
        assert res.status_code == 200
        assert res.json()["restored_as"] == "pricing-dev"
        branches = _git(tmp_path, "branch")
        assert "pricing-dev" in branches
        assert archived not in branches


class TestRefMoversPauseWatcher:
    """M4: every tree-replacing git route must run inside ``pause_watcher`` so a
    wholesale checkout never races the file-watcher. This guard fails if a
    ref-mover (or a future edit) drops the wrap — the spy is never entered."""

    @pytest.mark.parametrize(
        "method, path, body",
        [
            ("post", "/api/git/move", {"sha": "HEAD"}),
            ("post", "/api/git/fast-forward", {"remote": "origin"}),
            ("post", "/api/git/branch-away", {"remote": "origin"}),
            (
                "post",
                "/api/git/working-branch",
                {"branch": "pricing/test-user/x", "create": True},
            ),
            ("post", "/api/git/working-branches", {"name": "pricing/test-user/y"}),
            ("post", "/api/git/archive", {"branch": "pricing/test-user/z"}),
            ("delete", "/api/git/branches", {"branch": "pricing/test-user/z"}),
        ],
    )
    def test_ref_mover_pauses_watcher(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        method: str,
        path: str,
        body: dict[str, object],
    ) -> None:
        from collections.abc import Iterator
        from contextlib import contextmanager

        from haute.routes import git as git_route

        entered: list[bool] = []

        @contextmanager
        def spy(*_a: object, **_k: object) -> Iterator[None]:
            entered.append(True)
            yield

        monkeypatch.setattr(git_route, "pause_watcher", spy)
        # The response status is irrelevant — the wrap is entered before the engine
        # call, so even an erroring op must record an entry.
        client.request(method.upper(), path, json=body)
        assert entered, f"{method.upper()} {path} did not pause the watcher (M4)"
