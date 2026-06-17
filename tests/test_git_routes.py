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
# GET /api/git/status
# ---------------------------------------------------------------------------


class TestGitStatus:
    def test_returns_main(self, client: TestClient) -> None:
        res = client.get("/api/git/status")
        assert res.status_code == 200
        body = res.json()
        assert body["branch"] == "main"
        assert body["is_main"] is True
        assert body["is_read_only"] is True

    def test_returns_changed_files(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / "new.py").write_text("x = 1\n")
        res = client.get("/api/git/status")
        assert "new.py" in res.json()["changed_files"]

    def test_on_own_branch(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        res = client.get("/api/git/status")
        body = res.json()
        assert body["branch"] == "pricing/test-user/feat"
        assert body["is_main"] is False
        assert body["is_read_only"] is False


# ---------------------------------------------------------------------------
# POST /api/git/branches — create
# ---------------------------------------------------------------------------


class TestGitCreateBranch:
    def test_creates_branch(self, client: TestClient, tmp_path: Path) -> None:
        res = client.post("/api/git/branches", json={"description": "Update factors"})
        assert res.status_code == 200
        body = res.json()
        assert body["branch"] == "pricing/test-user/update-factors"
        # Verify we're on the new branch
        assert _git(tmp_path, "symbolic-ref", "--short", "HEAD") == body["branch"]

    def test_empty_description(self, client: TestClient) -> None:
        res = client.post("/api/git/branches", json={"description": ""})
        assert res.status_code == 400

    def test_duplicate_name(self, client: TestClient, tmp_path: Path) -> None:
        client.post("/api/git/branches", json={"description": "my feature"})
        _git(tmp_path, "checkout", "main")
        res = client.post("/api/git/branches", json={"description": "my feature"})
        assert res.status_code == 400
        assert "already exists" in res.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/git/branches — list
# ---------------------------------------------------------------------------


class TestGitListBranches:
    def test_lists_branches(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        _git(tmp_path, "checkout", "main")

        res = client.get("/api/git/branches")
        assert res.status_code == 200
        body = res.json()
        assert body["current"] == "main"
        names = [b["name"] for b in body["branches"]]
        assert "main" in names
        assert "pricing/test-user/feat" in names

    def test_own_branches_flagged(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/mine")
        _git(tmp_path, "checkout", "main")
        _git(tmp_path, "checkout", "-b", "pricing/other-user/theirs")
        _git(tmp_path, "checkout", "main")

        res = client.get("/api/git/branches")
        branches = {b["name"]: b for b in res.json()["branches"]}
        assert branches["pricing/test-user/mine"]["is_yours"] is True
        assert branches["pricing/other-user/theirs"]["is_yours"] is False


# ---------------------------------------------------------------------------
# POST /api/git/switch
# ---------------------------------------------------------------------------


class TestGitSwitch:
    def test_switches(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        _git(tmp_path, "checkout", "main")

        res = client.post("/api/git/switch", json={"branch": "pricing/test-user/feat"})
        assert res.status_code == 200
        assert _git(tmp_path, "symbolic-ref", "--short", "HEAD") == "pricing/test-user/feat"

    def test_auto_commits_dirty(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        (tmp_path / "dirty.py").write_text("x = 1\n")

        res = client.post("/api/git/switch", json={"branch": "main"})
        assert res.status_code == 200

        # Switch back and verify the file is there
        _git(tmp_path, "checkout", "pricing/test-user/feat")
        assert (tmp_path / "dirty.py").exists()


# ---------------------------------------------------------------------------
# POST /api/git/save
# ---------------------------------------------------------------------------


class TestGitSave:
    def test_saves(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        (tmp_path / "main.py").write_text("x = 1\n")

        res = client.post("/api/git/save")
        assert res.status_code == 200
        body = res.json()
        assert body["commit_sha"]
        assert body["message"] == "Updated main"

    def test_blocked_on_main(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / "new.py").write_text("x = 1\n")
        res = client.post("/api/git/save")
        assert res.status_code == 403

    def test_no_changes(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        res = client.post("/api/git/save")
        assert res.status_code == 400
        assert "No changes" in res.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/git/submit
# ---------------------------------------------------------------------------


class TestGitSubmit:
    def test_submit_returns_branch(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        res = client.post("/api/git/submit")
        assert res.status_code == 200
        assert res.json()["branch"] == "pricing/test-user/feat"

    def test_blocked_on_main(self, client: TestClient) -> None:
        res = client.post("/api/git/submit")
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/git/history
# ---------------------------------------------------------------------------


class TestGitHistory:
    def test_returns_entries(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        (tmp_path / "a.py").write_text("a = 1\n")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", "Add a.py")

        res = client.get("/api/git/history")
        assert res.status_code == 200
        entries = res.json()["entries"]
        assert len(entries) == 1
        assert entries[0]["message"] == "Add a.py"

    def test_respects_limit(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text(f"x = {i}\n")
            _git(tmp_path, "add", ".")
            _git(tmp_path, "commit", "-m", f"Commit {i}")

        res = client.get("/api/git/history?limit=3")
        assert len(res.json()["entries"]) == 3


# ---------------------------------------------------------------------------
# POST /api/git/revert
# ---------------------------------------------------------------------------


class TestGitRevert:
    def test_reverts(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        (tmp_path / "a.py").write_text("v1\n")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", "v1")
        target = _git(tmp_path, "rev-parse", "HEAD")

        (tmp_path / "a.py").write_text("v2\n")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", "v2")

        res = client.post("/api/git/revert", json={"sha": target})
        assert res.status_code == 200
        assert res.json()["reverted_to"] == target[:7]
        assert (tmp_path / "a.py").read_text() == "v1\n"

    def test_blocked_on_main(self, client: TestClient, tmp_path: Path) -> None:
        sha = _git(tmp_path, "rev-parse", "HEAD")
        res = client.post("/api/git/revert", json={"sha": sha})
        assert res.status_code == 403

    def test_invalid_sha(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        res = client.post("/api/git/revert", json={"sha": "deadbeef12345678"})
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/git/pull
# ---------------------------------------------------------------------------


class TestGitPull:
    def test_blocked_on_main(self, client: TestClient) -> None:
        res = client.post("/api/git/pull")
        # main is protected → 403
        assert res.status_code in (400, 403)

    def test_no_remote(self, client: TestClient, tmp_path: Path) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        res = client.post("/api/git/pull")
        assert res.status_code == 400
        assert "No remote" in res.json()["detail"]


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
        res = client.post(
            "/api/git/working-branches", json={"name": "pricing/test-user/feature"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["switched"] is False and body["moved"] is False
        assert "pricing/test-user/feature" in _git(tmp_path, "branch")

    def test_duplicate_name_rejected(self, client: TestClient) -> None:
        self._adopt(client)
        client.post("/api/git/working-branches", json={"name": "pricing/test-user/x"})
        res = client.post(
            "/api/git/working-branches", json={"name": "pricing/test-user/x"}
        )
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
        ("get_status", "GET", "/api/git/status", None),
        ("list_branches", "GET", "/api/git/branches", None),
        ("create_branch", "POST", "/api/git/branches", {"description": "test"}),
        ("switch_branch", "POST", "/api/git/switch", {"branch": "x"}),
        ("save_progress", "POST", "/api/git/save", None),
        ("submit_for_review", "POST", "/api/git/submit", None),
        ("get_history", "GET", "/api/git/history", None),
        ("revert_to", "POST", "/api/git/revert", {"sha": "abc123"}),
        ("pull_latest", "POST", "/api/git/pull", None),
        ("archive_working_pair", "POST", "/api/git/archive", {"branch": "x"}),
        ("delete_working_pair", "DELETE", "/api/git/branches", {"branch": "x"}),
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
            with pytest.raises(Exception):  # HTTPException
                _handle_git_error(GitError("something broke"))
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert call_args[0][0] == "git_error"

    def test_logs_guardrail_error(self) -> None:
        from unittest.mock import patch

        from haute._git import GitGuardrailError
        from haute.routes.git import _handle_git_error

        with patch("haute.routes.git.logger") as mock_logger:
            with pytest.raises(Exception):  # HTTPException
                _handle_git_error(GitGuardrailError("not allowed"))
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert call_args[0][0] == "git_guardrail_error"


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
        ("get_status", "GET", "/api/git/status", None),
        ("list_branches", "GET", "/api/git/branches", None),
        ("create_branch", "POST", "/api/git/branches", {"description": "test"}),
        ("switch_branch", "POST", "/api/git/switch", {"branch": "x"}),
        ("save_progress", "POST", "/api/git/save", None),
        ("submit_for_review", "POST", "/api/git/submit", None),
        ("get_history", "GET", "/api/git/history", None),
        ("revert_to", "POST", "/api/git/revert", {"sha": "abc123"}),
        ("pull_latest", "POST", "/api/git/pull", None),
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
# Edge cases for specific endpoints
# ---------------------------------------------------------------------------


class TestGitHistoryEdgeCases:
    def test_limit_bounds_low(self, client: TestClient) -> None:
        """limit=0 should fail validation (ge=1)."""
        res = client.get("/api/git/history?limit=0")
        assert res.status_code == 422

    def test_limit_bounds_high(self, client: TestClient) -> None:
        """limit=501 should fail validation (le=500)."""
        res = client.get("/api/git/history?limit=501")
        assert res.status_code == 422

    def test_empty_history(self, client: TestClient, tmp_path: Path) -> None:
        """Branch with no unique commits returns empty entries."""
        _git(tmp_path, "checkout", "-b", "pricing/test-user/empty")
        res = client.get("/api/git/history")
        assert res.status_code == 200
        assert res.json()["entries"] == []


class TestGitCreateBranchEdgeCases:
    def test_whitespace_only_description(self, client: TestClient) -> None:
        """A whitespace-only description should be rejected."""
        res = client.post("/api/git/branches", json={"description": "   "})
        assert res.status_code == 400
        assert "empty" in res.json()["detail"].lower()


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
        res = client.post(
            "/api/git/working-branch", json={"branch": "fresh", "create": True}
        )
        assert res.status_code == 200
        assert res.json()["working_branch"] == "fresh"

    def test_set_protected_refused_403(self, client: TestClient) -> None:
        res = client.post("/api/git/working-branch", json={"branch": "main"})
        assert res.status_code == 403

    def test_set_missing_branch_400(self, client: TestClient) -> None:
        res = client.post("/api/git/working-branch", json={"branch": "ghost"})
        assert res.status_code == 400


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
        res = client.post(
            "/api/git/identity", json={"user_name": "", "user_email": "x@y.z"}
        )
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/git/commit and GET /api/git/milestones (P3)
# ---------------------------------------------------------------------------


class TestCommitAndMilestonesRoutes:
    def _set_branch(self, client: TestClient) -> None:
        res = client.post(
            "/api/git/working-branch", json={"branch": "pricing-dev", "create": True}
        )
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
        res = client.post(
            "/api/git/working-branch", json={"branch": "pricing-dev", "create": True}
        )
        assert res.status_code == 200

    def test_pending_saves_lists_unmilestoned(
        self, client: TestClient, tmp_path: Path
    ) -> None:
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
        res = client.post(
            "/api/git/working-branch", json={"branch": "pricing-dev", "create": True}
        )
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
        refused = client.request(
            "DELETE", "/api/git/branches", json={"branch": "pricing-dev"}
        )
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
