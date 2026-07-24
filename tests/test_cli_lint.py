"""Tests for haute.cli._lint — the ``haute lint`` command (edge cases)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph
from haute.cli import cli

if TYPE_CHECKING:
    from click.testing import CliRunner


class TestLintEdgeCases:
    def test_parse_error_reports_failure(self, runner: CliRunner, tmp_path: Path) -> None:
        """A syntactically invalid Python file should report a parse error."""
        bad = tmp_path / "bad.py"
        bad.write_text("def oops(\n")
        result = runner.invoke(cli, ["lint", str(bad)])
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_orphan_nodes_detected(self, runner: CliRunner, tmp_path: Path) -> None:
        """Nodes with no edges should be flagged as disconnected."""
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    data=NodeData(label="a", nodeType="dataInput", config={"path": "d.parquet"}),
                ),
                GraphNode(
                    id="b",
                    data=NodeData(label="b", nodeType="dataInput", config={"path": "d.parquet"}),
                ),
            ],
            edges=[],
        )
        p = tmp_path / "orphan.py"
        p.write_text("# placeholder\n")

        with patch("haute.parser.parse_pipeline_file", return_value=graph):
            result = runner.invoke(cli, ["lint", str(p)])
        assert result.exit_code == 1
        assert "disconnected" in result.output.lower()

    def test_node_with_parse_error_config(self, runner: CliRunner, tmp_path: Path) -> None:
        """Nodes with parseError in their config should be flagged."""
        graph = PipelineGraph(
            nodes=[
                GraphNode(
                    id="a",
                    data=NodeData(
                        label="a",
                        nodeType="dataInput",
                        config={"parseError": "bad syntax"},
                    ),
                ),
                GraphNode(
                    id="b",
                    data=NodeData(label="b", nodeType="polars", config={}),
                ),
            ],
            edges=[GraphEdge(id="e1", source="a", target="b")],
        )
        p = tmp_path / "pe.py"
        p.write_text("# placeholder\n")

        with patch("haute.parser.parse_pipeline_file", return_value=graph):
            result = runner.invoke(cli, ["lint", str(p)])
        assert result.exit_code == 1
        assert "parse error" in result.output.lower()

    def test_edges_referencing_missing_nodes(self, runner: CliRunner, tmp_path: Path) -> None:
        """Edges pointing to non-existent nodes should be flagged."""
        graph = PipelineGraph(
            nodes=[
                GraphNode(id="a", data=NodeData(label="a", nodeType="dataInput", config={})),
            ],
            edges=[GraphEdge(id="e1", source="a", target="nonexistent")],
        )
        p = tmp_path / "missing_target.py"
        p.write_text("# placeholder\n")

        with patch("haute.parser.parse_pipeline_file", return_value=graph):
            result = runner.invoke(cli, ["lint", str(p)])
        assert result.exit_code == 1
        assert "missing" in result.output.lower()

    def test_auto_discover_no_toml_defaults_to_main(
        self,
        runner: CliRunner,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without haute.toml, lint should default to main.py."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["lint"])
        assert result.exit_code == 1
        assert "main.py" in result.output
