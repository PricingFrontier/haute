"""Isolated reproduction for V011.

Claim: a multi-port apiInput *source* node reports ZERO columns in its own
preview NodeResult (``column_count == 0`` / ``columns == []``) even though its
representative first-port DataFrame has real columns and ``row_count`` is
reported correctly.

Root cause (per candidate):
- ``_execute_eager_core`` multi-port branch hard-sets ``output_columns[nid] = []``
  (src/haute/_execute_lazy.py:1904) then ``continue``s, so the empty list flows
  out as ``EagerResult.output_columns`` -> ``output_cols`` in the executor.
- ``_column_infos_for_node`` (src/haute/executor.py:1131-1137) does
  ``full_output = output_cols.get(node_id)`` -> ``[]``; because ``[] is not None``
  it sets ``columns = []`` and never reaches the ``elif df is not None`` branch
  that would derive columns from the representative first-port DataFrame
  (executor.py:1182-1185).

This script builds a tiny synthetic multi-port apiInput graph entirely inside a
tempfile project root (via haute._sandbox.set_project_root), builds the v2
per-port cache in that isolated tree, runs the real ``execute_graph`` preview
path, and ASSERTS on the specific wrong value for the source node.

It never reads or writes rating/, src/, tests/, or real project files.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from haute._json_flatten import _json_cache_dir
from haute._json_shred import build_per_port_cache
from haute._sandbox import _get_project_root, set_project_root
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.executor import _preview_cache, execute_graph


def _rating_records() -> list[dict[str, Any]]:
    return [
        {
            "policy_id": 1001,
            "drivers": [
                {"driver_id": 1, "age_band": "30-59"},
                {"driver_id": 2, "age_band": "60+"},
            ],
        },
        {
            "policy_id": 1002,
            "drivers": [{"driver_id": 3, "age_band": "60+"}],
        },
    ]


def _multi_port_config(data_path: Path) -> dict[str, Any]:
    # Two emit-true tables -> source emits dict[port_label, DataFrame]
    # (the multi-port branch). First port = "policies".
    return {
        "path": str(data_path),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[*]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[*].policy_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
            {
                "path": "$[*].drivers[*]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    {
                        "name": "driver_id",
                        "path": "$[*].drivers[*].driver_id",
                        "type": "int",
                        "selected": True,
                    },
                    {
                        "name": "age_band",
                        "path": "$[*].drivers[*].age_band",
                        "type": "str",
                        "selected": True,
                    },
                ],
            },
        ],
    }


def _api_input_node(node_id: str, config: dict[str, Any]) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=NodeType.API_INPUT, config=config),
    )


def main() -> None:
    original_root = _get_project_root()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        set_project_root(tmp_path)
        _preview_cache.invalidate()
        try:
            data_path = tmp_path / "data.json"
            data_path.write_text(json.dumps(_rating_records()))
            config = _multi_port_config(data_path)

            # Build the v2 per-port cache in the isolated tree (mirrors the
            # /api/json-cache/build endpoint that the editor calls).
            cache_dir = _json_cache_dir(data_path, "working")
            build_per_port_cache(data_path, config, cache_dir)

            # apiInput (multi-port) -> one downstream consumer wired to the
            # first port. Targeting the consumer forces the whole graph to
            # execute so the SOURCE node's preview NodeResult is produced too
            # (exactly the situation where the source node's own preview is
            # rendered in the editor).
            graph = PipelineGraph(
                nodes=[
                    _api_input_node("api", config),
                    GraphNode(
                        id="d_policies",
                        data=NodeData(
                            label="d_policies",
                            nodeType=NodeType.POLARS,
                            config={},  # no code -> passthrough
                        ),
                    ),
                ],
                edges=[
                    GraphEdge(
                        id="e_p",
                        source="api",
                        target="d_policies",
                        sourceHandle="policies",
                    ),
                ],
            )

            results = execute_graph(graph, target_node_id="d_policies")

            api = results["api"]
            print("api.status          =", api.status)
            print("api.row_count       =", api.row_count)
            print("api.column_count    =", api.column_count)
            print("api.columns         =", [c.name for c in api.columns])

            # Sanity: the source must have executed OK and report the first
            # port's row count correctly (policies = 2 records).
            assert api.status == "ok", f"source node errored: {api.error!r}"
            assert api.row_count == 2, (
                f"expected row_count==2 (policies first port), got {api.row_count}"
            )

            # The representative first-port frame ("policies") has exactly one
            # real column: policy_id. A correct preview MUST surface it.
            expected_columns = ["policy_id"]
            actual_columns = [c.name for c in api.columns]

            # ---- THE BUG ASSERTION ----------------------------------------
            # If the bug is present, column_count==0 and columns==[] even
            # though row_count is correct and a real first-port frame exists.
            bug_present = api.column_count == 0 and actual_columns == []
            print("BUG PRESENT (column_count==0 & columns==[]):", bug_present)

            assert api.column_count == len(expected_columns), (
                "V011 REPRODUCED: multi-port apiInput source reports the WRONG "
                f"column_count. expected {len(expected_columns)} (cols "
                f"{expected_columns}) but got {api.column_count} (cols "
                f"{actual_columns}). row_count was reported correctly "
                f"({api.row_count}), proving the frame exists and only the "
                "column metadata is dropped."
            )
            assert actual_columns == expected_columns, (
                "V011 REPRODUCED: multi-port apiInput source reports the WRONG "
                f"columns. expected {expected_columns} but got {actual_columns}."
            )

            print("NO BUG: source node correctly reports its first-port columns.")
        finally:
            set_project_root(original_root)
            _preview_cache.invalidate()


if __name__ == "__main__":
    main()
