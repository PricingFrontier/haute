"""Tests for read_user_text and encoding robustness across the codebase."""

from __future__ import annotations

import json

import pytest

from haute._io import read_user_text


class TestReadUserText:
    """Unit tests for the read_user_text helper."""

    def test_replaces_bad_bytes(self, tmp_path):
        """Windows-1252 en-dash (0x96) is replaced with U+FFFD, not crash."""
        p = tmp_path / "bad.txt"
        p.write_bytes(b"hello \x96 world")
        result = read_user_text(p)
        assert "\ufffd" in result
        assert "hello" in result
        assert "world" in result

    def test_valid_utf8_unchanged(self, tmp_path):
        """Normal UTF-8 content passes through unchanged."""
        p = tmp_path / "good.txt"
        p.write_text("café résumé naïve", encoding="utf-8")
        assert read_user_text(p) == "café résumé naïve"

    def test_empty_file(self, tmp_path):
        """Empty file returns empty string."""
        p = tmp_path / "empty.txt"
        p.write_bytes(b"")
        assert read_user_text(p) == ""

    def test_accepts_string_path(self, tmp_path):
        """Accepts a string path, not just Path objects."""
        p = tmp_path / "str.txt"
        p.write_text("ok")
        assert read_user_text(str(p)) == "ok"

    def test_multiple_bad_bytes(self, tmp_path):
        """Multiple non-UTF-8 bytes throughout the file are all replaced."""
        p = tmp_path / "multi.txt"
        p.write_bytes(b"20\x9627 and 28\x9634 and 35\x9641")
        result = read_user_text(p)
        assert result.count("\ufffd") == 3

    def test_missing_file_raises(self, tmp_path):
        """Non-existent path raises FileNotFoundError, not silent empty string."""
        with pytest.raises(FileNotFoundError):
            read_user_text(tmp_path / "nonexistent.txt")


class TestParsePipelineEncodingRobustness:
    """Verify pipeline parsing tolerates non-UTF-8 bytes in .py files."""

    def test_non_utf8_in_comment(self, tmp_path, monkeypatch):
        """A non-UTF-8 byte in a Python comment should not crash the parser."""
        monkeypatch.chdir(tmp_path)
        pipeline_dir = tmp_path / "project"
        pipeline_dir.mkdir()
        content = (
            b"import haute\nimport polars as pl\n\n"
            b'pipeline = haute.Pipeline("test")\n\n'
            b"# This has an en-dash \x96 in a comment\n\n"
            b"@pipeline.polars\n"
            b"def transform(df: pl.LazyFrame) -> pl.LazyFrame:\n"
            b"    return df\n"
        )
        (pipeline_dir / "main.py").write_bytes(content)

        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "main.py")
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "transform"

    def test_non_utf8_in_string_literal(self, tmp_path, monkeypatch):
        """A non-UTF-8 byte in a string literal still parses nodes."""
        monkeypatch.chdir(tmp_path)
        pipeline_dir = tmp_path / "project"
        pipeline_dir.mkdir()
        content = (
            b"import haute\nimport polars as pl\n\n"
            b'pipeline = haute.Pipeline("test")\n\n'
            b"@pipeline.polars\n"
            b"def transform(df: pl.LazyFrame) -> pl.LazyFrame:\n"
            b'    """Has en-dash \x96 in docstring"""\n'
            b"    return df\n"
        )
        (pipeline_dir / "main.py").write_bytes(content)

        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "main.py")
        # The replacement char may cause a syntax error, but the regex
        # fallback parser should still find the node
        assert len(graph.nodes) >= 1


class TestConfigEncodingRobustness:
    """Verify config loading tolerates non-UTF-8 bytes in JSON files."""

    def test_non_utf8_json_with_valid_structure_loads_successfully(self, tmp_path, monkeypatch):
        """JSON config with non-UTF-8 bytes but valid structure loads without error."""
        monkeypatch.chdir(tmp_path)
        pipeline_dir = tmp_path / "project"
        pipeline_dir.mkdir()
        config_dir = pipeline_dir / "config" / "banding"
        config_dir.mkdir(parents=True)
        # JSON with 0x96 in a value — after replacement, JSON is still valid
        (config_dir / "bands.json").write_bytes(b'{"bands": [{"label": "20\x9627"}]}')
        (pipeline_dir / "main.py").write_text(
            "import haute\nimport polars as pl\n\n"
            'pipeline = haute.Pipeline("test")\n\n'
            '@pipeline.banding(config="config/banding/bands.json")\n'
            "def bands(df: pl.LazyFrame) -> pl.LazyFrame:\n"
            "    return df\n"
        )

        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "main.py")
        assert len(graph.nodes) == 1
        # After replacement, the JSON is valid (just has U+FFFD in the label)
        # so it should load successfully — no _load_error
        config = graph.nodes[0].data.config
        assert "_load_error" not in config

    def test_end_to_end_corrupt_config_shows_warning(self, tmp_path, monkeypatch):
        """Full parse with a config that has broken JSON structure after
        replacement shows graph.warning and still loads the node."""
        monkeypatch.chdir(tmp_path)
        pipeline_dir = tmp_path / "project"
        pipeline_dir.mkdir()
        config_dir = pipeline_dir / "config" / "banding"
        config_dir.mkdir(parents=True)
        # 0x96 placed where it will corrupt JSON structure after replacement
        # (the replacement char U+FFFD breaks the key)
        (config_dir / "bands.json").write_bytes(b'{\x96: "value"}')
        (pipeline_dir / "main.py").write_text(
            "import haute\nimport polars as pl\n\n"
            'pipeline = haute.Pipeline("test")\n\n'
            '@pipeline.banding(config="config/banding/bands.json")\n'
            "def bands(df: pl.LazyFrame) -> pl.LazyFrame:\n"
            "    return df\n"
        )

        from haute.parser import parse_pipeline_file

        graph = parse_pipeline_file(pipeline_dir / "main.py")
        assert len(graph.nodes) == 1
        assert graph.warning is not None
        assert "bands" in graph.warning
