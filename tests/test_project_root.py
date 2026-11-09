"""Tests for the Haute project-root and pipeline-resolution helpers (``haute._project``).

Contract recap:

    def get_project_root(start: Path | None = None) -> Path:
        # Walks up from ``start`` (or cwd if None) looking for a directory
        # containing ``haute.toml``. Validates it is also inside a git repo
        # (``.git`` directory OR worktree-style file anywhere at/above the
        # haute.toml root).
        # Raises ConfigError if no haute.toml found.
        # Raises ConfigError if haute.toml found but not inside a git repo.

    def is_haute_project(path: Path) -> bool:
        # True iff ``path`` contains ``haute.toml`` and is inside a git repo.

The pipeline-resolution helpers read ``[project].pipeline`` from
``haute.toml`` (failing loudly on malformed TOML), reject files that do not
look like pipelines, and resolve the default pipeline file.

Principle: **fail loudly.** No fallback to cwd, no swallowing of
permission errors while walking up.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from haute._project import (
    _looks_like_pipeline_file,
    _toml_configured_pipeline,
    get_project_root,
    is_haute_project,
    resolve_pipeline_file,
)
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


_PIPELINE_BODY = "import haute\np = haute.Pipeline()\n"


# ---------------------------------------------------------------------------
# Filesystem builders — kept identical in spirit to test_project_root.py.
# ---------------------------------------------------------------------------


def _make_pipeline_file(root: Path, name: str = "main.py") -> Path:
    """Write a ``.py`` file containing ``haute.Pipeline`` and return it."""
    f = root / name
    f.write_text(_PIPELINE_BODY, encoding="utf-8")
    return f


# ===========================================================================
# 1. _toml_configured_pipeline
# ===========================================================================


class TestTomlConfiguredPipeline:
    def test_returns_none_when_no_toml(self, tmp_path: Path) -> None:
        """No ``haute.toml`` → the resolver falls through to discovery."""
        assert _toml_configured_pipeline(tmp_path) is None

    def test_returns_configured_path_unresolved(self, tmp_path: Path) -> None:
        """A valid ``[project].pipeline`` returns ``root / <value>``.

        The path is intentionally unresolved — existence is the caller's
        job — so we only assert it points at the configured location.
        """
        _make_haute_toml(tmp_path, '[project]\npipeline = "rating/main.py"\n')
        result = _toml_configured_pipeline(tmp_path)
        assert result == tmp_path / "rating/main.py"

    @pytest.mark.parametrize("pipeline", ["../outside/main.py", "OUTSIDE"])
    def test_a_pipeline_outside_the_project_is_refused(self, tmp_path: Path, pipeline: str) -> None:
        """An absolute or escaping ``[project].pipeline`` would bind a file outside it."""
        root = tmp_path / "project"
        root.mkdir()
        outside = tmp_path / "outside" / "main.py"
        value = outside.as_posix() if pipeline == "OUTSIDE" else pipeline
        _make_haute_toml(root, f'[project]\npipeline = "{value}"\n')
        with pytest.raises(ConfigError, match="inside the project"):
            _toml_configured_pipeline(root)

    def test_malformed_toml_raises_config_error(self, tmp_path: Path) -> None:
        """Malformed tier-one config must not silently fall through to discovery."""
        _make_haute_toml(tmp_path, "this is = = not valid toml [[[\n")
        with pytest.raises(ConfigError, match="Could not read haute.toml"):
            _toml_configured_pipeline(tmp_path)

    def test_returns_none_when_no_project_table(self, tmp_path: Path) -> None:
        """Line 128/131: well-formed TOML with no ``[project].pipeline``."""
        _make_haute_toml(tmp_path, '[other]\nname = "x"\n')
        assert _toml_configured_pipeline(tmp_path) is None

    def test_returns_none_when_pipeline_missing(self, tmp_path: Path) -> None:
        """``[project]`` present but no ``pipeline`` key → None."""
        _make_haute_toml(tmp_path, '[project]\nname = "x"\n')
        assert _toml_configured_pipeline(tmp_path) is None

    def test_returns_none_when_pipeline_empty_string(self, tmp_path: Path) -> None:
        """Line 131 branch: an empty ``pipeline`` string is rejected."""
        _make_haute_toml(tmp_path, '[project]\npipeline = ""\n')
        assert _toml_configured_pipeline(tmp_path) is None

    def test_non_string_pipeline_raises_config_error(self, tmp_path: Path) -> None:
        """A configured value that is not a path is a broken tier one: it must
        not fall through to discovery and bind a different pipeline."""
        _make_haute_toml(tmp_path, "[project]\npipeline = 42\n")
        with pytest.raises(ConfigError, match="must be a path string"):
            _toml_configured_pipeline(tmp_path)


# ===========================================================================
# 2. _looks_like_pipeline_file
# ===========================================================================


class TestLooksLikePipelineFile:
    def test_true_for_py_with_pipeline_marker(self, tmp_path: Path) -> None:
        f = _make_pipeline_file(tmp_path, "model.py")
        assert _looks_like_pipeline_file(f) is True

    def test_false_for_py_without_marker(self, tmp_path: Path) -> None:
        f = tmp_path / "helper.py"
        f.write_text("x = 1\n", encoding="utf-8")
        assert _looks_like_pipeline_file(f) is False

    def test_false_for_excluded_name(self, tmp_path: Path) -> None:
        """Line 159 branch: ``__init__.py`` is never a pipeline entry point
        even when it contains the marker."""
        f = _make_pipeline_file(tmp_path, "__init__.py")
        assert _looks_like_pipeline_file(f) is False

    def test_false_for_non_py_suffix(self, tmp_path: Path) -> None:
        """Line 158/159: a non-``.py`` file is rejected before reading."""
        f = tmp_path / "config.toml"
        f.write_text(_PIPELINE_BODY, encoding="utf-8")
        assert _looks_like_pipeline_file(f) is False

    def test_false_when_read_raises_oserror(self, tmp_path: Path) -> None:
        """Lines 162-163: an unreadable ``.py`` file is treated as 'not a
        pipeline' rather than crashing resolution for the directory."""
        f = tmp_path / "broken.py"
        f.write_text(_PIPELINE_BODY, encoding="utf-8")

        def _boom(self: Path, *args: object, **kwargs: object) -> str:
            raise OSError("permission denied")

        with patch.object(Path, "read_text", _boom):
            assert _looks_like_pipeline_file(f) is False


# ===========================================================================
# 3. resolve_pipeline_file — anchors the helpers in their call paths
# ===========================================================================


class TestResolvePipelineFile:
    def test_existing_file_resolved_to_absolute(self, tmp_path: Path) -> None:
        f = _make_pipeline_file(tmp_path, "main.py")
        assert resolve_pipeline_file(f) == f.resolve()

    def test_missing_file_raises_filenotfound(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.py"
        with pytest.raises(FileNotFoundError):
            resolve_pipeline_file(missing)

    def test_directory_uses_toml_configured_pipeline(self, tmp_path: Path) -> None:
        """A directory argument runs the fallback chain; a valid
        ``[project].pipeline`` wins."""
        _make_haute_toml(tmp_path, '[project]\npipeline = "rating.py"\n')
        f = _make_pipeline_file(tmp_path, "rating.py")
        assert resolve_pipeline_file(tmp_path) == f.resolve()

    def test_directory_toml_points_at_missing_file_raises(self, tmp_path: Path) -> None:
        """A configured path that doesn't exist raises rather than
        silently falling through to discovery."""
        _make_haute_toml(tmp_path, '[project]\npipeline = "ghost.py"\n')
        with pytest.raises(FileNotFoundError):
            resolve_pipeline_file(tmp_path)

    def test_directory_main_py_convention(self, tmp_path: Path) -> None:
        """No TOML config → ``main.py`` wins over discovery."""
        f = _make_pipeline_file(tmp_path, "main.py")
        assert resolve_pipeline_file(tmp_path) == f.resolve()

    def test_directory_single_discovery(self, tmp_path: Path) -> None:
        """Exactly one ``.py`` with the marker and no ``main.py`` → pick it."""
        f = _make_pipeline_file(tmp_path, "motor.py")
        assert resolve_pipeline_file(tmp_path) == f.resolve()

    def test_directory_ambiguous_raises_naming_candidates(self, tmp_path: Path) -> None:
        _make_pipeline_file(tmp_path, "motor.py")
        _make_pipeline_file(tmp_path, "home.py")
        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_pipeline_file(tmp_path)
        msg = str(exc_info.value)
        assert "motor.py" in msg and "home.py" in msg

    def test_directory_empty_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            resolve_pipeline_file(tmp_path)
