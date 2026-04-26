"""Contract tests for route-helper cache and sidecar normalization."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import haute.routes._helpers as helpers


def _write_pipeline(py_path: Path, *submodels: str) -> None:
    lines = [
        "import haute",
        'pipeline = haute.Pipeline("test")',
    ]
    lines.extend(f'pipeline.submodel("{path}")' for path in submodels)
    lines.extend(
        [
            "@pipeline.polars",
            "def node(df):",
            "    return df",
            "",
        ]
    )
    py_path.write_text("\n".join(lines), encoding="utf-8")


def test_pipelines_importing_module_rebuilds_after_invalidate(tmp_path: Path) -> None:
    pricing = tmp_path / "pricing.py"
    shared = tmp_path / "shared.py"
    _write_pipeline(pricing, "modules/pricing.py")
    _write_pipeline(shared, "modules/shared.py")

    helpers.invalidate_pipeline_index()
    with patch("haute.routes._helpers.discover_pipelines", return_value=[pricing, shared]):
        assert helpers.pipelines_importing_module("pricing") == [pricing]
        assert helpers.pipelines_importing_module("shared") == [shared]

        _write_pipeline(pricing, "modules/shared.py")
        helpers.invalidate_pipeline_index()

        assert helpers.pipelines_importing_module("pricing") == []
        assert set(helpers.pipelines_importing_module("shared")) == {pricing, shared}


def test_pipelines_importing_module_skips_broken_pipeline_source(tmp_path: Path) -> None:
    valid = tmp_path / "valid.py"
    broken = tmp_path / "broken.py"
    _write_pipeline(valid, "modules/pricing.py")
    broken.write_text("def broken(\n", encoding="utf-8")

    helpers.invalidate_pipeline_index()
    with patch("haute.routes._helpers.discover_pipelines", return_value=[valid, broken]):
        assert helpers.pipelines_importing_module("pricing") == [valid]
        assert helpers.pipelines_importing_module("shared") == []


def test_parse_pipeline_to_graph_normalizes_sidecar_sources(tmp_path: Path) -> None:
    py_path = tmp_path / "pipeline.py"
    _write_pipeline(py_path)
    py_path.with_suffix(".haute.json").write_text(
        json.dumps(
            {
                "sources": ["batch_a", "live", "batch_a", 7, "", "batch_b"],
                "active_source": "batch_b",
            }
        ),
        encoding="utf-8",
    )

    graph = helpers.parse_pipeline_to_graph(py_path)

    assert graph.sources == ["live", "batch_a", "batch_b"]
    assert graph.active_source == "batch_b"


def test_parse_pipeline_to_graph_ignores_non_string_active_source(tmp_path: Path) -> None:
    py_path = tmp_path / "pipeline.py"
    _write_pipeline(py_path)
    py_path.with_suffix(".haute.json").write_text(
        json.dumps({"sources": ["batch_a"], "active_source": {"name": "batch_a"}}),
        encoding="utf-8",
    )

    graph = helpers.parse_pipeline_to_graph(py_path)

    assert graph.sources == ["live", "batch_a"]
    assert graph.active_source == "live"


def test_parse_pipeline_to_graph_ignores_invalid_position_payloads(tmp_path: Path) -> None:
    py_path = tmp_path / "pipeline.py"
    _write_pipeline(py_path)
    py_path.with_suffix(".haute.json").write_text(
        json.dumps({"positions": {"node": {"x": "left", "y": 20}}}),
        encoding="utf-8",
    )

    graph = helpers.parse_pipeline_to_graph(py_path)
    node = next(node for node in graph.nodes if node.id == "node")

    assert node.position == {"x": 0.0, "y": 0.0}
