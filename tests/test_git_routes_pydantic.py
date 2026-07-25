"""Pin the internal Git API to Pydantic models (item #74).

The Git engine returns the response models used by the route layer directly.
These tests prevent the old dataclass-to-Pydantic rewrapping shim from
returning.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import BaseModel

from tests._git_helpers import init_repo as _init_repo


@pytest.fixture(autouse=True)
def _isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test runs in a fresh Git repository."""
    repo = _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    return repo


class TestGitModuleReturnsPydantic:
    def test_move_to_commit_returns_pydantic_model(self, tmp_path: Path) -> None:
        from haute._git import move_to_commit
        from haute.schemas import GitMoveResponse

        result = move_to_commit("HEAD", tmp_path)

        assert isinstance(result, BaseModel), (
            "_git.move_to_commit() must return a Pydantic BaseModel; "
            f"got {type(result).__name__!r}."
        )
        GitMoveResponse.model_validate(result.model_dump())


class TestGitRouteBodiesDoNotRewrap:
    def test_no_dc_to_pydantic_helper_remains(self) -> None:
        from haute.routes import git as git_routes

        source = inspect.getsource(git_routes)
        assert "_dc_to_pydantic" not in source

    def test_no_dataclasses_round_trip_remains(self) -> None:
        from haute.routes import git as git_routes

        source = inspect.getsource(git_routes)
        assert "import dataclasses" not in source
        assert "dataclasses.asdict" not in source


class TestGitPushResponseContract:
    def test_default_bootstrap_fields_are_required_with_false_default(self) -> None:
        from pydantic import ValidationError

        from haute.schemas import GitPushResponse

        with pytest.raises(ValidationError):
            GitPushResponse(
                remote="origin",
                working_branch="pricing/alice/dev",
                ledger_branch="pricing/alice/dev-save",
            )
        response = GitPushResponse(
            remote="origin",
            working_branch="pricing/alice/dev",
            ledger_branch="pricing/alice/dev-save",
            default_branch="main",
        )
        assert response.default_branch == "main"
        assert response.bootstrapped_default is False
