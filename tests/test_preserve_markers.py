"""Tests for haute:preserve-start / haute:preserve-end marker support.

Verifies that preserved blocks survive a parse -> codegen round-trip,
that multiple blocks are maintained in order, and that files without
markers work normally.
"""

from __future__ import annotations

from pathlib import Path

from haute._config_io import collect_node_configs
from haute.codegen import graph_to_code
from haute.parser import parse_pipeline_source

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _roundtrip(source: str, base_dir: Path | None = None) -> str:
    """Parse source -> codegen -> return generated code.

    If *base_dir* is given, config files are written there before parsing
    and the parser resolves ``config=`` references relative to it.
    """
    graph = parse_pipeline_source(source, _base_dir=base_dir)
    code = graph_to_code(
        graph,
        pipeline_name=graph.pipeline_name or "main",
        description=graph.pipeline_description or "",
        preamble=graph.preamble or "",
    )
    if base_dir is not None:
        for rel_path, content in collect_node_configs(graph).items():
            cfg_file = base_dir / rel_path
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(content)
    return code


def _make_pipeline(
    *,
    preserved: str = "",
    preamble: str = "",
    node_code: str = "",
) -> str:
    """Build a minimal pipeline source string with optional preserved blocks."""
    lines = [
        "import polars as pl",
        "import haute",
    ]
    if preamble:
        lines.append("")
        lines.append(preamble)
    lines.append("")
    lines.append('pipeline = haute.Pipeline("test", description="test pipeline")')
    lines.append("")
    if preserved:
        lines.append("")
        lines.append(preserved)
        lines.append("")
    lines.append("")
    lines.append("@pipeline.polars")
    lines.append("def source() -> pl.LazyFrame:")
    lines.append('    """Create source rows."""')
    lines.append('    return pl.LazyFrame({"x": [1]})')
    if node_code:
        lines.append("")
        lines.append(node_code)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parser extraction tests
# ---------------------------------------------------------------------------


class TestExtractPreservedBlocks:
    def test_single_block_extracted(self):
        source = _make_pipeline(
            preserved=("# haute:preserve-start\nCUSTOM_CONSTANT = 42\n# haute:preserve-end"),
        )
        graph = parse_pipeline_source(source)
        assert len(graph.preserved_blocks) == 1
        assert "CUSTOM_CONSTANT = 42" in graph.preserved_blocks[0]

    def test_multiple_blocks_extracted_in_order(self):
        source = _make_pipeline(
            preserved=(
                "# haute:preserve-start\n"
                "FIRST = 1\n"
                "# haute:preserve-end\n"
                "\n"
                "# haute:preserve-start\n"
                "SECOND = 2\n"
                "# haute:preserve-end"
            ),
        )
        graph = parse_pipeline_source(source)
        assert len(graph.preserved_blocks) == 2
        assert "FIRST = 1" in graph.preserved_blocks[0]
        assert "SECOND = 2" in graph.preserved_blocks[1]

    def test_no_markers_returns_empty_list(self):
        source = _make_pipeline()
        graph = parse_pipeline_source(source)
        assert graph.preserved_blocks == []

    def test_function_in_preserved_block(self):
        source = _make_pipeline(
            preserved=(
                "# haute:preserve-start\ndef my_helper(x):\n    return x * 2\n# haute:preserve-end"
            ),
        )
        graph = parse_pipeline_source(source)
        assert len(graph.preserved_blocks) == 1
        assert "def my_helper(x):" in graph.preserved_blocks[0]
        assert "return x * 2" in graph.preserved_blocks[0]

    def test_comments_in_preserved_block(self):
        source = _make_pipeline(
            preserved=(
                "# haute:preserve-start\n"
                "# This is a custom comment\n"
                "MY_VAR = 'hello'\n"
                "# haute:preserve-end"
            ),
        )
        graph = parse_pipeline_source(source)
        assert len(graph.preserved_blocks) == 1
        assert "# This is a custom comment" in graph.preserved_blocks[0]
        assert "MY_VAR = 'hello'" in graph.preserved_blocks[0]

    def test_unmatched_start_marker_ignored(self):
        source = _make_pipeline(
            preserved=(
                "# haute:preserve-start\nORPHAN = True"
                # No end marker
            ),
        )
        graph = parse_pipeline_source(source)
        assert graph.preserved_blocks == []
        assert len(graph.nodes) == 1  # source node still parsed

    def test_empty_preserved_block(self):
        source = _make_pipeline(
            preserved=("# haute:preserve-start\n# haute:preserve-end"),
        )
        graph = parse_pipeline_source(source)
        # Empty blocks (only whitespace) are stored as empty strings
        assert len(graph.preserved_blocks) == 1
        assert graph.preserved_blocks[0] == ""


# ---------------------------------------------------------------------------
# Codegen emission tests
# ---------------------------------------------------------------------------


class TestCodegenEmitsPreservedBlocks:
    def test_single_block_emitted(self):
        graph = parse_pipeline_source(_make_pipeline())
        graph.preserved_blocks = ["CUSTOM = 42"]
        code = graph_to_code(graph, pipeline_name="test")
        assert "# haute:preserve-start" in code
        assert "CUSTOM = 42" in code
        assert "# haute:preserve-end" in code
        compile(code, "<test>", "exec")

    def test_multiple_blocks_emitted_in_order(self):
        graph = parse_pipeline_source(_make_pipeline())
        graph.preserved_blocks = ["FIRST = 1", "SECOND = 2"]
        code = graph_to_code(graph, pipeline_name="test")
        first_idx = code.index("FIRST = 1")
        second_idx = code.index("SECOND = 2")
        assert first_idx < second_idx
        # Each block gets its own markers
        code_lines = code.splitlines()
        starts = [
            i for i, line in enumerate(code_lines) if line.strip() == "# haute:preserve-start"
        ]
        ends = [i for i, line in enumerate(code_lines) if line.strip() == "# haute:preserve-end"]
        assert len(starts) == 2
        assert len(ends) == 2
        compile(code, "<test>", "exec")

    def test_preserved_blocks_before_node_functions(self):
        graph = parse_pipeline_source(_make_pipeline())
        graph.preserved_blocks = ["BEFORE_NODES = True"]
        code = graph_to_code(graph, pipeline_name="test")
        preserve_idx = code.index("BEFORE_NODES = True")
        node_idx = code.index("@pipeline.polars")
        assert preserve_idx < node_idx

    def test_preserved_blocks_after_pipeline_def(self):
        graph = parse_pipeline_source(_make_pipeline())
        graph.preserved_blocks = ["AFTER_PIPELINE = True"]
        code = graph_to_code(graph, pipeline_name="test")
        pipeline_idx = code.index("haute.Pipeline(")
        preserve_idx = code.index("AFTER_PIPELINE = True")
        assert pipeline_idx < preserve_idx

    def test_no_blocks_no_markers(self):
        graph = parse_pipeline_source(_make_pipeline())
        assert graph.preserved_blocks == []
        code = graph_to_code(graph, pipeline_name="test")
        assert "# haute:preserve-start" not in code
        assert "# haute:preserve-end" not in code

    def test_explicit_preserved_blocks_param_overrides_graph(self):
        graph = parse_pipeline_source(_make_pipeline())
        graph.preserved_blocks = ["FROM_GRAPH = True"]
        code = graph_to_code(
            graph,
            pipeline_name="test",
            preserved_blocks=["FROM_PARAM = True"],
        )
        assert "FROM_PARAM = True" in code
        assert "FROM_GRAPH = True" not in code


# ---------------------------------------------------------------------------
# Round-trip tests (parse -> codegen -> parse)
# ---------------------------------------------------------------------------


class TestPreservedBlocksRoundTrip:
    def test_single_block_survives_roundtrip(self, tmp_path):
        source = _make_pipeline(
            preserved=("# haute:preserve-start\nCUSTOM_CONSTANT = 42\n# haute:preserve-end"),
        )
        code = _roundtrip(source, base_dir=tmp_path)
        graph2 = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(graph2.preserved_blocks) == 1
        assert "CUSTOM_CONSTANT = 42" in graph2.preserved_blocks[0]

    def test_multiple_blocks_survive_roundtrip(self, tmp_path):
        source = _make_pipeline(
            preserved=(
                "# haute:preserve-start\n"
                "FIRST = 1\n"
                "# haute:preserve-end\n"
                "\n"
                "# haute:preserve-start\n"
                "SECOND = 2\n"
                "# haute:preserve-end"
            ),
        )
        code = _roundtrip(source, base_dir=tmp_path)
        graph2 = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(graph2.preserved_blocks) == 2
        assert "FIRST = 1" in graph2.preserved_blocks[0]
        assert "SECOND = 2" in graph2.preserved_blocks[1]

    def test_function_survives_roundtrip(self, tmp_path):
        source = _make_pipeline(
            preserved=(
                "# haute:preserve-start\n"
                "def my_custom_helper():\n"
                "    return 42\n"
                "# haute:preserve-end"
            ),
        )
        code = _roundtrip(source, base_dir=tmp_path)
        graph2 = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(graph2.preserved_blocks) == 1
        assert "def my_custom_helper():" in graph2.preserved_blocks[0]
        assert "return 42" in graph2.preserved_blocks[0]

    def test_mixed_content_survives_roundtrip(self, tmp_path):
        block = (
            "# haute:preserve-start\n"
            "# Custom lookup table\n"
            "REGIONS = {'north': 1.1, 'south': 0.9}\n"
            "\n"
            "def get_factor(region):\n"
            "    return REGIONS.get(region, 1.0)\n"
            "# haute:preserve-end"
        )
        source = _make_pipeline(preserved=block)
        code = _roundtrip(source, base_dir=tmp_path)
        graph2 = parse_pipeline_source(code, _base_dir=tmp_path)
        assert len(graph2.preserved_blocks) == 1
        assert "REGIONS" in graph2.preserved_blocks[0]
        assert "def get_factor(region):" in graph2.preserved_blocks[0]

    def test_roundtrip_without_markers_still_works(self, tmp_path):
        source = _make_pipeline()
        code = _roundtrip(source, base_dir=tmp_path)
        graph2 = parse_pipeline_source(code, _base_dir=tmp_path)
        assert graph2.preserved_blocks == []
        assert len(graph2.nodes) == 1

    def test_preamble_and_preserved_blocks_coexist(self, tmp_path):
        source = _make_pipeline(
            preamble="import numpy as np",
            preserved=("# haute:preserve-start\nCUSTOM = True\n# haute:preserve-end"),
        )
        graph = parse_pipeline_source(source)
        assert "numpy" in (graph.preamble or "")
        assert len(graph.preserved_blocks) == 1

        code = _roundtrip(source, base_dir=tmp_path)
        graph2 = parse_pipeline_source(code, _base_dir=tmp_path)
        assert "numpy" in (graph2.preamble or "")
        assert len(graph2.preserved_blocks) == 1
        assert "CUSTOM = True" in graph2.preserved_blocks[0]

    def test_generated_code_compiles(self):
        source = _make_pipeline(
            preamble="import math",
            preserved=("# haute:preserve-start\nTAU = math.pi * 2\n# haute:preserve-end"),
        )
        code = _roundtrip(source)
        compile(code, "<test>", "exec")

    def test_double_roundtrip_stable(self, tmp_path):
        """Two consecutive round-trips produce the same output (idempotent)."""
        source = _make_pipeline(
            preamble="from datetime import date",
            preserved=(
                "# haute:preserve-start\n"
                "TODAY = date.today()\n"
                "# haute:preserve-end\n"
                "\n"
                "# haute:preserve-start\n"
                "MAX_ROWS = 10_000\n"
                "# haute:preserve-end"
            ),
        )
        code1 = _roundtrip(source, base_dir=tmp_path)
        code2 = _roundtrip(code1, base_dir=tmp_path)
        assert code1 == code2
        assert "TODAY = date.today()" in code2
        assert "MAX_ROWS = 10_000" in code2
