"""Tests for the Haute project-root helper (``haute._project``).

F7 of the codebase review execution plan. Written TDD-style — these tests
fail until ``src/haute/_project.py`` is implemented. They may also fail on
the import of ``haute.errors.ConfigError`` if Foundation task F1 has not
yet landed; that is an acceptable dependency.

Contract recap (from F7 spec):

    def get_project_root(start: Path | None = None) -> Path:
        # Walks up from ``start`` (or cwd if None) looking for a directory
        # containing ``haute.toml``. Validates it is also inside a git repo
        # (``.git`` directory OR worktree-style file anywhere at/above the
        # haute.toml root).
        # Raises ConfigError if no haute.toml found.
        # Raises ConfigError if haute.toml found but not inside a git repo.

    def is_haute_project(path: Path) -> bool:
        # True iff ``path`` contains ``haute.toml`` and is inside a git repo.

Principle: **fail loudly.** No fallback to cwd, no swallowing of
permission errors while walking up.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from haute._project import get_project_root, is_haute_project
from haute.errors import ConfigError

# ---------------------------------------------------------------------------
# Filesystem builders — small helpers keep each test focused on its
# scenario rather than the mechanics of laying out a fake project.
# ---------------------------------------------------------------------------


def _make_haute_toml(root: Path, body: str = '[project]\nname = "test"\n') -> Path:
    """Create a minimal ``haute.toml`` at ``root`` and return its path."""
    toml = root / "haute.toml"
    toml.write_text(body, encoding="utf-8")
    return toml


def _make_git_dir(root: Path) -> Path:
    """Create a ``.git`` directory at ``root`` (plain repo layout)."""
    git = root / ".git"
    git.mkdir()
    # A real git repo has sub-files; we add one so callers that do a
    # truthy check on contents don't get confused by an empty dir.
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return git


def _make_git_file(root: Path, target: str = "/absolute/gitdir/path") -> Path:
    """Create a ``.git`` **file** at ``root`` (worktree layout).

    Git worktrees write a plain text file named ``.git`` whose contents
    are ``gitdir: <absolute-path-to-real-gitdir>``. ``get_project_root``
    must accept this shape as a valid git repo, otherwise every worktree
    checkout of a Haute project would be rejected.
    """
    git = root / ".git"
    git.write_text(f"gitdir: {target}\n", encoding="utf-8")
    return git


# ===========================================================================
# 1. Happy path
# ===========================================================================


class TestHappyPath:
    def test_returns_tmp_when_haute_toml_and_git_dir_both_present(self, tmp_path: Path) -> None:
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        assert get_project_root(tmp_path) == tmp_path

    def test_returned_path_is_resolved(self, tmp_path: Path) -> None:
        """The returned path should be absolute/resolved.

        Callers build further paths from the return value (cache dirs,
        artifact paths, etc.). Returning a relative or un-resolved path
        would leak CWD coupling into downstream code.
        """
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        result = get_project_root(tmp_path)
        assert result.is_absolute()
        assert result == tmp_path.resolve()


# ===========================================================================
# 2. Walks up from a subdirectory
# ===========================================================================


class TestWalksUp:
    def test_walks_up_from_nested_subdir(self, tmp_path: Path) -> None:
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert get_project_root(nested) == tmp_path

    def test_walks_up_from_immediate_child(self, tmp_path: Path) -> None:
        """Start one directory below the project root."""
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        child = tmp_path / "src"
        child.mkdir()
        assert get_project_root(child) == tmp_path

    def test_walks_up_from_file_containing_dir(self, tmp_path: Path) -> None:
        """Starting from a deeply nested rating config dir still finds root."""
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        deep = tmp_path / "rating" / "config" / "endorsements"
        deep.mkdir(parents=True)
        assert get_project_root(deep) == tmp_path


# ===========================================================================
# 3. Defaulting to current working directory
# ===========================================================================


class TestCwdDefault:
    def test_no_arg_uses_cwd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert get_project_root() == tmp_path

    def test_none_arg_uses_cwd(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit ``None`` must behave identically to omission."""
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert get_project_root(None) == tmp_path

    def test_cwd_default_walks_up(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cwd-based default still walks up to find the project root."""
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        nested = tmp_path / "deep" / "subdir"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        assert get_project_root() == tmp_path


# ===========================================================================
# 4. No ``haute.toml`` anywhere — loud failure
# ===========================================================================


class TestNoHauteToml:
    def test_raises_config_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            get_project_root(tmp_path)

    def test_error_message_mentions_haute_toml(self, tmp_path: Path) -> None:
        """Users need to know what file was missing, not a generic 'not found'.

        The message is load-bearing UX — it is the first thing a new user
        sees when they run ``haute serve`` in the wrong directory.
        """
        with pytest.raises(ConfigError) as exc_info:
            get_project_root(tmp_path)
        assert "haute.toml" in str(exc_info.value)

    def test_raises_even_with_git_present(self, tmp_path: Path) -> None:
        """A bare git repo with no ``haute.toml`` is not a Haute project."""
        _make_git_dir(tmp_path)
        with pytest.raises(ConfigError) as exc_info:
            get_project_root(tmp_path)
        assert "haute.toml" in str(exc_info.value)


# ===========================================================================
# 5. ``haute.toml`` present but not a git repo — loud failure
# ===========================================================================


class TestNotAGitRepo:
    def test_raises_config_error_when_no_dot_git_anywhere(self, tmp_path: Path) -> None:
        _make_haute_toml(tmp_path)
        with pytest.raises(ConfigError):
            get_project_root(tmp_path)

    def test_error_message_mentions_git(self, tmp_path: Path) -> None:
        """The error must distinguish 'no haute.toml' from 'no git repo'.

        Both are ``ConfigError`` but point to different user actions
        (create a project vs. ``git init``).
        """
        _make_haute_toml(tmp_path)
        with pytest.raises(ConfigError) as exc_info:
            get_project_root(tmp_path)
        assert "git" in str(exc_info.value).lower()

    def test_raises_when_called_from_nested_dir_without_git(self, tmp_path: Path) -> None:
        _make_haute_toml(tmp_path)
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        with pytest.raises(ConfigError):
            get_project_root(nested)


# ===========================================================================
# 6. ``.git`` is a file (worktree layout) — accepted
# ===========================================================================


class TestGitAsFile:
    def test_git_file_at_project_root_is_accepted(self, tmp_path: Path) -> None:
        """Git worktrees write ``.git`` as a file, not a directory.

        The helper must stat-check without caring whether ``.git`` is a
        file or a directory — otherwise every worktree checkout would be
        rejected as 'not a git repo'. Robustness matters here because
        engineers often have multiple worktrees open for long-running
        migrations.
        """
        _make_haute_toml(tmp_path)
        _make_git_file(tmp_path)
        assert get_project_root(tmp_path) == tmp_path

    def test_git_file_above_project_root_is_accepted(self, tmp_path: Path) -> None:
        """A worktree-style ``.git`` file in an ancestor also counts."""
        inner = tmp_path / "project"
        inner.mkdir()
        _make_haute_toml(inner)
        _make_git_file(tmp_path)
        assert get_project_root(inner) == inner


# ===========================================================================
# 7. Nested projects — inner wins
# ===========================================================================


class TestNestedProjects:
    def test_inner_project_wins_when_called_from_inner_subdir(self, tmp_path: Path) -> None:
        """First ``haute.toml`` walking up, not the outermost."""
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)  # shared repo at outer
        inner = tmp_path / "inner"
        inner.mkdir()
        _make_haute_toml(inner)
        sub = inner / "sub"
        sub.mkdir()
        assert get_project_root(sub) == inner

    def test_inner_project_wins_when_called_from_inner_itself(self, tmp_path: Path) -> None:
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        inner = tmp_path / "inner"
        inner.mkdir()
        _make_haute_toml(inner)
        assert get_project_root(inner) == inner

    def test_outer_project_wins_when_called_from_outer_sibling(self, tmp_path: Path) -> None:
        """Regression guard: calling from a dir outside the inner project
        walks up to the outer one, not down into the inner one."""
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        inner = tmp_path / "inner"
        inner.mkdir()
        _make_haute_toml(inner)
        sibling = tmp_path / "sibling"
        sibling.mkdir()
        assert get_project_root(sibling) == tmp_path


# ===========================================================================
# 8-11. ``is_haute_project`` predicate
# ===========================================================================


class TestIsHauteProject:
    def test_returns_true_for_valid_project(self, tmp_path: Path) -> None:
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        assert is_haute_project(tmp_path) is True

    def test_returns_false_when_no_haute_toml(self, tmp_path: Path) -> None:
        _make_git_dir(tmp_path)
        assert is_haute_project(tmp_path) is False

    def test_returns_false_when_no_git(self, tmp_path: Path) -> None:
        _make_haute_toml(tmp_path)
        assert is_haute_project(tmp_path) is False

    def test_returns_false_when_neither_present(self, tmp_path: Path) -> None:
        assert is_haute_project(tmp_path) is False

    def test_returns_true_with_git_file_worktree(self, tmp_path: Path) -> None:
        """Worktree-style ``.git`` file must be accepted, mirroring
        ``get_project_root``'s robustness requirement."""
        _make_haute_toml(tmp_path)
        _make_git_file(tmp_path)
        assert is_haute_project(tmp_path) is True

    def test_returns_false_for_subdir_of_project(self, tmp_path: Path) -> None:
        """``is_haute_project`` checks *this* path, not ancestors.

        The helper is a predicate, not a walker. Callers that want
        walk-up semantics should call ``get_project_root`` instead.
        """
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        sub = tmp_path / "src"
        sub.mkdir()
        assert is_haute_project(sub) is False

    def test_returns_bool_type_not_truthy(self, tmp_path: Path) -> None:
        """Return value must be a real ``bool`` — not a Path or None.

        Callers write ``if is_haute_project(p):`` expecting a bool;
        returning a truthy Path would work by accident but is a footgun
        for ``assert is_haute_project(p) is True`` in tests.
        """
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)
        assert is_haute_project(tmp_path) is True
        assert is_haute_project(tmp_path / "nonexistent") is False


# ===========================================================================
# 12. Non-existent start path
# ===========================================================================


class TestNonExistentStart:
    """Document the choice: a non-existent start path raises ``ConfigError``.

    Rationale: the helper's contract is 'find the Haute project root' —
    a non-existent path cannot contain a project, so the same error mode
    as 'no haute.toml found walking up' applies. We deliberately do *not*
    silently fall back to cwd, which would hide typos in CLI arguments
    (e.g. ``haute serve --project /typo``).
    """

    def test_non_existent_path_raises_config_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does" / "not" / "exist"
        assert not missing.exists()
        with pytest.raises(ConfigError):
            get_project_root(missing)

    def test_non_existent_sibling_of_real_root_still_raises(self, tmp_path: Path) -> None:
        """Even if a sibling directory *is* a Haute project, a typo'd
        path must not quietly succeed by walking up to it."""
        real = tmp_path / "real"
        real.mkdir()
        _make_haute_toml(real)
        _make_git_dir(real)
        typo = tmp_path / "realx"  # sibling, does not exist
        # Walking up from ``tmp_path/realx`` lands on ``tmp_path`` which
        # is NOT a haute project, and the walk continues upward — it
        # must not silently succeed.
        with pytest.raises(ConfigError):
            get_project_root(typo)


# ===========================================================================
# 13. Permission error walking up — must propagate
# ===========================================================================


class TestPermissionError:
    """A ``PermissionError`` from the filesystem must propagate.

    Silently swallowing it would let the walker report 'no haute.toml
    found' when the real problem is that the walker couldn't even stat
    an ancestor. That is the opposite of fail-loudly.
    """

    def test_permission_error_propagates(self, tmp_path: Path) -> None:
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)

        def _boom(self: Path) -> bool:
            raise PermissionError(f"no access to {self}")

        with patch.object(Path, "exists", _boom):
            with pytest.raises(PermissionError):
                get_project_root(tmp_path)

    def test_permission_error_is_not_wrapped_as_config_error(self, tmp_path: Path) -> None:
        """``PermissionError`` must surface as itself, not as a
        ``ConfigError`` whose message hides the underlying OS cause.

        Wrapping would obscure the diagnostic: ``ConfigError`` tells the
        user 'your project is misconfigured', but the real problem is
        that filesystem permissions are broken.
        """
        _make_haute_toml(tmp_path)
        _make_git_dir(tmp_path)

        def _boom(self: Path) -> bool:
            raise PermissionError(f"no access to {self}")

        with patch.object(Path, "exists", _boom):
            with pytest.raises(PermissionError):
                get_project_root(tmp_path)
