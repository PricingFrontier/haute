"""Tests for git panel API endpoints (routes/git.py).

Uses real git repos in tmp_path for integration testing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

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
    """Delete pins the pair under ``refs/haute/trash/<id>/`` + a ``.haute/trash.json``
    entry keyed by that id; undelete rebuilds the pair exactly (under its own name
    or another) and consumes both."""

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
        entry_id = res.json()["trash_id"]
        assert entry_id.startswith(f"{self._FORK}@")

        # Branch refs gone; the entry's pins + tombstone preserve the pair.
        assert self._FORK not in _git(tmp_path, "branch")
        assert _git(tmp_path, "rev-parse", f"refs/haute/trash/{entry_id}/working") == branch_tip
        assert _git(tmp_path, "rev-parse", f"refs/haute/trash/{entry_id}/ledger") == ledger_tip
        tombstone = read_trash(tmp_path)[entry_id]
        assert tombstone["branch"] == self._FORK
        assert tombstone["branch_tip"] == branch_tip
        assert tombstone["ledger_tip"] == ledger_tip
        assert tombstone["was_archived"] is False
        assert tombstone["deleted_at"]
        listing = client.get("/api/git/working-branches").json()
        assert [d["branch"] for d in listing["deleted"]] == [self._FORK]
        assert listing["deleted"][0]["id"] == entry_id

        res = client.post("/api/git/undelete", json={"id": entry_id})
        assert res.status_code == 200
        assert res.json() == {"status": "restored", "branch": self._FORK}
        # Tips identical to before the delete; trash consumed.
        assert _git(tmp_path, "rev-parse", self._FORK) == branch_tip
        assert _git(tmp_path, "rev-parse", ledger) == ledger_tip
        assert read_trash(tmp_path) == {}
        assert _git(tmp_path, "for-each-ref", "refs/haute/trash/") == ""

    def test_undelete_unknown_entry_is_a_domain_error(self, client: TestClient) -> None:
        res = client.post(
            "/api/git/undelete", json={"id": "pricing/test-user/ghost@20260101T000000Z"}
        )
        assert res.status_code == 400
        assert res.json()["detail"] == "That deleted branch is no longer in the trash."

    def test_undelete_refuses_a_reoccupied_name_and_restores_under_another(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._seed_fork_with_history(tmp_path)
        branch_tip = _git(tmp_path, "rev-parse", self._FORK)
        res = client.request(
            "DELETE", "/api/git/branches", json={"branch": self._FORK, "confirm": True}
        )
        entry_id = res.json()["trash_id"]
        _git(tmp_path, "branch", self._FORK)  # a NEW branch reclaims the name

        res = client.post("/api/git/undelete", json={"id": entry_id})
        assert res.status_code == 400
        assert "already exists" in res.json()["detail"]
        assert "another name" in res.json()["detail"]

        res = client.post("/api/git/undelete", json={"id": entry_id, "as_name": "feat old"})
        assert res.status_code == 400
        assert res.json()["detail"] == (
            "'feat old' can't be used as a branch name: it contains a space. "
            "Use letters, numbers, dashes and slashes."
        )

        res = client.post(
            "/api/git/undelete", json={"id": entry_id, "as_name": "pricing/test-user/feat-old"}
        )
        assert res.status_code == 200
        assert res.json()["branch"] == "pricing/test-user/feat-old"
        assert _git(tmp_path, "rev-parse", "pricing/test-user/feat-old") == branch_tip

    def test_a_name_deleted_twice_keeps_both_deletions(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        from haute._git_state import read_trash

        self._seed_fork_with_history(tmp_path)
        first_tip = _git(tmp_path, "rev-parse", self._FORK)
        first = client.request(
            "DELETE", "/api/git/branches", json={"branch": self._FORK, "confirm": True}
        ).json()["trash_id"]
        _git(tmp_path, "branch", self._FORK, "main")  # made again, elsewhere
        second = client.request(
            "DELETE", "/api/git/branches", json={"branch": self._FORK, "confirm": True}
        ).json()["trash_id"]

        assert first != second
        assert set(read_trash(tmp_path)) == {first, second}
        res = client.post("/api/git/undelete", json={"id": first})
        assert res.status_code == 200
        assert _git(tmp_path, "rev-parse", self._FORK) == first_tip

    def test_a_tombstone_from_before_entry_ids_is_restored(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        from haute._git_state import read_trash, record_trash

        tip = _git(tmp_path, "rev-parse", "HEAD")
        _git(tmp_path, "update-ref", f"refs/haute/trash/{self._FORK}", tip)
        record_trash(
            tmp_path,
            self._FORK,
            {"branch_tip": tip, "ledger_tip": None, "was_archived": False, "deleted_at": "x"},
        )

        res = client.post("/api/git/undelete", json={"id": self._FORK})

        assert res.status_code == 200
        assert _git(tmp_path, "rev-parse", self._FORK) == tip
        assert read_trash(tmp_path) == {}
        assert _git(tmp_path, "for-each-ref", "refs/haute/trash/") == ""

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
        entry_id = res.json()["trash_id"]
        assert read_trash(tmp_path)[entry_id]["was_archived"] is True

        res = client.post("/api/git/undelete", json={"id": entry_id})
        assert res.status_code == 200
        assert _git(tmp_path, "rev-parse", archived) == tip
        assert _git(tmp_path, "rev-parse", f"{archived}-save") == ledger_tip
        # Archived state IS the name prefix — the branch manager sees it again.
        listing = client.get("/api/git/working-branches").json()
        row = next(b for b in listing["branches"] if b["name"] == archived)
        assert row["is_archived"] is True

    def test_the_cap_drops_the_oldest_entry_with_its_pins(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        from haute._git_state import read_trash, record_trash

        tip = _git(tmp_path, "rev-parse", "HEAD")
        for i in range(20):
            entry_id = f"pricing/test-user/b{i:02d}@20260101T0000{i:02d}Z"
            pin = f"refs/haute/trash/{entry_id}/working"
            _git(tmp_path, "update-ref", pin, tip)
            record_trash(
                tmp_path,
                entry_id,
                {
                    "branch": f"pricing/test-user/b{i:02d}",
                    "branch_tip": tip,
                    "working_pin": pin,
                    "ledger_pin": f"refs/haute/trash/{entry_id}/ledger",
                },
            )
        _git(tmp_path, "branch", self._FORK)

        res = client.request(
            "DELETE", "/api/git/branches", json={"branch": self._FORK, "confirm": True}
        )

        assert res.status_code == 200
        trash = read_trash(tmp_path)
        assert len(trash) == 20
        oldest = "pricing/test-user/b00@20260101T000000Z"
        assert oldest not in trash
        assert res.json()["trash_id"] in trash
        assert _git(tmp_path, "for-each-ref", f"refs/haute/trash/{oldest}/") == ""


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

    def test_switch_opens_the_new_branch(self, client: TestClient, tmp_path: Path) -> None:
        self._adopt(client)
        res = client.post(
            "/api/git/working-branches",
            json={"name": "pricing/test-user/idea", "switch": True},
        )
        assert res.status_code == 200
        assert res.json()["switched"] is True and res.json()["moved"] is False
        assert _git(tmp_path, "symbolic-ref", "--short", "HEAD") == "pricing/test-user/idea-save"
        status = client.get("/api/git/working-branch").json()
        assert status["working_branch"] == "pricing/test-user/idea"

    def test_move_and_switch_together_rejected(self, client: TestClient) -> None:
        self._adopt(client)
        res = client.post(
            "/api/git/working-branches",
            json={"name": "pricing/test-user/x", "move": True, "switch": True},
        )
        assert res.status_code == 400

    def test_a_typed_name_git_cannot_use_is_refused_naming_the_rule(
        self, client: TestClient
    ) -> None:
        self._adopt(client)
        res = client.post("/api/git/working-branches", json={"name": "my branch"})
        assert res.status_code == 400
        assert res.json()["detail"] == (
            "'my branch' can't be used as a branch name: it contains a space. "
            "Use letters, numbers, dashes and slashes."
        )


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
        ("push_with_ci_secrets", "POST", "/api/git/push", {"remote": "origin"}),
        ("set_missing_ci_secrets", "POST", "/api/git/ci-secrets", {"remote": "origin"}),
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
# E6: git_error_http_exception logs warnings
# ---------------------------------------------------------------------------


class TestGitErrorHttpExceptionLogging:
    """git_error_http_exception should log warnings for GitError and GitGuardrailError."""

    def test_logs_git_error(self) -> None:
        from unittest.mock import patch

        from haute._git import GitError
        from haute.routes.git import git_error_http_exception

        with patch("haute.routes.git.logger") as mock_logger:
            http_exc = git_error_http_exception(GitError("something broke"))
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert call_args[0][0] == "git_error"
        assert http_exc.status_code == 400

    def test_logs_guardrail_error(self) -> None:
        from unittest.mock import patch

        from haute._git import GitGuardrailError
        from haute.routes.git import git_error_http_exception

        with patch("haute.routes.git.logger") as mock_logger:
            http_exc = git_error_http_exception(GitGuardrailError("not allowed"))
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert call_args[0][0] == "git_guardrail_error"
        assert http_exc.status_code == 403


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

        res = client.get(f"/api/git/show/{sha1}", params={"source_file": "pipeline.py"})
        assert res.status_code == 200
        labels = {n["data"]["label"] for n in res.json()["nodes"]}
        assert labels == {"base"}

    def test_reads_the_requested_pipeline_when_the_project_has_several(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        # "a_first.py" sorts before "rating.py" and has nodes, so any
        # discovery-order choice would return the wrong pipeline.
        (tmp_path / "a_first.py").write_text(self._V2)
        (tmp_path / "rating.py").write_text(self._V1)
        _git(tmp_path, "add", "a_first.py", "rating.py")
        _git(tmp_path, "commit", "-m", "two pipelines")
        sha = _git(tmp_path, "rev-parse", "HEAD")

        res = client.get(f"/api/git/show/{sha}", params={"source_file": "rating.py"})
        assert res.status_code == 200
        body = res.json()
        assert body["source_file"] == "rating.py"
        assert {n["data"]["label"] for n in body["nodes"]} == {"base"}

    def test_pipeline_absent_at_the_commit_returns_400(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        (tmp_path / "pipeline.py").write_text(self._V1)
        _git(tmp_path, "add", "pipeline.py")
        _git(tmp_path, "commit", "-m", "v1")
        sha = _git(tmp_path, "rev-parse", "HEAD")

        res = client.get(f"/api/git/show/{sha}", params={"source_file": "later.py"})
        assert res.status_code == 400
        assert "later.py" in res.json()["detail"]

    def test_source_file_is_required(self, client: TestClient) -> None:
        res = client.get(f"/api/git/show/{'0' * 40}")
        assert res.status_code == 422

    def test_unknown_commit_returns_400(self, client: TestClient) -> None:
        res = client.get(f"/api/git/show/{'0' * 40}", params={"source_file": "pipeline.py"})
        assert res.status_code == 400


# ---------------------------------------------------------------------------
# A working branch adopted through the API, for the routes that act on its history
# ---------------------------------------------------------------------------


class _AdoptedBranch:
    """A working branch adopted through the API, with saves committed on its ledger."""

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


# ---------------------------------------------------------------------------
# POST /api/git/restore-version — an earlier version back as a new save
# ---------------------------------------------------------------------------


class TestGitRestoreVersion(_AdoptedBranch):
    def test_restore_saves_the_version_on_the_same_branch(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from haute import _project_storage

        pushes: list[bool] = []
        monkeypatch.setattr(_project_storage, "enqueue_push", lambda: pushes.append(True))
        self._adopt(client)
        sha1 = self._commit(tmp_path, "# v1\n")
        sha2 = self._commit(tmp_path, "# v2\n")

        res = client.post("/api/git/restore-version", json={"sha": sha1})

        assert res.status_code == 200
        body = res.json()
        assert body["restored_from"] == sha1
        assert body["working_branch"] == "pricing/test-user/dev"
        assert body["short_sha"] == body["sha"][:8]
        assert _git(tmp_path, "rev-parse", "pricing/test-user/dev-save") == body["sha"]
        assert _git(tmp_path, "rev-parse", f"{body['sha']}^") == sha2
        assert (tmp_path / "rating.py").read_text() == "# v1\n"
        status = client.get("/api/git/working-branch").json()
        assert status["state"] == "ready"
        assert status["working_branch"] == "pricing/test-user/dev"
        assert pushes == [True]

    def test_restore_repairs_a_configured_pipeline_that_went_missing(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Listing the pipelines before the action must not refuse it when the
        configured pipeline is gone: restoring a version is how it comes back."""
        self._adopt(client)
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "rating.py"\n')
        _git(tmp_path, "add", "haute.toml")
        sha1 = self._commit(tmp_path, 'import haute\n\npipeline = haute.Pipeline("p")\n')
        self._commit(tmp_path, 'import haute\n\npipeline = haute.Pipeline("p2")\n')
        (tmp_path / "rating.py").unlink()  # deleted by accident, the server still running

        res = client.post("/api/git/restore-version", json={"sha": sha1})

        assert res.status_code == 200, res.text
        assert 'haute.Pipeline("p")' in (tmp_path / "rating.py").read_text()

    def test_restore_of_the_current_version_returns_400(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        self._adopt(client)
        self._commit(tmp_path, "# v1\n")
        sha2 = self._commit(tmp_path, "# v2\n")

        res = client.post("/api/git/restore-version", json={"sha": sha2})

        assert res.status_code == 400
        assert res.json()["detail"] == "This is already the current version."


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
        from haute import _git
        from haute._git import GitError
        from haute.routes._helpers import _INTERNAL_ERROR_DETAIL

        raw_detail = "authentication failed for https://token-secret@example.test/repo.git"
        monkeypatch.setattr(
            _git,
            "push_working_pair",
            lambda *args, **kwargs: (_ for _ in ()).throw(GitError(raw_detail)),
        )

        response = client.post("/api/git/push", json={"remote": "origin"})

        assert response.status_code == 400
        assert response.json()["detail"] == _INTERNAL_ERROR_DETAIL
        assert "token-secret" not in response.text

    def test_push_against_an_unreachable_remote_answers_the_refresh_message(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Push reported a remote it could not reach as "Operation failed", while Catch up
        and Spin off said why; it now says the same as they do."""
        self._adopt(client)
        _git(tmp_path, "remote", "add", "origin", str(tmp_path / "missing.git"))

        res = client.post("/api/git/push", json={"remote": "origin"})

        assert res.status_code == 400
        assert res.json()["detail"] == (
            "Could not refresh 'origin'. Check your connection or credentials and try again."
        )

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
# git_error_http_exception HTTP status codes
# ---------------------------------------------------------------------------


class TestGitErrorHttpExceptionStatusCodes:
    """git_error_http_exception must map GitError to 400 and GitGuardrailError to 403."""

    def test_git_error_raises_400(self) -> None:
        """Phase 1C #11: ``GitError`` messages may contain raw git stderr
        (absolute paths, remote URLs, SSL errors, credentials) so they
        are no longer echoed to the HTTP body.  The handler returns a
        400 with the sanitized ``_INTERNAL_ERROR_DETAIL`` constant; the
        full exception text stays in the structured log.
        """
        from haute._git import GitError
        from haute.routes._helpers import _INTERNAL_ERROR_DETAIL
        from haute.routes.git import git_error_http_exception

        http_exc = git_error_http_exception(GitError("bad ref"))
        assert http_exc.status_code == 400
        assert http_exc.detail == _INTERNAL_ERROR_DETAIL

    def test_guardrail_error_raises_403(self) -> None:
        """Guardrail errors are hand-written, user-facing, and preserved
        verbatim (they describe intentional blocks rather than internal
        failures).
        """
        from haute._git import GitGuardrailError
        from haute.routes.git import git_error_http_exception

        http_exc = git_error_http_exception(GitGuardrailError("protected branch"))
        assert http_exc.status_code == 403
        assert http_exc.detail == "protected branch"


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

    def test_a_broken_pair_is_reported_and_repaired(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _git(tmp_path, "branch", "pricing-dev")
        assert client.post("/api/git/working-branch", json={"branch": "pricing-dev"}).is_success
        _git(tmp_path, "update-ref", "-d", "refs/heads/pricing-dev")

        broken = client.get("/api/git/working-branch").json()["broken_pairs"]
        assert broken == [
            {"kind": "orphan-ledger", "ledger": "pricing-dev-save", "working": "pricing-dev"}
        ]

        res = client.post("/api/git/repair-pair", json={"ledger": "pricing-dev-save"})
        assert res.status_code == 200
        assert res.json() == {"working": "pricing-dev", "ledger": "pricing-dev-save"}
        status = client.get("/api/git/working-branch").json()
        assert status["broken_pairs"] == []
        assert status["state"] == "ready"

        again = client.post("/api/git/repair-pair", json={"ledger": "pricing-dev-save"})
        assert again.status_code == 400
        assert again.json()["detail"] == "'pricing-dev-save' is not part of a broken branch."

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
        from haute._git import commit_milestone, commit_save, resolve_ledger
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

        _git(tmp_path, "fetch", "origin")

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
            ("post", "/api/git/restore-version", {"sha": "HEAD"}),
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


class TestTreeChangesAreAnnounced:
    """A git action that rewrites the files runs with the watcher paused, so once it
    has answered the server announces what is on disk: every open canvas follows it
    as it follows an IDE edit, and the name→path index sees the files as they are."""

    _PIPELINE = 'import haute\n\npipeline = haute.Pipeline("{name}", description="{description}")\n'

    def _commit_pipeline(self, repo: Path, file: str, name: str, description: str) -> None:
        (repo / file).write_text(self._PIPELINE.format(name=name, description=description))
        _git(repo, "add", file)
        _git(repo, "commit", "-m", f"save {description}")

    def _two_branches(self, client: TestClient, repo: Path) -> None:
        """``dev`` holds Pipeline X; ``other``, opened last, holds Pipeline Y and a
        second pipeline dev lacks."""
        res = client.post(
            "/api/git/working-branch", json={"branch": "pricing/test-user/dev", "create": True}
        )
        assert res.status_code == 200
        self._commit_pipeline(repo, "test_pipeline.py", "test_pipeline", "Pipeline X")
        res = client.post(
            "/api/git/working-branches", json={"name": "pricing/test-user/other", "switch": True}
        )
        assert res.status_code == 200
        self._commit_pipeline(repo, "test_pipeline.py", "test_pipeline", "Pipeline Y")
        self._commit_pipeline(repo, "second.py", "second", "Second")

    def test_a_switch_broadcasts_the_destinations_document(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        from haute._event_bus import default_bus

        self._two_branches(client, tmp_path)
        frames: list[dict[str, object]] = []
        unsubscribe = default_bus.subscribe("pipeline.document.update", frames.append)
        try:
            res = client.post("/api/git/working-branch", json={"branch": "pricing/test-user/dev"})
        finally:
            unsubscribe()

        assert res.status_code == 200
        documents = {frame["source_file"]: frame["document"] for frame in frames}
        assert documents["test_pipeline.py"]["pipeline_description"] == "Pipeline X"  # type: ignore[index]
        assert "second.py" not in documents

    def test_a_switch_refreshes_the_pipeline_index(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        from haute.routes._helpers import _ensure_pipeline_index

        self._two_branches(client, tmp_path)
        client.post("/api/git/working-branch", json={"branch": "pricing/test-user/dev"})
        assert "second" not in _ensure_pipeline_index()

        res = client.post("/api/git/working-branch", json={"branch": "pricing/test-user/other"})

        assert res.status_code == 200
        assert "second" in _ensure_pipeline_index()

    def test_a_refused_action_announces_nothing(self, client: TestClient) -> None:
        from haute._event_bus import default_bus

        announced: list[object] = []
        unsubscribe = default_bus.subscribe("git.tree.changed", announced.append)
        try:
            res = client.post("/api/git/working-branch", json={"branch": "main"})
        finally:
            unsubscribe()

        assert res.status_code == 403
        assert announced == []

    @pytest.mark.parametrize(
        "method, path, body, engine, answer",
        [
            (
                "post",
                "/api/git/working-branch",
                {"branch": "b"},
                "set_working_branch",
                {"working_branch": "b", "state": "ready"},
            ),
            (
                "post",
                "/api/git/working-branches",
                {"name": "b"},
                "create_working_branch",
                {"working_branch": "b", "moved": False, "switched": True},
            ),
            (
                "post",
                "/api/git/restore-version",
                {"sha": "HEAD"},
                "restore_version",
                {
                    "sha": "a" * 40,
                    "short_sha": "a" * 8,
                    "restored_from": "b" * 40,
                    "working_branch": "b",
                },
            ),
            (
                "post",
                "/api/git/archive",
                {"branch": "b"},
                "archive_working_pair",
                {"archived_as": "archive/b"},
            ),
            (
                "delete",
                "/api/git/branches",
                {"branch": "b"},
                "delete_working_pair",
                {"status": "deleted", "branch": "b", "trash_id": "b@20261027T000000Z"},
            ),
            (
                "post",
                "/api/git/fast-forward",
                {"remote": "origin"},
                "fast_forward_pair",
                {"remote": "origin", "working_branch": "b"},
            ),
            (
                "post",
                "/api/git/branch-away",
                {"remote": "origin"},
                "branch_away",
                {"working_branch": "b", "set_aside_as": "b-local"},
            ),
        ],
    )
    def test_every_route_that_pauses_the_watcher_announces_the_change(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        method: str,
        path: str,
        body: dict[str, object],
        engine: str,
        answer: dict[str, object],
    ) -> None:
        from haute._event_bus import default_bus
        from haute.routes import git as git_route

        # The engine call answers as it would on success, with the route's own model.
        response_model = next(
            route.response_model  # type: ignore[attr-defined]
            for route in git_route.router.routes
            if route.path == path and method.upper() in route.methods  # type: ignore[attr-defined]
        )
        monkeypatch.setattr(git_route, engine, lambda *_a, **_k: response_model(**answer))
        announced: list[object] = []
        unsubscribe = default_bus.subscribe("git.tree.changed", announced.append)
        try:
            res = client.request(method.upper(), path, json=body)
        finally:
            unsubscribe()

        assert res.status_code == 200, res.text
        assert [set(payload) for payload in announced] == [{"pipelines_before"}]
