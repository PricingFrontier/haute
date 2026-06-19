"""Pin the internal git API to Pydantic models (item #74).

Currently ``haute._git`` returns ``@dataclass`` instances (``GitStatus``,
``BranchListResult``) and ``routes/git.py`` rewraps every result through
``_dc_to_pydantic`` — a dataclass -> dict -> Pydantic round-trip.  This
double indirection has no value; it just widens the surface where a field
could drift between the dataclass and the schema.

Item #74 demands that ``_git.py`` return Pydantic models directly, so
the route bodies collapse to ``return get_status()`` (and equivalents)
without the shim.  These tests pin the desired contract:

* every public function in ``_git`` that currently returns a dataclass
  returns a Pydantic ``BaseModel`` instance matching the schema used by
  the route;
* the route bodies do not contain ``_dc_to_pydantic`` or any equivalent
  ``dataclasses.asdict(...) + model_validate(...)`` round-trip;
* the wire-format payload for the most user-visible read endpoint
  (``GET /api/git/status``) is unchanged from the commit-c5ad780
  baseline — the refactor must be a pure internal cleanup, never a
  client-visible regression.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

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

    def test_move_to_commit_returns_pydantic_model(self, tmp_path: Path) -> None:
        from haute._git import move_to_commit
        from haute.schemas import GitMoveResponse

        # Moving to the current commit is a valid no-op detach on a clean tree.
        result = move_to_commit("HEAD", tmp_path)

        assert isinstance(result, BaseModel), (
            f"_git.move_to_commit() must return a Pydantic BaseModel; "
            f"got {type(result).__name__!r}."
        )
        GitMoveResponse.model_validate(result.model_dump())


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
            f"#74: {route_func_name} still uses dataclasses.asdict — drop the shim. Body:\n{source}"
        )
        # A list-comprehension building Pydantic models over dataclass
        # entries is the history-specific variant of the same smell.
        assert "for e in entries" not in source, (
            f"#74: {route_func_name} still builds Pydantic entries "
            f"one-at-a-time from a dataclass list — the _git readers "
            f"(working_milestones / pending_ledger_saves) hand back models "
            f"directly. Body:\n{source}"
        )


# ---------------------------------------------------------------------------
# 3. Wire-shape stability — the client sees the same JSON
# ---------------------------------------------------------------------------


class TestGitRouteWireShapeUnchanged:
    """Users must not notice the refactor.  The JSON body shape for the
    highest-traffic read endpoint (``/api/git/status``) must match the
    commit-c5ad780 baseline.
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
        assert body["main_last_updated"] is None or isinstance(body["main_last_updated"], str)
