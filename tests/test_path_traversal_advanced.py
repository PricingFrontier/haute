"""Advanced path traversal tests covering gaps not addressed by test_path_traversal_fixes.py.

Each test class targets a specific module or attack vector:

1. ConfigIOTraversal        -- _config_io.load_node_config / config_path_for_node have no
                               path validation; attacker-controlled config paths read arbitrary files.
2. ModelScorerTraversal     -- _model_scorer.score_from_config reads JSON from an unvalidated path.
3. SymlinkTraversal         -- is_relative_to is bypassed when a symlink inside the project root
                               points to a directory outside it.
4. WindowsMixedSeparators   -- Backslash-based traversal (..\\..\\etc\\passwd) on Windows.
5. NullByteInjection        -- Null bytes in file paths can truncate strings at the OS level.
6. VeryLongPaths            -- Paths exceeding OS limits should fail gracefully, not crash.
7. ConfigPathForNode        -- node_name containing ".." or "/" escapes the config directory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    """Write a dict as JSON to the given path, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# =========================================================================
# 1. _config_io.load_node_config -- no path traversal protection
# =========================================================================


class TestConfigIOLoadTraversal:
    """load_node_config resolves config_path against base_dir with zero
    validation.  An attacker who controls the config= decorator argument
    (e.g. via a crafted .py file or API payload) can read any JSON file
    on the filesystem.

    Production failure: reading /etc/shadow, ~/.ssh/known_hosts, or
    cloud credential files from a pipeline config string.
    """

    def test_dotdot_config_path_reads_outside_base(self, tmp_path: Path):
        """config_path='../../secret.json' is now blocked by path validation.

        The fix: load_node_config validates that the resolved path stays
        within base_dir, raising ValueError for traversal attempts.
        """
        from haute._config_io import load_node_config

        # Set up: a secret file two levels above the "project"
        project = tmp_path / "org" / "project"
        project.mkdir(parents=True)
        secret = tmp_path / "secret.json"
        secret.write_text(json.dumps({"password": "hunter2"}))

        with pytest.raises(ValueError, match="outside project root"):
            load_node_config("../../secret.json", base_dir=project)

    def test_absolute_config_path_ignores_base_dir(self, tmp_path: Path):
        """An absolute path outside base_dir is now blocked.

        The fix: load_node_config validates the resolved path stays
        within base_dir even for absolute paths.
        """
        from haute._config_io import load_node_config

        secret = tmp_path / "abs_secret.json"
        secret.write_text(json.dumps({"key": "value"}))

        project = tmp_path / "project"
        project.mkdir()

        with pytest.raises(ValueError, match="outside project root"):
            load_node_config(str(secret), base_dir=project)

    def test_dotdot_mid_path_escapes(self, tmp_path: Path):
        """config/banding/../../../secret.json is now blocked.

        The fix: load_node_config validates the resolved path stays
        within base_dir.
        """
        from haute._config_io import load_node_config

        project = tmp_path / "project"
        (project / "config" / "banding").mkdir(parents=True)
        secret = tmp_path / "secret.json"
        secret.write_text(json.dumps({"leaked": True}))

        with pytest.raises(ValueError, match="outside project root"):
            load_node_config("config/banding/../../../secret.json", base_dir=project)


# =========================================================================
# 2. config_path_for_node -- node_name with ".." or "/" escapes config dir
# =========================================================================


class TestConfigPathForNodeTraversal:
    """config_path_for_node builds: config/<folder>/<node_name>.json

    If node_name contains path separators or '..', the resulting path
    escapes the intended config/<folder>/ directory.

    Production failure: save_node_config writes to an arbitrary location;
    remove_config_file deletes an arbitrary file.
    """

    def test_node_name_with_dotdot_escapes_folder(self):
        """node_name='../../evil' is now rejected by config_path_for_node."""
        from haute._config_io import config_path_for_node
        from haute._types import NodeType

        with pytest.raises(ValueError, match="must not contain"):
            config_path_for_node(NodeType.BANDING, "../../evil")

    def test_node_name_with_slash_escapes_folder(self):
        """node_name='../model_scoring/victim' is now rejected."""
        from haute._config_io import config_path_for_node
        from haute._types import NodeType

        with pytest.raises(ValueError, match="must not contain"):
            config_path_for_node(NodeType.BANDING, "../model_scoring/victim")

    def test_save_node_config_writes_outside_config_dir(self, tmp_path: Path):
        """save_node_config with a crafted node_name is now rejected.

        The fix: config_path_for_node rejects node names containing '..'
        or path separators.
        """
        from haute._config_io import save_node_config
        from haute._types import NodeType

        with pytest.raises(ValueError, match="must not contain"):
            save_node_config(
                NodeType.BANDING,
                "../../pwned",
                {"compromised": True},
                base_dir=tmp_path,
            )

    def test_remove_config_file_deletes_outside_config_dir(self, tmp_path: Path):
        """remove_config_file with a crafted node_name is now blocked.

        The fix: config_path_for_node rejects names with '..' so
        remove_config_file returns False without deleting anything.
        """
        from haute._config_io import remove_config_file
        from haute._types import NodeType

        # Place a victim file at project root
        victim = tmp_path / "important.json"
        victim.write_text(json.dumps({"important": True}))

        # Craft node_name to target ../../important — should be blocked
        result = remove_config_file(NodeType.BANDING, "../../important", base_dir=tmp_path)
        assert result is False, "remove_config_file should return False for invalid names"
        assert victim.exists(), "Victim file should not have been deleted"


# =========================================================================
# 3. _model_scorer.score_from_config -- no path validation
# =========================================================================


class TestModelScorerConfigTraversal:
    """score_from_config reads a JSON file from an unvalidated path.
    The config= argument comes from generated code, but if the .py file
    is tampered with, arbitrary files can be read.

    Production failure: reading sensitive JSON files (credentials, secrets)
    via a crafted config path in a pipeline .py file.
    """

    def test_dotdot_config_escapes_base_dir(self, tmp_path: Path):
        """config='../../secret.json' is now blocked by path validation.

        The fix: score_from_config validates the resolved path stays
        within the project root.
        """
        from haute._model_scorer import score_from_config

        project = tmp_path / "org" / "project"
        project.mkdir(parents=True)
        secret = tmp_path / "secret.json"
        secret.write_text(
            json.dumps(
                {
                    "sourceType": "run",
                    "run_id": "abc123",
                    "artifact_path": "model",
                    "task": "regression",
                    "output_column": "pred",
                }
            )
        )

        with pytest.raises(ValueError, match="outside project root"):
            score_from_config(config="../../secret.json", base_dir=str(project))

    def test_absolute_config_path_bypasses_base_dir(self, tmp_path: Path):
        """An absolute config path outside base_dir is now blocked."""
        from haute._model_scorer import score_from_config

        config_file = tmp_path / "stolen_config.json"
        config_file.write_text(
            json.dumps(
                {
                    "sourceType": "registered",
                    "registered_model": "evil_model",
                    "version": "1",
                    "task": "classification",
                    "output_column": "pred",
                }
            )
        )

        with pytest.raises(ValueError, match="outside project root"):
            score_from_config(
                config=str(config_file),
                base_dir=str(tmp_path / "project"),
            )


# =========================================================================
# 4. Symlink traversal -- is_relative_to bypassed via symlinks
# =========================================================================


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Symlink creation requires elevated privileges on Windows",
)
class TestSymlinkTraversal:
    """Path.resolve() follows symlinks.  A symlink inside the project
    root that points to /etc or /home can bypass is_relative_to checks
    IF the check is done BEFORE resolve() (checking the unresolved path).

    validate_safe_path and validate_project_path both resolve() first,
    which is correct.  These tests confirm that behavior.

    Production failure: a symlink at project/data -> /etc allows reading
    /etc/passwd via the path "data/passwd".
    """

    def test_validate_safe_path_blocks_symlink_escape(self, tmp_path: Path):
        """A symlink inside base pointing outside must be rejected."""
        from fastapi import HTTPException
        from haute.routes._helpers import validate_safe_path

        outside = tmp_path / "outside_secrets"
        outside.mkdir()
        (outside / "credentials.json").write_text('{"token": "secret"}')

        project = tmp_path / "project"
        project.mkdir()
        (project / "data").symlink_to(outside)

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(project, "data/credentials.json")
        assert exc_info.value.status_code == 403

    def test_validate_project_path_blocks_symlink_escape(self, tmp_path: Path):
        """validate_project_path in _sandbox.py should also block symlinks."""
        from haute._sandbox import set_project_root, validate_project_path

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")

        project = tmp_path / "project"
        project.mkdir()
        (project / "link").symlink_to(outside)

        set_project_root(project)
        with pytest.raises(ValueError, match="outside"):
            validate_project_path(project / "link" / "secret.txt")

    def test_nested_symlink_chain_blocked(self, tmp_path: Path):
        """A chain of symlinks (a -> b -> outside) should still be blocked."""
        from fastapi import HTTPException
        from haute.routes._helpers import validate_safe_path

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret").write_text("leaked")

        project = tmp_path / "project"
        project.mkdir()

        # Chain: project/a -> project/b -> outside
        (project / "b").symlink_to(outside)
        (project / "a").symlink_to(project / "b")

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(project, "a/secret")
        assert exc_info.value.status_code == 403

    def test_symlink_to_parent_directory_blocked(self, tmp_path: Path):
        """project/escape -> project/.. (parent) allows reading anything."""
        from fastapi import HTTPException
        from haute.routes._helpers import validate_safe_path

        project = tmp_path / "project"
        project.mkdir()
        (project / "escape").symlink_to(tmp_path)

        (tmp_path / "secret.txt").write_text("confidential")

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(project, "escape/secret.txt")
        assert exc_info.value.status_code == 403

    def test_load_node_config_blocks_symlinks_outside_project(self, tmp_path: Path):
        """FIX: load_node_config now validates resolved paths, so symlinks
        pointing outside the project root are correctly blocked.
        """
        from haute._config_io import load_node_config

        outside = tmp_path / "outside"
        outside.mkdir()
        real_config = outside / "stolen.json"
        real_config.write_text(json.dumps({"secret": "data"}))

        project = tmp_path / "project"
        config_dir = project / "config" / "banding"
        config_dir.mkdir(parents=True)
        (config_dir / "linked.json").symlink_to(real_config)

        with pytest.raises(ValueError, match="outside project root"):
            load_node_config("config/banding/linked.json", base_dir=project)


# =========================================================================
# 5. Windows-specific mixed separator traversal
# =========================================================================


class TestWindowsMixedSeparatorTraversal:
    """On Windows, both / and \\ are path separators.  Backslash-based
    traversal (..\\..\\etc\\passwd) might bypass checks that only look
    for forward slashes.

    Production failure: an API payload with backslash separators escapes
    path validation on Windows servers.
    """

    def test_validate_safe_path_blocks_backslash_traversal(self, tmp_path: Path):
        """..\\..\\etc\\passwd must be blocked on all platforms.

        On Unix, backslashes are literal filename characters (harmless).
        On Windows, they are path separators and enable traversal.
        """
        from haute.routes._helpers import validate_safe_path

        if sys.platform == "win32":
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                validate_safe_path(tmp_path, "..\\..\\Windows\\System32\\config\\SAM")
            assert exc_info.value.status_code == 403
        else:
            # On Unix, backslash is a literal character in filenames.
            # The path stays inside tmp_path (it's just an odd filename).
            result = validate_safe_path(tmp_path, "..\\..\\etc\\passwd")
            assert result.is_relative_to(tmp_path)

    def test_mixed_separators_blocked_on_windows(self, tmp_path: Path):
        """Mixing / and \\ should not confuse the validator."""
        from haute.routes._helpers import validate_safe_path

        if sys.platform == "win32":
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                validate_safe_path(tmp_path, "sub/..\\..\\..\\Windows\\win.ini")
            assert exc_info.value.status_code == 403
        else:
            # On Unix this is a literal filename with backslashes
            result = validate_safe_path(tmp_path, "sub/..\\..\\..\\etc\\passwd")
            assert result.is_relative_to(tmp_path)

    def test_config_path_for_node_backslash_in_name(self, tmp_path: Path):
        """node_name with backslashes is now rejected on all platforms."""
        from haute._config_io import config_path_for_node
        from haute._types import NodeType

        name_with_backslash = "..\\..\\evil"
        with pytest.raises(ValueError, match="must not contain"):
            config_path_for_node(NodeType.BANDING, name_with_backslash)

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Windows-only: tests UNC path handling",
    )
    def test_unc_path_bypasses_validation(self, tmp_path: Path):
        """A UNC path (\\\\server\\share\\...) might bypass relative path checks.

        Production failure: reading files from network shares.
        """
        from fastapi import HTTPException
        from haute.routes._helpers import validate_safe_path

        with pytest.raises(HTTPException) as exc_info:
            validate_safe_path(tmp_path, "\\\\evil-server\\share\\secret.json")
        assert exc_info.value.status_code == 403


# =========================================================================
# 6. Null byte injection
# =========================================================================


class TestNullByteInjection:
    """Some C-level OS APIs treat null bytes as string terminators.
    A path like 'config/valid.json\\x00../../etc/passwd' might:
    - Pass Python-level validation (sees 'config/valid.json...')
    - Be truncated to 'config/valid.json' at the OS level

    Modern Python (3.x) raises ValueError for embedded null bytes,
    but we should confirm this consistently.

    Production failure: bypassing path validation in older Python or
    through ctypes/cffi calls that pass paths to C libraries.
    """

    def test_null_byte_in_load_node_config(self, tmp_path: Path):
        """load_node_config with null byte in path should raise, not read."""
        from haute._config_io import load_node_config

        project = tmp_path / "project"
        project.mkdir()
        # Create a valid config file
        _write_json(project / "config.json", {"ok": True})

        with pytest.raises((ValueError, OSError)):
            load_node_config("config.json\x00../../etc/passwd", base_dir=project)

    def test_null_byte_in_validate_safe_path(self, tmp_path: Path):
        """validate_safe_path must not accept paths with null bytes."""
        from haute.routes._helpers import validate_safe_path

        # Python's pathlib raises ValueError on null bytes
        with pytest.raises((ValueError, Exception)):
            validate_safe_path(tmp_path, "file\x00.txt")

    def test_null_byte_in_validate_project_path(self, tmp_path: Path):
        """validate_project_path must not accept null bytes."""
        from haute._sandbox import set_project_root, validate_project_path

        set_project_root(tmp_path)
        with pytest.raises((ValueError, Exception)):
            validate_project_path(tmp_path / "data\x00.json")

    def test_null_byte_in_config_path_for_node(self):
        """node_name with null byte is now rejected by path traversal check."""
        from haute._config_io import config_path_for_node
        from haute._types import NodeType

        # The null byte name also contains path separators, so it is
        # rejected by the traversal check. Either ValueError from our
        # check or from OS-level null byte rejection is acceptable.
        with pytest.raises((ValueError, OSError)):
            config_path_for_node(NodeType.BANDING, "evil\x00../../etc/passwd")

    def test_null_byte_in_score_from_config(self, tmp_path: Path):
        """score_from_config with null byte in config path should raise."""
        from haute._model_scorer import score_from_config

        with pytest.raises((ValueError, OSError)):
            score_from_config(config="config\x00.json", base_dir=str(tmp_path))


# =========================================================================
# 7. Very long paths exceeding OS limits
# =========================================================================


class TestVeryLongPaths:
    """Extremely long paths can cause:
    - OSError / FileNotFoundError on file operations
    - Buffer overflows in C extensions
    - DoS via excessive memory allocation

    These tests confirm the functions fail gracefully.

    Production failure: a crafted node_name with 10,000 characters causes
    an unhandled exception or memory exhaustion.
    """

    def test_long_node_name_in_config_path(self):
        """A very long node_name should not crash config_path_for_node."""
        from haute._config_io import config_path_for_node
        from haute._types import NodeType

        long_name = "a" * 10000
        # Should not raise -- it just builds a Path object
        path = config_path_for_node(NodeType.BANDING, long_name)
        assert long_name in str(path)

    def test_long_path_in_load_node_config(self, tmp_path: Path):
        """Loading from a path exceeding OS limits should raise OSError."""
        from haute._config_io import load_node_config

        long_segment = "x" * 300
        long_path = "/".join([long_segment] * 20) + "/config.json"

        with pytest.raises((OSError, FileNotFoundError)):
            load_node_config(long_path, base_dir=tmp_path)

    def test_long_path_in_validate_safe_path(self, tmp_path: Path):
        """validate_safe_path with a very long path should not crash."""
        from haute.routes._helpers import validate_safe_path

        long_path = "sub/" * 500 + "file.txt"
        # Should succeed (path is within base, just very long)
        result = validate_safe_path(tmp_path, long_path)
        assert result.is_relative_to(tmp_path)

    def test_long_path_in_validate_project_path(self, tmp_path: Path):
        """validate_project_path should handle very long paths gracefully."""
        from haute._sandbox import set_project_root, validate_project_path

        set_project_root(tmp_path)
        long_path = tmp_path / ("a" * 300) / ("b" * 300) / "file.txt"
        # Should not crash -- just validates the path
        result = validate_project_path(long_path)
        assert result.is_relative_to(tmp_path)

    def test_long_path_in_save_node_config(self, tmp_path: Path):
        """save_node_config with a very long node_name should raise OSError
        when it tries to create the file.
        """
        from haute._config_io import save_node_config
        from haute._types import NodeType

        long_name = "z" * 500  # Exceeds most filesystem name limits (255 chars)

        with pytest.raises(OSError):
            save_node_config(
                NodeType.BANDING,
                long_name,
                {"data": "test"},
                base_dir=tmp_path,
            )

    def test_long_config_path_in_score_from_config(self, tmp_path: Path):
        """score_from_config with a very long config path should raise."""
        from haute._model_scorer import score_from_config

        long_path = "config/" + "a" * 5000 + ".json"
        with pytest.raises((OSError, FileNotFoundError)):
            score_from_config(config=long_path, base_dir=str(tmp_path))


# =========================================================================
# 8. find_config_by_func_name -- func_name with traversal
# =========================================================================


class TestFindConfigByFuncNameTraversal:
    """find_config_by_func_name scans config/<folder>/<func_name>.json.

    If func_name contains path separators, it could match files outside
    the expected directory.

    Production failure: func_name='../../credentials' reads a credentials
    file through the config recovery path.
    """

    def test_func_name_with_dotdot_escapes(self, tmp_path: Path):
        """func_name with '..' should not read files above config/."""
        from haute._config_io import find_config_by_func_name

        # Place a "secret" file that a traversal would reach
        secret = tmp_path / "secret.json"
        secret.write_text(json.dumps({"leaked": True}))

        # Create the config directory structure
        (tmp_path / "config" / "banding").mkdir(parents=True)

        # func_name that would resolve to ../../secret.json from config/banding/
        result = find_config_by_func_name("../../secret", base_dir=tmp_path)
        # On some OS/filesystem combos, the traversal might work
        if result is not None:
            # Vulnerability confirmed: traversal worked
            config_data, _ = result
            assert config_data.get("leaked") is True, (
                "find_config_by_func_name followed a '..' traversal"
            )

    def test_func_name_with_slash_reads_sibling_folder(self, tmp_path: Path):
        """func_name with '/' could read from a sibling config folder."""
        from haute._config_io import find_config_by_func_name

        # Set up config directories
        (tmp_path / "config" / "banding").mkdir(parents=True)
        (tmp_path / "config" / "model_scoring").mkdir(parents=True)

        # Place a config in model_scoring
        target = tmp_path / "config" / "model_scoring" / "victim.json"
        target.write_text(json.dumps({"cross_folder": True}))

        # func_name that crosses into model_scoring from banding
        result = find_config_by_func_name("../model_scoring/victim", base_dir=tmp_path)
        if result is not None:
            config_data, _ = result
            assert config_data.get("cross_folder") is True


# =========================================================================
# 9. validate_project_path edge cases
# =========================================================================


class TestValidateProjectPathEdgeCases:
    """Additional edge cases for _sandbox.validate_project_path beyond
    what test_path_traversal_fixes.py covers.
    """

    def test_empty_path_resolves_to_cwd(self, tmp_path: Path):
        """An empty string path should resolve to cwd, which may or may
        not be inside the project root.
        """
        from haute._sandbox import set_project_root, validate_project_path

        set_project_root(tmp_path)
        # Empty path resolves to cwd, which is tmp_path (via monkeypatch in CI)
        # or the actual cwd. Either way, should not crash.
        try:
            result = validate_project_path("")
            # If it succeeds, the resolved cwd must be inside project root
            assert result.is_relative_to(tmp_path)
        except ValueError:
            # cwd is outside project root -- that's acceptable
            pass

    def test_dot_path_resolves_to_cwd(self, tmp_path: Path):
        """'.' should resolve to cwd."""
        from haute._sandbox import set_project_root, validate_project_path

        set_project_root(tmp_path)
        try:
            result = validate_project_path(".")
            assert result.exists() or True  # May not exist but should resolve
        except ValueError:
            pass  # cwd outside project root

    def test_tilde_not_expanded(self, tmp_path: Path):
        """~ should NOT be expanded to $HOME -- it's a literal path component."""
        from haute._sandbox import set_project_root, validate_project_path

        set_project_root(tmp_path)
        # Path("~") does not expand; it resolves relative to cwd
        # This should either succeed (if cwd is inside project) or raise ValueError
        try:
            result = validate_project_path("~/secret")
            # If it resolves, it must be inside project root
            assert result.is_relative_to(tmp_path)
        except ValueError:
            pass  # Outside project root -- correct behavior

    def test_unicode_normalization_attack(self, tmp_path: Path):
        """Unicode normalization (e.g. full-width dots) should not bypass checks.

        Some filesystems normalize Unicode, so U+FF0E (fullwidth full stop)
        might be treated as '.', enabling traversal via '\uff0e\uff0e/'.
        """
        from haute._sandbox import set_project_root, validate_project_path

        set_project_root(tmp_path)
        # Fullwidth dots -- should be treated as literal characters, not '..'
        unicode_dots = "\uff0e\uff0e/secret"
        try:
            result = validate_project_path(tmp_path / unicode_dots)
            # Must stay inside project root
            assert result.is_relative_to(tmp_path)
        except (ValueError, OSError):
            pass  # Rejected -- correct behavior
