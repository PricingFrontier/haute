"""OUTPUT v2 nested round-trip + render (MULTI_FRAME_PLAN piece 4b / commit 9).

End-to-end proof of the OUTPUT assembler on the canonical data-model example
(``notes-haute/data-model/_DATA_MODEL.md`` §4.2): a single nested document
(``data_model_example.json``) is shredded by the v2 apiInput into four ports
(policies / drivers / licenses / vehicles, with W1 ancestor keys distributed
into the child tables), and the OUTPUT node reassembles them via its
``outputMapping`` into the *same* nested document.

This exercises the parts the kill-v1 cutover's flat tests do not:
- a genuinely **multi-port** OUTPUT (one apiInput feeding four edges, each a
  distinct ``sourceHandle``) — the executor keys frames by source *port*;
- **prefix nesting** three arrays deep with two sibling branches
  (``drivers`` carries ``licenses``; ``vehicles`` is a sibling) that must NOT
  cross-multiply;
- the **render** path: ``pl.LazyFrame(document)`` round-trips a ragged
  document by null-filling, which ``_prune`` (Q1) restores — so the rendered
  JSON equals the original "up to empty collections".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._json_flatten import _json_cache_dir
from haute._json_shred import build_per_port_cache
from haute._output_assembler import _prune, render_output_document
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _preview_cache, execute_graph

_FIXTURE = Path(__file__).parent / "fixtures" / "output_assembler" / "data_model_example.json"


def _expected_document() -> list[dict[str, Any]]:
    return json.loads(_FIXTURE.read_text())


def _api_input_config(data_path: Path) -> dict[str, Any]:
    """v2 apiInput shred config: four emit-true tables with W1 ancestor keys.

    Each child table declares the ancestor key column(s) with their *shallow*
    JSONPath (e.g. drivers declares ``policy_id`` at ``$[*].policy_id``); the
    shred distributes the ancestor value into every descendant row so the
    OUTPUT can nest the child back under the right parent.
    """

    def col(name: str, path: str, type_: str) -> dict[str, Any]:
        return {"name": name, "path": path, "type": type_, "selected": True}

    return {
        "path": str(data_path),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[*]",
                "label": "policies",
                "emit": True,
                "columns": [col("policy_id", "$[*].policy_id", "int")],
            },
            {
                "path": "$[*].drivers[*]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    col("policy_id", "$[*].policy_id", "int"),
                    col("driver_id", "$[*].drivers[*].driver_id", "int"),
                    col("main", "$[*].drivers[*].main", "bool"),
                    col("age_band", "$[*].drivers[*].age_band", "str"),
                ],
            },
            {
                "path": "$[*].drivers[*].licenses[*]",
                "label": "licenses",
                "emit": True,
                "columns": [
                    col("policy_id", "$[*].policy_id", "int"),
                    col("driver_id", "$[*].drivers[*].driver_id", "int"),
                    col("license_id", "$[*].drivers[*].licenses[*].license_id", "int"),
                    col(
                        "issuing_authority",
                        "$[*].drivers[*].licenses[*].issuing_authority",
                        "str",
                    ),
                    col("license_type", "$[*].drivers[*].licenses[*].license_type", "str"),
                ],
            },
            {
                "path": "$[*].vehicles[*]",
                "label": "vehicles",
                "emit": True,
                "columns": [
                    col("policy_id", "$[*].policy_id", "int"),
                    col("vehicle_id", "$[*].vehicles[*].vehicle_id", "int"),
                    col("engine_size", "$[*].vehicles[*].engine_size", "str"),
                    col("class_of_use", "$[*].vehicles[*].class_of_use", "str"),
                ],
            },
        ],
    }


def _output_mapping() -> list[dict[str, Any]]:
    """Reassemble the nested document. Ancestor keys (policy_id, driver_id) are
    mapped to their shallow paths from every table that carries them — that
    shared path is the join/nest constraint."""

    def e(port: str, col: str, path: str) -> dict[str, Any]:
        return {
            "source_port": port,
            "source_column": col,
            "output_path": path,
            "enabled": True,
        }

    return [
        e("policies", "policy_id", "$[:].policy_id"),
        e("drivers", "policy_id", "$[:].policy_id"),
        e("drivers", "driver_id", "$[:].drivers[:].driver_id"),
        e("drivers", "main", "$[:].drivers[:].main"),
        e("drivers", "age_band", "$[:].drivers[:].age_band"),
        e("licenses", "policy_id", "$[:].policy_id"),
        e("licenses", "driver_id", "$[:].drivers[:].driver_id"),
        e("licenses", "license_id", "$[:].drivers[:].licenses[:].license_id"),
        e("licenses", "issuing_authority", "$[:].drivers[:].licenses[:].issuing_authority"),
        e("licenses", "license_type", "$[:].drivers[:].licenses[:].license_type"),
        e("vehicles", "policy_id", "$[:].policy_id"),
        e("vehicles", "vehicle_id", "$[:].vehicles[:].vehicle_id"),
        e("vehicles", "engine_size", "$[:].vehicles[:].engine_size"),
        e("vehicles", "class_of_use", "$[:].vehicles[:].class_of_use"),
    ]


@pytest.fixture()
def project_with_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Materialise the example under ``<root>/data/`` (the runtime location)
    and point the sandbox project root at the isolated tree."""
    monkeypatch.chdir(tmp_path)
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.invalidate()
    data_path = tmp_path / "data" / "data_model_example.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(_FIXTURE.read_text())
    yield tmp_path, data_path
    set_project_root(original)
    _preview_cache.invalidate()


def _roundtrip_graph(config: dict[str, Any]) -> PipelineGraph:
    ports = ["policies", "drivers", "licenses", "vehicles"]
    return PipelineGraph(
        nodes=[
            GraphNode(
                id="api",
                data=NodeData(label="api", nodeType=NodeType.API_INPUT, config=config),
            ),
            GraphNode(
                id="out",
                data=NodeData(
                    label="out",
                    nodeType=NodeType.OUTPUT,
                    config={"outputMapping": _output_mapping(), "outputFormat": "json"},
                ),
            ),
        ],
        edges=[GraphEdge(id=f"e_{p}", source="api", target="out", sourceHandle=p) for p in ports],
    )


def test_nested_output_roundtrips_to_original(project_with_data) -> None:
    tmp_path, data_path = project_with_data
    config = _api_input_config(data_path)
    build_per_port_cache(data_path, config, _json_cache_dir(data_path, "working"))

    graph = _roundtrip_graph(config)
    results = execute_graph(graph, target_node_id="out")

    assert results["out"].status == "ok", results["out"].error
    # The preview render already prunes the ragged null-fill for OUTPUT nodes
    # (executor wires ``render_output_document``), so the product preview equals
    # the original document directly — equality up to empty collections.
    assert results["out"].preview == _expected_document()
    # And the bare assembled frame round-trips under the same render recipe.
    assert _prune(results["out"].preview) == _expected_document()


def test_nested_output_renders_through_deploy_scorer() -> None:
    """The deploy scoring path (``score_graph``) + response render
    (``render_output_document``, what the container template calls) produces a
    nested document — the deploy seam the canvas-preview round-trip doesn't
    cover. A single denormalised frame nests via the OUTPUT mapping (one source
    port: ``policy_id`` carried at root, ``driver_id`` emitted under it)."""
    from haute.deploy._scorer import score_graph

    input_df = pl.DataFrame({"policy_id": [1001, 1001, 1002], "driver_id": [1, 2, 1]})
    mapping = [
        {
            "source_port": "src",
            "source_column": "policy_id",
            "output_path": "$[:].policy_id",
            "enabled": True,
        },
        {
            "source_port": "src",
            "source_column": "driver_id",
            "output_path": "$[:].drivers[:].driver_id",
            "enabled": True,
        },
    ]
    graph = PipelineGraph(
        nodes=[
            GraphNode(
                id="src",
                data=NodeData(label="src", nodeType=NodeType.API_INPUT, config={"path": ""}),
            ),
            GraphNode(
                id="out",
                data=NodeData(
                    label="out",
                    nodeType=NodeType.OUTPUT,
                    config={"outputMapping": mapping, "outputFormat": "json"},
                ),
            ),
        ],
        edges=[GraphEdge(id="e", source="src", target="out")],
    )

    result = score_graph(
        graph=graph,
        input_df=input_df,
        input_node_ids=["src"],
        output_node_id="out",
        artifact_paths={},
    )
    # ``render_output_document`` is exactly what the deploy ``/quote`` response
    # template runs on the OUTPUT frame.
    assert render_output_document(result) == [
        {"policy_id": 1001, "drivers": [{"driver_id": 1}, {"driver_id": 2}]},
        {"policy_id": 1002, "drivers": [{"driver_id": 1}]},
    ]
