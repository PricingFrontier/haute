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
    for path in submodels:
        definition_id = Path(path).stem
        lines.append(
            f'pipeline.submodel("{path}", definition_id="{definition_id}", '
            f'instance_id="submodel__{definition_id}", alias="{definition_id}")'
        )
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


def test_pipelines_importing_module_matches_stem_case_insensitively(tmp_path: Path) -> None:
    """Build-side stems come from ``pipeline.submodel("...")`` source literals;
    the watcher passes the ON-DISK filename's stem.  On a case-insensitive
    filesystem (macOS/Windows) ``modules/rates.py`` in source resolves against
    an on-disk ``modules/Rates.py`` — one module, two spellings — so the map
    must be hit regardless of stem case or live-sync silently goes stale."""
    pricing = tmp_path / "pricing.py"
    shared = tmp_path / "shared.py"
    _write_pipeline(pricing, "modules/rates.py")  # lowercase source literal
    _write_pipeline(shared, "modules/BaseRates.py")  # mixed-case source literal

    helpers.invalidate_pipeline_index()
    with patch("haute.routes._helpers.discover_pipelines", return_value=[pricing, shared]):
        # Watcher queries with a differently-cased on-disk stem.
        assert helpers.pipelines_importing_module("Rates") == [pricing]
        assert helpers.pipelines_importing_module("RATES") == [pricing]
        assert helpers.pipelines_importing_module("baserates") == [shared]
        # Same-case queries still hit, and distinct stems stay distinct.
        assert helpers.pipelines_importing_module("rates") == [pricing]
        assert helpers.pipelines_importing_module("other") == []


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
