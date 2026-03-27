"""Safety tests — verify pipeline structural invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._types import NodeType
from haute.parser import parse_pipeline_file


def _find_pipeline_files() -> list[Path]:
    """Discover committed pipeline fixture files for parametrisation."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    if not fixtures_dir.is_dir():
        return []
    return [
        f
        for f in fixtures_dir.rglob("*.py")
        if f.name != "__init__.py" and "haute.Pipeline" in f.read_text()
    ]


class TestApiOutputSafety:
    """Every fixture pipeline must contain at least one output node."""

    @pytest.mark.parametrize(
        "pipeline_file",
        _find_pipeline_files(),
        ids=lambda p: p.name,
    )
    def test_pipeline_has_output_node(self, pipeline_file: Path) -> None:
        graph = parse_pipeline_file(pipeline_file)
        output_nodes = [n for n in graph.nodes if n.data.nodeType == NodeType.OUTPUT]
        assert output_nodes, f"{pipeline_file.name} has no output node"
