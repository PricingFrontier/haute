"""Tests for read_user_text and the strict decoding of pipeline sources and node configs."""

from __future__ import annotations

import pytest

from haute._io import read_user_text
from haute.errors import ConfigError, ParseError


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


class TestPipelineSourcesDecodeStrictly:
    """A pipeline or submodel source that is not UTF-8 is refused, never read with replacements.

    Python itself refuses to compile such a file, and a source decoded with U+FFFD would be
    written over the author's bytes by the next save.
    """

    def test_a_cp1252_byte_in_a_comment_fails_the_parse_naming_file_line_and_byte(self, tmp_path):
        content = (
            b"import haute\nimport polars as pl\n\n"
            b'pipeline = haute.Pipeline("test")\n\n'
            b"# Pr\xe4mie in a comment\n\n"
            b"@pipeline.polars\n"
            b"def transform() -> pl.LazyFrame:\n"
            b'    return pl.LazyFrame({"x": [1]})\n'
        )
        (tmp_path / "main.py").write_bytes(content)

        from haute.parser import parse_pipeline_file

        with pytest.raises(ParseError) as raised:
            parse_pipeline_file(tmp_path / "main.py")
        message = str(raised.value)
        assert "main.py" in message
        assert "not valid UTF-8" in message
        assert "byte 0xE4 at line 6, column 5" in message
        assert raised.value.context["line"] == 6
        assert raised.value.context["column"] == 5

    def test_a_cp1252_byte_in_a_registered_submodel_file_fails_the_parse(self, tmp_path):
        (tmp_path / "modules").mkdir()
        (tmp_path / "modules" / "pricing.py").write_bytes(
            b"import haute\n\n"
            b'submodel = haute.Submodel("pricing", definition_id="pricing", '
            b'input_ports=[], output_ports=[], pipeline_dir="..")\n'
            b"# \x96\n"
        )
        (tmp_path / "main.py").write_text(
            "import haute\n\n"
            "from modules import pricing\n\n"
            'pipeline = haute.Pipeline("test")\n\n'
            'pipeline.submodel(pricing.submodel, "pricing")\n',
            encoding="utf-8",
        )

        from haute.parser import parse_pipeline_file, parse_submodel_file

        for parse in (
            lambda: parse_pipeline_file(tmp_path / "main.py"),
            lambda: parse_submodel_file(tmp_path / "modules" / "pricing.py"),
        ):
            with pytest.raises(ParseError, match=r"pricing\.py.*byte 0x96 at line 4, column 3"):
                parse()

    def test_a_byte_order_mark_is_dropped_as_python_drops_it(self, tmp_path):
        from haute._io import read_user_source
        from haute.parser import parse_pipeline_file

        source = (
            "import haute\n\n"
            'pipeline = haute.Pipeline("bom")\n\n'
            "@pipeline.polars\n"
            "def transform():\n"
            '    """Prämie"""\n'
            "    return None\n"
        )
        raw = b"\xef\xbb\xbf" + source.encode("utf-8")
        (tmp_path / "main.py").write_bytes(raw)

        assert read_user_source(tmp_path / "main.py") == (raw, source)
        assert [node.id for node in parse_pipeline_file(tmp_path / "main.py").nodes] == [
            "transform"
        ]


class TestNodeConfigsDecodeStrictly:
    """A node's JSON config that is not UTF-8 is refused naming the file and the byte."""

    @staticmethod
    def _banding_project(tmp_path):
        config_dir = tmp_path / "config" / "banding"
        config_dir.mkdir(parents=True)
        config = config_dir / "bands.json"
        config.write_bytes(
            b'{"factors": [{"banding": "categorical", "column": "age", '
            b'"outputColumn": "age_band", "rules": [{"value": "young", '
            b'"assignment": "20\x9627"}], "default": "other"}]}'
        )
        (tmp_path / "main.py").write_text(
            "import haute\n\n"
            'pipeline = haute.Pipeline("test")\n\n'
            '@pipeline.banding(config="config/banding/bands.json")\n'
            "def bands(df): ...\n",
            encoding="utf-8",
        )
        return config

    def test_the_parse_fails_naming_the_file_and_the_byte(self, tmp_path):
        config = self._banding_project(tmp_path)
        before = config.read_bytes()

        from haute.parser import parse_pipeline_file

        with pytest.raises(ConfigError) as raised:
            parse_pipeline_file(tmp_path / "main.py")
        message = str(raised.value)
        assert "bands.json" in message
        assert "not valid UTF-8" in message
        assert "byte 0x96 at line 1, column 132" in message
        assert config.read_bytes() == before

    def test_the_node_fails_where_it_runs(self, tmp_path):
        config = self._banding_project(tmp_path)

        from haute._config_io import load_node_config

        with pytest.raises(ConfigError, match=r"bands\.json is not valid UTF-8: byte 0x96"):
            load_node_config(config.relative_to(tmp_path), base_dir=tmp_path)
