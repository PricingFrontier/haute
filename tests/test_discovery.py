"""Tests for haute.discovery - pipeline file discovery."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from haute.discovery import _configured_pipeline, discover_pipelines
from haute.errors import ConfigError

PIPELINE_CONTENT = """\
import haute

pipeline = haute.Pipeline("test")
"""

NO_PIPELINE_CONTENT = """\
import os
print("hello")
"""


# ---------------------------------------------------------------------------
# _configured_pipeline
# ---------------------------------------------------------------------------


class TestConfiguredPipeline:
    def test_returns_none_when_toml_missing(self, tmp_path: Path) -> None:
        assert _configured_pipeline(tmp_path) is None

    def test_returns_path_when_pipeline_configured(self, tmp_path: Path) -> None:
        toml = tmp_path / "haute.toml"
        toml.write_text('[project]\npipeline = "main.py"\n')
        result = _configured_pipeline(tmp_path)
        assert result == tmp_path / "main.py"

    def test_returns_none_when_project_section_missing(self, tmp_path: Path) -> None:
        toml = tmp_path / "haute.toml"
        toml.write_text('[other]\nkey = "value"\n')
        assert _configured_pipeline(tmp_path) is None

    def test_returns_none_when_pipeline_key_missing(self, tmp_path: Path) -> None:
        toml = tmp_path / "haute.toml"
        toml.write_text('[project]\nname = "foo"\n')
        assert _configured_pipeline(tmp_path) is None

    def test_toml_parse_error_fails_loud(self, tmp_path: Path) -> None:
        toml = tmp_path / "haute.toml"
        toml.write_text("this is not valid toml [[[")
        with pytest.raises(ConfigError, match="haute.toml"):
            _configured_pipeline(tmp_path)

    def test_returns_subdirectory_path(self, tmp_path: Path) -> None:
        toml = tmp_path / "haute.toml"
        toml.write_text('[project]\npipeline = "src/pipeline.py"\n')
        result = _configured_pipeline(tmp_path)
        assert result == tmp_path / "src" / "pipeline.py"


# ---------------------------------------------------------------------------
# discover_pipelines - empty / basic
# ---------------------------------------------------------------------------


class TestDiscoverPipelinesEmpty:
    def test_no_py_files_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        assert discover_pipelines(tmp_path) == []

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        result = discover_pipelines(tmp_path)
        assert result == []
        assert isinstance(result, list)

    def test_empty_py_file_not_discovered(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "empty.py").write_text("")
        assert discover_pipelines(tmp_path) == []


# ---------------------------------------------------------------------------
# discover_pipelines - single and multiple
# ---------------------------------------------------------------------------


class TestDiscoverPipelinesSingleAndMultiple:
    def test_single_pipeline_in_root(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "pipeline.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(tmp_path)
        assert len(result) == 1
        assert result[0] == tmp_path / "pipeline.py"

    def test_multiple_pipelines_sorted(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "zeta.py").write_text(PIPELINE_CONTENT)
        (tmp_path / "alpha.py").write_text(PIPELINE_CONTENT)
        (tmp_path / "mid.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(tmp_path)
        names = [p.name for p in result]
        assert names == ["alpha.py", "mid.py", "zeta.py"]

    def test_returns_list_of_paths(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "pipe.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(tmp_path)
        assert all(isinstance(p, Path) for p in result)


# ---------------------------------------------------------------------------
# discover_pipelines - configured pipeline precedence
# ---------------------------------------------------------------------------


class TestConfiguredPipelinePrecedence:
    def test_configured_pipeline_listed_first(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "zz_last.py"\n')
        (tmp_path / "zz_last.py").write_text(PIPELINE_CONTENT)
        (tmp_path / "aa_first.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(tmp_path)
        assert result[0] == tmp_path / "zz_last.py"
        assert result[1] == tmp_path / "aa_first.py"

    def test_configured_pipeline_without_haute_pipeline_string(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "nope.py"\n')
        (tmp_path / "nope.py").write_text(NO_PIPELINE_CONTENT)
        (tmp_path / "real.py").write_text(PIPELINE_CONTENT)
        with pytest.raises(FileNotFoundError, match="nope.py"):
            discover_pipelines(tmp_path)

    def test_configured_pipeline_file_missing(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "missing.py"\n')
        (tmp_path / "real.py").write_text(PIPELINE_CONTENT)
        with pytest.raises(FileNotFoundError, match="missing.py"):
            discover_pipelines(tmp_path)


# ---------------------------------------------------------------------------
# discover_pipelines - skip files
# ---------------------------------------------------------------------------


class TestSkipFiles:
    @pytest.mark.parametrize("name", ["__init__.py", "setup.py", "conftest.py"])
    def test_skip_special_files(self, tmp_path: Path, name: str) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / name).write_text(PIPELINE_CONTENT)
        assert discover_pipelines(tmp_path) == []

    def test_skip_files_but_discover_others(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "__init__.py").write_text(PIPELINE_CONTENT)
        (tmp_path / "setup.py").write_text(PIPELINE_CONTENT)
        (tmp_path / "real.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(tmp_path)
        assert len(result) == 1
        assert result[0].name == "real.py"


# ---------------------------------------------------------------------------
# discover_pipelines - content matching
# ---------------------------------------------------------------------------


class TestContentMatching:
    def test_file_with_pipeline_string_discovered(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "good.py").write_text(PIPELINE_CONTENT)
        assert len(discover_pipelines(tmp_path)) == 1

    def test_file_without_pipeline_string_excluded(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "bad.py").write_text(NO_PIPELINE_CONTENT)
        assert discover_pipelines(tmp_path) == []

    def test_pipeline_string_in_comment_still_discovered(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        content = "# This uses haute.Pipeline to build stuff\n"
        (tmp_path / "commented.py").write_text(content)
        result = discover_pipelines(tmp_path)
        assert len(result) == 1
        assert result[0].name == "commented.py"

    def test_pipeline_string_in_docstring_discovered(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        content = '"""Module that wraps haute.Pipeline."""\n'
        (tmp_path / "doc.py").write_text(content)
        assert len(discover_pipelines(tmp_path)) == 1


# ---------------------------------------------------------------------------
# discover_pipelines - error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_toml_parse_error_does_not_bind_a_different_file(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text("not valid toml [[[")
        (tmp_path / "pipe.py").write_text(PIPELINE_CONTENT)
        with pytest.raises(ConfigError, match="haute.toml"):
            discover_pipelines(tmp_path)


# ---------------------------------------------------------------------------
# discover_pipelines - root parameter
# ---------------------------------------------------------------------------


class TestRootParameter:
    def test_none_root_uses_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        (tmp_path / "pipe.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(None)
        assert len(result) == 1
        assert result[0].name == "pipe.py"

    def test_custom_root_searches_that_directory(self, tmp_path: Path) -> None:
        subdir = tmp_path / "myproject"
        subdir.mkdir()
        (subdir / "haute.toml").write_text('[project]\nname = "x"\n')
        (subdir / "pipe.py").write_text(PIPELINE_CONTENT)
        (tmp_path / "decoy.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(subdir)
        assert len(result) == 1
        assert result[0].parent == subdir


# ---------------------------------------------------------------------------
# discover_pipelines - symlinks and deduplication
# ---------------------------------------------------------------------------


class TestSymlinksAndDedup:
    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_symlink_to_pipeline_resolved(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        real = tmp_path / "real.py"
        real.write_text(PIPELINE_CONTENT)
        link = tmp_path / "link.py"
        link.symlink_to(real)
        result = discover_pipelines(tmp_path)
        assert any(p.name == "link.py" or p.name == "real.py" for p in result)

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_duplicate_via_configured_and_glob_deduplicated(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "pipe.py"\n')
        (tmp_path / "pipe.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(tmp_path)
        assert len(result) == 1

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_symlink_configured_deduplicates_with_real(self, tmp_path: Path) -> None:
        real = tmp_path / "real.py"
        real.write_text(PIPELINE_CONTENT)
        link = tmp_path / "configured.py"
        link.symlink_to(real)
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "configured.py"\n')
        result = discover_pipelines(tmp_path)
        resolved_paths = [p.resolve() for p in result]
        assert len(set(resolved_paths)) == len(resolved_paths)

    def test_configured_same_as_glob_no_duplicate(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\npipeline = "pipe.py"\n')
        (tmp_path / "pipe.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(tmp_path)
        assert len(result) == 1
        assert result[0] == tmp_path / "pipe.py"


# ---------------------------------------------------------------------------
# discover_pipelines - haute.toml edge cases
# ---------------------------------------------------------------------------


class TestTomlEdgeCases:
    def test_haute_toml_missing_project_section(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[other]\nfoo = "bar"\n')
        (tmp_path / "pipe.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(tmp_path)
        assert len(result) == 1

    def test_haute_toml_missing_pipeline_key(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "foo"\n')
        (tmp_path / "pipe.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(tmp_path)
        assert len(result) == 1

    def test_haute_toml_empty_pipeline_value(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\npipeline = ""\n')
        (tmp_path / "pipe.py").write_text(PIPELINE_CONTENT)
        result = discover_pipelines(tmp_path)
        assert len(result) == 1

    def test_haute_toml_pipeline_value_is_empty_string(self, tmp_path: Path) -> None:
        (tmp_path / "haute.toml").write_text('[project]\npipeline = ""\n')
        assert _configured_pipeline(tmp_path) is None


class TestUnreadablePipeline:
    def test_candidate_read_error_raises_config_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "haute.toml").write_text('[project]\nname = "x"\n')
        candidate = tmp_path / "locked.py"
        candidate.write_text(PIPELINE_CONTENT)

        original_read_text = Path.read_text

        def patched(path: Path, *args: object, **kwargs: object) -> str:
            if path == candidate:
                raise PermissionError(13, "Permission denied", str(path))
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", patched)

        with pytest.raises(ConfigError, match="locked.py"):
            discover_pipelines(tmp_path)
