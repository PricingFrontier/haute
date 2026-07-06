"""Isolated reproduction for V014.

Claim: ``executor._cached_output_names`` raises ``AttributeError`` when a
multi-port apiInput source is a direct parent of an ``instanceOf`` node.
For a multi-port apiInput, ``output_columns[nid] == []`` (falsy) and
``eager_outputs[nid]`` is a ``dict[port_label, DataFrame]`` (not a frame),
so ``_cached_output_names`` falls through to ``set(df.columns)`` on a dict.

That call sits in ``execute_graph``'s schema-warning loop, which is OUTSIDE
the per-node ``swallow_errors`` region, so the exception propagates out of
``execute_graph`` and fails the WHOLE preview/trace request rather than
degrading one node.

ISOLATION: all disk I/O lives under a Python tempdir; project root is set
via ``haute._sandbox.set_project_root``. No real project files are touched.
"""

from __future__ import annotations

import json
import tempfile
import traceback
from pathlib import Path
from typing import Any

from haute._json_shred import build_per_port_cache
from haute._json_flatten import _json_cache_dir
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
        {"policy_id": 1002, "drivers": [{"driver_id": 3, "age_band": "60+"}]},
    ]


def _multi_port_config(data_path: Path) -> dict[str, Any]:
    return {
        "path": str(data_path),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[*]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {"name": "policy_id", "path": "$[*].policy_id", "type": "int", "selected": True},
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

            # Build the v2 per-port cache so the multi-port apiInput emits a
            # dict[port_label, DataFrame] at runtime (exactly as in
            # tests/test_apiinput_multi_port_runtime.py).
            cache_dir = _json_cache_dir(str(data_path), "working")
            build_per_port_cache(str(data_path), config, cache_dir)

            # Graph: multi-port apiInput "api" is a DIRECT PARENT of an
            # instanceOf node "inst". "orig" exists so the ``ref in node_map``
            # guard in the schema-warning loop passes. The instance reads the
            # "drivers" port via sourceHandle (a perfectly valid wiring).
            graph = PipelineGraph(
                nodes=[
                    GraphNode(
                        id="api",
                        data=NodeData(
                            label="api", nodeType=NodeType.API_INPUT, config=config
                        ),
                    ),
                    GraphNode(
                        id="orig",
                        data=NodeData(label="orig", nodeType=NodeType.POLARS, config={}),
                    ),
                    GraphNode(
                        id="inst",
                        data=NodeData(
                            label="inst",
                            nodeType=NodeType.POLARS,
                            config={"instanceOf": "orig"},
                        ),
                    ),
                ],
                edges=[
                    GraphEdge(
                        id="e_inst",
                        source="api",
                        target="inst",
                        sourceHandle="drivers",
                    ),
                ],
            )

            # Full execution (target=None -> result_order = full topo order),
            # so the schema-warning loop visits "inst" and calls
            # _cached_output_names("api") on the multi-port parent.
            raised: BaseException | None = None
            try:
                results = execute_graph(graph, target_node_id=None)
            except BaseException as exc:  # noqa: BLE001 - we are characterising it
                raised = exc

            if raised is not None:
                tb = "".join(
                    traceback.format_exception(type(raised), raised, raised.__traceback__)
                )
                in_cached = "_cached_output_names" in tb
                is_attr_cols = isinstance(raised, AttributeError) and "columns" in str(raised)
                print("REPRO: execute_graph RAISED instead of returning per-node results")
                print(f"  exception type : {type(raised).__name__}")
                print(f"  exception args : {raised!r}")
                print(f"  via _cached_output_names: {in_cached}")
                print(f"  AttributeError on .columns: {is_attr_cols}")
                print("  --- traceback tail ---")
                for line in tb.strip().splitlines()[-8:]:
                    print("   " + line)

                # The bug is: a dict has no .columns, raised from the
                # schema-warning helper, escaping execute_graph entirely.
                assert isinstance(raised, AttributeError), (
                    f"expected AttributeError, got {type(raised).__name__}"
                )
                assert "columns" in str(raised), (
                    f"expected '.columns' AttributeError, got: {raised!r}"
                )
                assert in_cached, (
                    "AttributeError did not originate in _cached_output_names; "
                    "may be an unrelated failure"
                )
                print("\nV014 CONFIRMED: multi-port apiInput parent of an instanceOf "
                      "node makes execute_graph raise AttributeError (dict has no "
                      "'.columns'), failing the whole request.")
            else:
                # If we get here, execute_graph returned normally — the claim
                # would be refuted (no crash). Show what happened so we can
                # see whether the warning path was even exercised.
                print("execute_graph returned WITHOUT raising. Per-node statuses:")
                for nid, res in results.items():
                    print(f"  {nid}: status={res.status} error={res.error!r}")
                raise AssertionError(
                    "V014 NOT reproduced: execute_graph did not raise. "
                    "The schema-warning helper handled the multi-port dict."
                )
        finally:
            set_project_root(original_root)
            _preview_cache.invalidate()


if __name__ == "__main__":
    main()
