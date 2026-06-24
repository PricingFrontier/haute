"""Gap-fill tests for the resolver helpers in ``haute._project``.

``test_project_root.py`` covers :func:`get_project_root` and
:func:`is_haute_project`. This file targets the pipeline-resolution
helpers that those tests don't exercise:

* :func:`_toml_configured_pipeline` — the ``[project].pipeline`` reader
  and its three "return None" guards (no file, malformed TOML, missing
  or non-string ``pipeline`` key).
* :func:`_looks_like_pipeline_file` — the name/suffix reject branch and
  the ``OSError`` read guard.
* :func:`resolve_pipeline_file` and :func:`_resolve_default_in` — the
  user-facing tiers, covered enough to anchor the helpers in their real
  call paths.

Mirrors the filesystem-builder style of ``test_project_root.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from haute._project import (
    _looks_like_pipeline_file,
    _toml_configured_pipeline,
    resolve_pipeline_file,
)

_PIPELINE_BODY = "import haute\np = haute.Pipeline()\n"


# ---------------------------------------------------------------------------
# Filesystem builders — kept identical in spirit to test_project_root.py.
# ---------------------------------------------------------------------------


def _make_pipeline_file(root: Path, name: str = "main.py") -> Path:
    """Write a ``.py`` file containing ``haute.Pipeline`` and return it."""
    f = root / name
    f.write_text(_PIPELINE_BODY, encoding="utf-8")
    return f


def _make_haute_toml(root: Path, body: str) -> Path:
    toml = root / "haute.toml"
    toml.write_text(body, encoding="utf-8")
    return toml


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

    def test_returns_none_for_malformed_toml(self, tmp_path: Path) -> None:
        """Line 124: a TOML parse error is swallowed to ``None``.

        The resolver has no opinion on TOML correctness — it just can't
        use a broken file as a pipeline source. The error surfaces
        elsewhere (DeployConfig.from_toml).
        """
        _make_haute_toml(tmp_path, "this is = = not valid toml [[[\n")
        assert _toml_configured_pipeline(tmp_path) is None

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

    def test_returns_none_when_pipeline_not_a_string(self, tmp_path: Path) -> None:
        """Line 130 branch: a non-string ``pipeline`` (e.g. a number) → None."""
        _make_haute_toml(tmp_path, "[project]\npipeline = 42\n")
        assert _toml_configured_pipeline(tmp_path) is None


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
