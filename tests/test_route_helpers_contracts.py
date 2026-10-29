"""Contract tests for route-helper cache and sidecar normalization."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import haute.routes._helpers as helpers


def _write_pipeline(py_path: Path, *modules: str) -> None:
    """A pipeline importing each of *modules* and registering one occurrence named after it."""
    lines = ["import haute", "import polars as pl"]
    if modules:
        lines.append(f"from modules import {', '.join(modules)}")
    lines.append('pipeline = haute.Pipeline("test")')
    for module in modules:
        lines.append(f'pipeline.submodel({module}.submodel, "{module}")')
    lines.extend(
        [
            "@pipeline.polars",
            "def node():",
            "    return pl.LazyFrame({'x': [1]})",
            "",
        ]
    )
    py_path.write_text("\n".join(lines), encoding="utf-8")


def _module(root: Path, name: str) -> Path:
    """The submodel module ``from modules import <name>`` imports beside *root*'s pipelines."""
    return root / "modules" / f"{name}.py"


def test_pipelines_importing_module_rebuilds_after_invalidate(tmp_path: Path) -> None:
    pricing = tmp_path / "pricing.py"
    shared = tmp_path / "shared.py"
    _write_pipeline(pricing, "pricing")
    _write_pipeline(shared, "shared")

    helpers.invalidate_pipeline_index()
    with patch("haute.routes._helpers.discover_pipelines", return_value=[pricing, shared]):
        assert helpers.pipelines_importing_module(_module(tmp_path, "pricing")) == [pricing]
        assert helpers.pipelines_importing_module(_module(tmp_path, "shared")) == [shared]

        _write_pipeline(pricing, "shared")
        helpers.invalidate_pipeline_index()

        assert helpers.pipelines_importing_module(_module(tmp_path, "pricing")) == []
        assert set(helpers.pipelines_importing_module(_module(tmp_path, "shared"))) == {
            pricing,
            shared,
        }


def test_a_module_is_the_file_beside_the_pipeline_importing_it(tmp_path: Path) -> None:
    """Two pipelines in different folders import their own ``modules/rates.py``: a
    change to one file is the importer's beside it, never the other's."""
    rating = tmp_path / "rating" / "main.py"
    other = tmp_path / "other" / "main.py"
    rating.parent.mkdir()
    other.parent.mkdir()
    _write_pipeline(rating, "rates")
    _write_pipeline(other, "rates")

    helpers.invalidate_pipeline_index()
    with patch("haute.routes._helpers.discover_pipelines", return_value=[rating, other]):
        assert helpers.pipelines_importing_module(_module(rating.parent, "rates")) == [rating]
        assert helpers.pipelines_importing_module(_module(other.parent, "rates")) == [other]
        assert helpers.pipelines_importing_module(_module(tmp_path, "rates")) == []


def test_pipelines_importing_module_skips_broken_pipeline_source(tmp_path: Path) -> None:
    valid = tmp_path / "valid.py"
    broken = tmp_path / "broken.py"
    _write_pipeline(valid, "pricing")
    broken.write_text("def broken(\n", encoding="utf-8")

    helpers.invalidate_pipeline_index()
    with patch("haute.routes._helpers.discover_pipelines", return_value=[valid, broken]):
        assert helpers.pipelines_importing_module(_module(tmp_path, "pricing")) == [valid]
        assert helpers.pipelines_importing_module(_module(tmp_path, "shared")) == []


@pytest.mark.parametrize(
    "refused",
    [
        pytest.param('pipeline.submodel("modules/shared.py", "shared")\n', id="removed-path-form"),
        pytest.param("from modules import shared\n", id="import-without-registration"),
    ],
)
def test_pipelines_importing_module_skips_a_pipeline_whose_registrations_are_refused(
    tmp_path: Path, refused: str
) -> None:
    """The parser refusing one pipeline's registrations leaves the map whole for the
    others; the refusal used to fail the map, and live-sync, for every pipeline."""
    refusing = tmp_path / "refusing.py"
    valid = tmp_path / "valid.py"
    refusing.write_text(
        'import haute\npipeline = haute.Pipeline("refusing")\n' + refused, encoding="utf-8"
    )
    _write_pipeline(valid, "pricing")

    helpers.invalidate_pipeline_index()
    with patch("haute.routes._helpers.discover_pipelines", return_value=[refusing, valid]):
        assert helpers.pipelines_importing_module(_module(tmp_path, "pricing")) == [valid]
        assert helpers.pipelines_importing_module(_module(tmp_path, "shared")) == []


def test_pipelines_importing_module_matches_the_file_case_insensitively(
    tmp_path: Path,
) -> None:
    """Build-side paths come from the ``from modules import <name>`` a registration
    names its definition by; the watcher passes the ON-DISK path.  On a
    case-insensitive filesystem (macOS/Windows) an imported ``rates`` resolves
    against an on-disk ``modules/Rates.py`` — one module, two spellings — so the map
    must be hit regardless of case or live-sync silently goes stale."""
    pricing = tmp_path / "pricing.py"
    shared = tmp_path / "shared.py"
    _write_pipeline(pricing, "rates")  # lowercase import
    _write_pipeline(shared, "BaseRates")  # mixed-case import

    helpers.invalidate_pipeline_index()
    with patch("haute.routes._helpers.discover_pipelines", return_value=[pricing, shared]):
        # Watcher queries with a differently-cased on-disk name.
        assert helpers.pipelines_importing_module(_module(tmp_path, "Rates")) == [pricing]
        assert helpers.pipelines_importing_module(_module(tmp_path, "RATES")) == [pricing]
        assert helpers.pipelines_importing_module(_module(tmp_path, "baserates")) == [shared]
        # Same-case queries still hit, and distinct modules stay distinct.
        assert helpers.pipelines_importing_module(_module(tmp_path, "rates")) == [pricing]
        assert helpers.pipelines_importing_module(_module(tmp_path, "other")) == []


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
