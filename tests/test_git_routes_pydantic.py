"""Pin the internal git API to Pydantic models (item #74).

Currently ``haute._git`` returns ``@dataclass`` instances (``GitStatus``,
``BranchListResult``, ``SaveResult``, ``HistoryEntry``, ``RevertResult``,
``PullResult``, ``SubmitResult``) and ``routes/git.py`` rewraps every
result through ``_dc_to_pydantic`` — a dataclass -> dict -> Pydantic
round-trip.  This double indirection has no value; it just widens the
surface where a field could drift between the dataclass and the schema.

Item #74 demands that ``_git.py`` return Pydantic models directly, so
the route bodies collapse to ``return get_status()`` (and equivalents)
without the shim.  These tests pin the desired contract:

* every public function in ``_git`` that currently returns a dataclass
  returns a Pydantic ``BaseModel`` instance matching the schema used by
  the route;
* the route bodies do not contain ``_dc_to_pydantic`` or any equivalent
  ``dataclasses.asdict(...) + model_validate(...)`` round-trip;
* the wire-format payload for the two most user-visible endpoints
  (``GET /api/git/status`` and ``GET /api/git/history``) is unchanged
  from the commit-c5ad780 baseline — the refactor must be a pure
  internal cleanup, never a client-visible regression.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from tests._git_helpers import git_run as _git
from tests._git_helpers import init_repo as _init_repo

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test runs in a fresh git repo so no user state bleeds in."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    return repo


# ---------------------------------------------------------------------------
# 1. _git.py public API returns Pydantic models directly
# ---------------------------------------------------------------------------


class TestGitModuleReturnsPydantic:
    """Every ``_git.py`` function must return a Pydantic model, not a
    dataclass.  Dataclass return types are a smell here because they force
    every caller (currently the git route module) to rewrap — the shim is
    dead weight once schemas exist, which they do.
    """

    def test_get_status_returns_pydantic_model(self) -> None:
        from haute._git import get_status
        from haute.schemas import GitStatusResponse

        result = get_status()

        assert isinstance(result, BaseModel), (
            f"#74: _git.get_status() must return a Pydantic BaseModel "
            f"instance; got {type(result).__name__!r} "
            f"({'dataclass' if hasattr(result, '__dataclass_fields__') else 'other'})."
        )
        # The returned model must either be GitStatusResponse itself or
        # trivially coerce to it (i.e. the fields line up).
        GitStatusResponse.model_validate(result.model_dump())

    def test_list_branches_returns_pydantic_model(self) -> None:
        from haute._git import list_branches
        from haute.schemas import GitBranchListResponse

        result = list_branches()

        assert isinstance(result, BaseModel), (
            f"#74: _git.list_branches() must return a Pydantic BaseModel; "
            f"got {type(result).__name__!r}."
        )
        GitBranchListResponse.model_validate(result.model_dump())

    def test_save_progress_returns_pydantic_model(self, tmp_path: Path) -> None:
        from haute._git import save_progress
        from haute.schemas import GitSaveResponse

        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        (tmp_path / "new.py").write_text("x = 1\n")

        result = save_progress()

        assert isinstance(result, BaseModel), (
            f"#74: _git.save_progress() must return a Pydantic BaseModel; "
            f"got {type(result).__name__!r}."
        )
        GitSaveResponse.model_validate(result.model_dump())

    def test_get_history_returns_list_of_pydantic_models(self, tmp_path: Path) -> None:
        from haute._git import get_history
        from haute.schemas import GitHistoryEntry

        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        (tmp_path / "a.py").write_text("a = 1\n")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", "Add a.py")

        result = get_history(limit=5)

        # Either a list of Pydantic models OR a single GitHistoryResponse
        # model that wraps ``entries``.  Both shapes eliminate the
        # dataclass-to-Pydantic shim; dataclasses are what we're rejecting.
        assert isinstance(result, (list, BaseModel)), (
            f"#74: _git.get_history() must return a list of Pydantic "
            f"models OR a GitHistoryResponse; got {type(result).__name__!r}."
        )
        if isinstance(result, list):
            assert result, "history should contain the commit we just made"
            for entry in result:
                assert isinstance(entry, BaseModel), (
                    f"#74: history entries must be Pydantic models; "
                    f"got {type(entry).__name__!r}."
                )
                GitHistoryEntry.model_validate(entry.model_dump())
        else:
            # Response-shaped return — must expose an 'entries' list of models.
            entries = getattr(result, "entries", None)
            assert entries is not None, (
                "#74: GitHistoryResponse must expose an 'entries' field."
            )
            for entry in entries:
                assert isinstance(entry, BaseModel)
                GitHistoryEntry.model_validate(entry.model_dump())

    def test_revert_to_returns_pydantic_model(self, tmp_path: Path) -> None:
        from haute._git import revert_to
        from haute.schemas import GitRevertResponse

        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        (tmp_path / "a.py").write_text("v1\n")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", "v1")
        target = _git(tmp_path, "rev-parse", "HEAD")
        (tmp_path / "a.py").write_text("v2\n")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", "v2")

        result = revert_to(target)

        assert isinstance(result, BaseModel), (
            f"#74: _git.revert_to() must return a Pydantic BaseModel; "
            f"got {type(result).__name__!r}."
        )
        GitRevertResponse.model_validate(result.model_dump())

    def test_submit_for_review_returns_pydantic_model(self, tmp_path: Path) -> None:
        from haute._git import submit_for_review
        from haute.schemas import GitSubmitResponse

        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        result = submit_for_review()

        assert isinstance(result, BaseModel), (
            f"#74: _git.submit_for_review() must return a Pydantic BaseModel; "
            f"got {type(result).__name__!r}."
        )
        GitSubmitResponse.model_validate(result.model_dump())


# ---------------------------------------------------------------------------
# 2. Route bodies do not rewrap — no _dc_to_pydantic, no asdict round-trip
# ---------------------------------------------------------------------------


class TestGitRouteBodiesDoNotRewrap:
    """After the #74 fix the route body reduces to ``return _git.X(...)``
    inside the try/except.  No dataclass conversion, no model_validate of
    a dict, no kwargs spread from ``asdict``.
    """

    def test_no_dc_to_pydantic_helper_remains(self) -> None:
        from haute.routes import git as git_routes

        source = inspect.getsource(git_routes)
        assert "_dc_to_pydantic" not in source, (
            "#74: routes/git.py still defines or uses `_dc_to_pydantic` — "
            "once _git.py returns Pydantic models directly this shim is "
            "dead code and must be removed."
        )

    def test_no_dataclasses_import_remains(self) -> None:
        from haute.routes import git as git_routes

        source = inspect.getsource(git_routes)
        assert "import dataclasses" not in source, (
            "#74: routes/git.py still imports `dataclasses` — once the "
            "rewrap is gone this import is unused."
        )
        assert "dataclasses.asdict" not in source, (
            "#74: routes/git.py still calls `dataclasses.asdict` — every "
            "use of it is the rewrapping anti-pattern."
        )

    @pytest.mark.parametrize(
        "route_func_name",
        [
            "git_status",
            "git_branches",
            "git_save",
            "git_submit",
            "git_history",
            "git_revert",
            "git_pull",
        ],
    )
    def test_route_body_is_thin_delegation(self, route_func_name: str) -> None:
        """Each route's try block must be a single ``result = <_git call>``
        and the return statement must be either ``return result`` or an
        equally thin form — no ``model_validate(asdict(result))``, no
        ``SomeSchema(entries=[...])`` comprehension over dataclasses.
        """
        from haute.routes import git as git_routes

        func = getattr(git_routes, route_func_name)
        source = inspect.getsource(func)

        # Structural assertions: everything that screams "rewrap" is gone.
        assert "_dc_to_pydantic(" not in source, (
            f"#74: {route_func_name} still calls _dc_to_pydantic. "
            f"Route body should delegate directly to _git.*: got:\n{source}"
        )
        assert "dataclasses.asdict" not in source, (
            f"#74: {route_func_name} still uses dataclasses.asdict — drop "
            f"the shim. Body:\n{source}"
        )
        # A list-comprehension building Pydantic models over dataclass
        # entries is the history-specific variant of the same smell.
        assert "for e in entries" not in source, (
            f"#74: {route_func_name} still builds Pydantic entries "
            f"one-at-a-time from a dataclass list — _git.get_history "
            f"should hand back models directly. Body:\n{source}"
        )


# ---------------------------------------------------------------------------
# 3. Wire-shape stability — the client sees the same JSON
# ---------------------------------------------------------------------------


class TestGitRouteWireShapeUnchanged:
    """Users must not notice the refactor.  The JSON body shape for the
    two highest-traffic read endpoints (``/api/git/status`` and
    ``/api/git/history``) must match the commit-c5ad780 baseline.
    """

    # Fields documented by the ``GitStatus`` dataclass / ``GitStatusResponse``
    # schema as of c5ad780. Every key must be present on every response.
    _STATUS_FIELDS = {
        "branch",
        "is_main",
        "is_read_only",
        "changed_files",
        "main_ahead",
        "main_ahead_by",
        "main_last_updated",
    }

    # HistoryEntry fields, in the list returned by ``/api/git/history``.
    _HISTORY_ENTRY_FIELDS = {
        "sha",
        "short_sha",
        "message",
        "timestamp",
        "files_changed",
    }

    def test_status_wire_shape_matches_c5ad780(self, client: TestClient) -> None:
        res = client.get("/api/git/status")
        assert res.status_code == 200
        body = res.json()
        assert isinstance(body, dict)
        assert set(body.keys()) == self._STATUS_FIELDS, (
            f"#74: GET /api/git/status must return exactly these top-level "
            f"keys (commit c5ad780 contract): "
            f"{sorted(self._STATUS_FIELDS)}.  Got: {sorted(body.keys())}"
        )
        # Specific primitive types that the frontend consumes.
        assert isinstance(body["branch"], str)
        assert isinstance(body["is_main"], bool)
        assert isinstance(body["is_read_only"], bool)
        assert isinstance(body["changed_files"], list)
        assert isinstance(body["main_ahead"], bool)
        assert isinstance(body["main_ahead_by"], int)
        # main_last_updated may be null; must not be an arbitrary object.
        assert body["main_last_updated"] is None or isinstance(
            body["main_last_updated"], str
        )

    def test_history_wire_shape_matches_c5ad780(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _git(tmp_path, "checkout", "-b", "pricing/test-user/feat")
        (tmp_path / "a.py").write_text("a = 1\n")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", "Add a.py")

        res = client.get("/api/git/history?limit=5")
        assert res.status_code == 200
        body = res.json()

        # Top-level: {"entries": [...]}.
        assert isinstance(body, dict) and set(body.keys()) == {"entries"}, (
            f"#74: GET /api/git/history must return exactly one top-level "
            f"key 'entries'. Got: {sorted(body.keys())}"
        )
        entries = body["entries"]
        assert isinstance(entries, list) and entries, "need at least one commit"

        for e in entries:
            assert set(e.keys()) == self._HISTORY_ENTRY_FIELDS, (
                f"#74: history entry fields changed vs c5ad780. "
                f"Expected {sorted(self._HISTORY_ENTRY_FIELDS)}; "
                f"got {sorted(e.keys())}"
            )
            assert isinstance(e["sha"], str) and e["sha"]
            assert isinstance(e["short_sha"], str) and e["short_sha"]
            assert isinstance(e["message"], str)
            assert isinstance(e["timestamp"], str)
            assert isinstance(e["files_changed"], list)
