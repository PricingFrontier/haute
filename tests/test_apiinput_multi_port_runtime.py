"""API Input multi-port runtime (MULTI_FRAME_PLAN commit 4).

Exercises the v2 apiInput at runtime end-to-end through execute_graph:

- 0 emit-true tables → preview fails loud with a configuration message.
- 1 emit-true table → single-port shorthand; source emits a bare
  LazyFrame; downstream edges with null ``sourceHandle`` bind correctly.
- 2+ emit-true tables → source emits a dict[port_label, LazyFrame]; the
  executor's edge-resolution picks per edge via ``sourceHandle``.
- Cache absent → fail with the "click Cache as Parquet" hint.
- Multi-port edge with null ``sourceHandle`` → executor raises a clear
  diagnostic naming the available ports.

The test pipelines use only DATA_SOURCE + POLARS + API_INPUT to keep
the surface focused on routing, not transform semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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


def _single_port_config(data_path: Path) -> dict[str, Any]:
    return {
        "path": str(data_path),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[:].policy_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
        ],
    }


def _multi_port_config(data_path: Path) -> dict[str, Any]:
    return {
        "path": str(data_path),
        "contract": "opaque",
        "tables": [
            {
                "path": "$[:]",
                "label": "policies",
                "emit": True,
                "columns": [
                    {
                        "name": "policy_id",
                        "path": "$[:].policy_id",
                        "type": "int",
                        "selected": True,
                    },
                ],
            },
            {
                "path": "$[:].drivers[:]",
                "label": "drivers",
                "emit": True,
                "columns": [
                    {
                        "name": "driver_id",
                        "path": "$[:].drivers[:].driver_id",
                        "type": "int",
                        "selected": True,
                    },
                    {
                        "name": "age_band",
                        "path": "$[:].drivers[:].age_band",
                        "type": "str",
                        "selected": True,
                    },
                ],
            },
        ],
    }


@pytest.fixture()
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set the sandbox project root to tmp_path so apiInput data + cache
    live in the test's isolated tree. Restore on teardown."""
    monkeypatch.chdir(tmp_path)
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.invalidate()
    yield tmp_path
    set_project_root(original)
    _preview_cache.invalidate()


def _api_input_node(node_id: str, config: dict[str, Any]) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(label=node_id, nodeType=NodeType.API_INPUT, config=config),
    )


def _build_cache_for(tmp_path: Path, data_path: Path, config: dict[str, Any]) -> None:
    """Build the v2 per-port cache for *data_path* using *config*. Mirrors
    what the /api/json-cache/build endpoint does (commit 3)."""
    cache_dir = _json_cache_dir(data_path, "working")
    build_per_port_cache(data_path, config, cache_dir)


# ─── 1. zero-emit-true: preview fails loud ────────────────────────


def test_zero_emit_true_fails_loud(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)
    config["tables"][0]["emit"] = False  # turn the lone table OFF

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])
    results = execute_graph(graph, target_node_id="api")
    assert results["api"].status == "error"
    error_msg = (results["api"].error or "").lower()
    assert "emit" in error_msg
    assert "tick" in error_msg or "configure" in error_msg or "configuration" in error_msg


# ─── 2. single-port: bare frame, null-sourceHandle edge works ─────


def test_single_port_emits_bare_frame_through_passthrough_consumer(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    # apiInput → polars (passthrough, code-free → uses _passthrough_fn).
    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="downstream",
                data=NodeData(
                    label="downstream",
                    nodeType=NodeType.POLARS,
                    config={},  # no code → _passthrough_fn
                ),
            ),
        ],
        edges=[
            # Single-port: null sourceHandle (the shorthand).
            GraphEdge(id="e", source="api", target="downstream", sourceHandle=None),
        ],
    )
    results = execute_graph(graph, target_node_id="downstream")
    assert results["api"].status == "ok"
    assert results["downstream"].status == "ok", results["downstream"].error
    # Source emitted exactly one frame (the policies table).
    assert results["api"].row_count == 2
    # Downstream passed it through unchanged.
    assert results["downstream"].row_count == 2


# ─── 3. multi-port: dict emit, sourceHandle picks per edge ────────


def test_multi_port_routes_per_edge_via_source_handle(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    # apiInput → two downstream consumers, each wired to a different port.
    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="d_policies",
                data=NodeData(label="d_policies", nodeType=NodeType.POLARS, config={}),
            ),
            GraphNode(
                id="d_drivers",
                data=NodeData(label="d_drivers", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            GraphEdge(
                id="e_p",
                source="api",
                target="d_policies",
                sourceHandle="policies",
            ),
            GraphEdge(
                id="e_d",
                source="api",
                target="d_drivers",
                sourceHandle="drivers",
            ),
        ],
    )
    p_results = execute_graph(graph, target_node_id="d_policies")
    assert p_results["d_policies"].status == "ok", p_results["d_policies"].error
    assert p_results["d_policies"].row_count == 2  # policies: 2 records

    d_results = execute_graph(graph, target_node_id="d_drivers")
    assert d_results["d_drivers"].status == "ok", d_results["d_drivers"].error
    assert d_results["d_drivers"].row_count == 3  # drivers: 3 across both policies


def test_multi_port_row_limit_caps_each_port(isolated_root) -> None:
    """A preview ``row_limit`` reaches each multi-port port (cap-fix).

    The ``drivers`` port has 3 rows across both policies; with ``row_limit=1``
    the collected port — and its downstream consumer — sees 1, matching how a
    single-frame source caps. Pre-fix the dict-emit branch ignored ``row_limit``
    and each port collected in full (this asserted 3).
    """
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="d_drivers",
                data=NodeData(label="d_drivers", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            GraphEdge(id="e_d", source="api", target="d_drivers", sourceHandle="drivers"),
        ],
    )
    results = execute_graph(graph, target_node_id="d_drivers", row_limit=1)
    assert results["d_drivers"].status == "ok", results["d_drivers"].error
    assert results["d_drivers"].row_count == 1  # capped from 3


# ─── 4. cache absent: clear error message ─────────────────────────


def test_runtime_fails_when_cache_missing(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)
    # Do NOT call _build_cache_for here — cache is absent.

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])
    results = execute_graph(graph, target_node_id="api")
    assert results["api"].status == "error"
    error_msg = (results["api"].error or "").lower()
    assert "cache" in error_msg and "parquet" in error_msg


# ─── 5. multi-port edge with null sourceHandle: loud error ────────


def test_multi_port_with_null_source_handle_raises(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="downstream",
                data=NodeData(label="downstream", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            # BAD: sourceHandle=None against a multi-port source.
            GraphEdge(id="e", source="api", target="downstream", sourceHandle=None),
        ],
    )
    results = execute_graph(graph, target_node_id="downstream")
    assert results["downstream"].status == "error"
    error_msg = results["downstream"].error or ""
    assert "no sourceHandle" in error_msg or "multi-port" in error_msg
    # The error names the available ports so the user can pick.
    assert "policies" in error_msg or "drivers" in error_msg


# ─── 6. multi-port edge with unknown sourceHandle: loud error ─────


def test_multi_port_with_unknown_source_handle_raises(isolated_root) -> None:
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="downstream",
                data=NodeData(label="downstream", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            # BAD: sourceHandle naming a port the source doesn't emit.
            GraphEdge(
                id="e",
                source="api",
                target="downstream",
                sourceHandle="non_existent_port",
            ),
        ],
    )
    results = execute_graph(graph, target_node_id="downstream")
    assert results["downstream"].status == "error"
    error_msg = results["downstream"].error or ""
    assert "non_existent_port" in error_msg
    # Names what IS available.
    assert "policies" in error_msg or "drivers" in error_msg


# ─── 7. preview-cache invalidation interacts correctly ────────────


def test_zero_columns_on_emitting_table_distinct_error(isolated_root) -> None:
    """emit:true with NO selected columns is its own error state — used
    to silently collapse to the no-emit error.

    Adversarial review caught this — the runtime now distinguishes
    between "no emitting tables" and "emitting tables but no selected
    columns" so the user sees the actual missing step.
    """
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)
    # Keep emit:true but un-select all columns.
    config["tables"][0]["columns"][0]["selected"] = False

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])
    results = execute_graph(graph, target_node_id="api")
    assert results["api"].status == "error"
    error_msg = (results["api"].error or "").lower()
    # Should mention "column" specifically, not just "emit".
    assert "column" in error_msg
    # And should name which tables are emit-true so the user knows
    # where to tick a column.
    assert "policies" in error_msg


def test_build_noop_when_fingerprint_matches(isolated_root) -> None:
    """Second build with the same v2 config is a no-op — doesn't
    re-shred and doesn't bump meta.json mtime, so commit 1's
    preview-cache invalidation doesn't thrash on repeated cache-button
    clicks.
    """
    from haute._json_flatten import _json_cache_dir
    from haute._json_shred import build_per_port_cache as _build

    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)
    cache_dir = _json_cache_dir(str(data_path), "working")

    _build(str(data_path), config, cache_dir)
    meta_path = cache_dir / "meta.json"
    first_mtime = meta_path.stat().st_mtime

    # Second build with the SAME schema; meta.json mtime should be
    # unchanged.
    _build(str(data_path), config, cache_dir)
    second_mtime = meta_path.stat().st_mtime

    assert first_mtime == second_mtime, (
        "no-op trapdoor failed: a fingerprint-matching rebuild shouldn't rewrite meta.json"
    )


def test_two_consumers_different_ports_dont_collide_on_column_cache(isolated_root) -> None:
    """Regression: ``column_cache`` was keyed by parent_node_id alone, so
    two consumers picking different ports of the SAME multi-port source
    collided on the same cache entry and the second consumer's
    contract-input check saw the first port's columns.

    The fix keyed the cache by ``(parent_id, sourceHandle)``. This test
    asserts the routing still produces the right per-port frames AND that
    each consumer sees its own port's columns.
    """
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="c_policies",
                data=NodeData(label="c_policies", nodeType=NodeType.POLARS, config={}),
            ),
            GraphNode(
                id="c_drivers",
                data=NodeData(label="c_drivers", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            GraphEdge(id="e_p", source="api", target="c_policies", sourceHandle="policies"),
            GraphEdge(id="e_d", source="api", target="c_drivers", sourceHandle="drivers"),
        ],
    )

    # Each consumer's preview must show its own port's columns + row count.
    p_results = execute_graph(graph, target_node_id="c_policies")
    assert p_results["c_policies"].status == "ok"
    p_col_names = {c.name for c in p_results["c_policies"].columns}
    assert p_col_names == {"policy_id"}, "policies port should expose only policy_id, got " + str(
        p_col_names
    )
    assert p_results["c_policies"].row_count == 2

    d_results = execute_graph(graph, target_node_id="c_drivers")
    assert d_results["c_drivers"].status == "ok"
    d_col_names = {c.name for c in d_results["c_drivers"].columns}
    assert d_col_names == {"driver_id", "age_band"}, (
        "drivers port should expose driver_id + age_band, got " + str(d_col_names)
    )
    assert d_results["c_drivers"].row_count == 3


def test_multi_port_node_result_carries_per_frame_columns(isolated_root) -> None:
    """D1=(2) backend per-frame column exposure (executor layer).

    Previewing a multi-port apiInput at its own node must surface each
    emit-true table's column schema on ``NodeResult.frame_columns``,
    keyed by the emit-table label (the port name a downstream edge binds
    to via ``sourceHandle``). This is the per-(node, port) information the
    executor already computes in ``column_cache``, now exposed so the
    OUTPUT editor can read every incoming frame's columns for ANY source
    type — not by re-reading apiInput config client-side.

    ``columns`` (the single representative frame) is left intact:
    ``frame_columns`` is strictly additive.
    """
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])
    results = execute_graph(graph, target_node_id="api")

    assert results["api"].status == "ok", results["api"].error

    frame_columns = results["api"].frame_columns
    # Keyed by emit-table label, one entry per emit-true table.
    assert set(frame_columns) == {"policies", "drivers"}
    assert {c.name for c in frame_columns["policies"]} == {"policy_id"}
    assert {c.name for c in frame_columns["drivers"]} == {"driver_id", "age_band"}
    # dtypes flow through too (not just names).
    policy_dtypes = {c.name: c.dtype for c in frame_columns["policies"]}
    assert policy_dtypes["policy_id"].lower().startswith("int")

    # Additive: the multi-port emit branch reports an empty flat
    # ``columns`` (a multi-port node has no single representative schema —
    # that's precisely why ``frame_columns`` exists). The new field is the
    # only column surface for these nodes; it does not perturb ``columns``.
    assert results["api"].columns == []


def test_single_frame_node_has_empty_frame_columns(isolated_root) -> None:
    """A single-frame producer leaves ``frame_columns`` empty — the
    consumer falls back to ``columns``. Guards against accidentally
    populating per-frame columns for the common single-port case (which
    would bloat every preview response).
    """
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])
    results = execute_graph(graph, target_node_id="api")

    assert results["api"].status == "ok", results["api"].error
    assert results["api"].frame_columns == {}
    assert {c.name for c in results["api"].columns} == {"policy_id"}


def test_preview_route_response_carries_node_frame_columns(isolated_root) -> None:
    """End-to-end through the real ``/api/pipeline/preview`` route: a
    multi-port apiInput graph's preview response carries
    ``node_frame_columns`` keyed node_id → emit-table label → columns.

    This is the field + shape the frontend OutputEditor frameColumns swap
    will consume. Asserting at the route (not just the executor) confirms
    the field survives Pydantic serialisation in ``PreviewNodeResponse``.
    """
    from fastapi.testclient import TestClient

    from haute.server import app

    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/pipeline/preview",
        json={"graph": graph.model_dump(), "node_id": "api"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # node_columns (the pre-existing sibling) is unchanged and additive.
    assert "node_columns" in body
    # The new per-frame field is present and keyed by node id.
    node_frame_columns = body["node_frame_columns"]
    assert "api" in node_frame_columns
    api_frames = node_frame_columns["api"]
    assert set(api_frames) == {"policies", "drivers"}
    assert {c["name"] for c in api_frames["policies"]} == {"policy_id"}
    assert {c["name"] for c in api_frames["drivers"]} == {"driver_id", "age_band"}
    # Each ColumnInfo carries name + dtype.
    assert all({"name", "dtype"} <= set(c) for c in api_frames["drivers"])


# ─── 8. lazy-gating: non-target multi-port ancestor stays lazy ────


def _graph_apiinput_upstream_of_target(config: dict[str, Any]) -> PipelineGraph:
    """apiInput ``api`` (multi-port) → polars ``d_policies`` (the target).

    The apiInput is an UPSTREAM ANCESTOR of the previewed target, not the
    target itself — the case where lazy-gating must keep it un-collected.
    """
    return PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="d_policies",
                data=NodeData(label="d_policies", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            GraphEdge(id="e_p", source="api", target="d_policies", sourceHandle="policies"),
        ],
    )


def test_multi_port_ancestor_not_collected_under_target_preview(isolated_root) -> None:
    """LAZINESS GATE: a non-target multi-port ancestor is NOT materialised.

    Under ``target_preview_only`` the executor passes
    ``materialize_node_ids={target}``. A multi-port apiInput that is an
    ANCESTOR of the target (not the target) must then behave like a
    single-frame lazy ancestor: its per-port frames stay LazyFrames in
    ``runtime_outputs`` (routing only) and it is ABSENT from
    ``eager_outputs`` — no collect happened.

    This is the new invariant. Against the OLD force-collect it fails:
    the ancestor was unconditionally collected into ``eager_outputs`` as a
    ``dict[label, DataFrame]``.
    """
    import polars as pl

    from haute.executor import _eager_execute

    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = _graph_apiinput_upstream_of_target(config)

    (
        raw_outputs,
        _order,
        errors,
        *_rest,
    ) = _eager_execute(
        graph,
        target_node_id="d_policies",
        row_limit=None,
        materialize_node_ids={"d_policies"},
    )

    # No node errored.
    assert errors == {}, errors
    # The ANCESTOR apiInput must NOT be materialised — absent from
    # eager_outputs entirely (its lazy ports live only in runtime_outputs,
    # which _eager_execute does not surface). Mirrors a single-frame lazy
    # ancestor.
    assert raw_outputs.get("api") is None, (
        "multi-port ancestor was collected into eager_outputs (force-collect "
        f"regression); got {type(raw_outputs.get('api')).__name__}"
    )
    # The TARGET, by contrast, IS materialised (a real DataFrame).
    assert isinstance(raw_outputs.get("d_policies"), pl.DataFrame)


def test_multi_port_target_still_collected_when_it_is_the_target(isolated_root) -> None:
    """Control for the gate: when the multi-port apiInput IS the target,
    it stays materialised — ``dict[label, DataFrame]`` in ``eager_outputs``
    exactly as before. The gate only suppresses collection of ANCESTORS.
    """
    import polars as pl

    from haute.executor import _eager_execute

    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])

    (raw_outputs, *_rest) = _eager_execute(
        graph,
        target_node_id="api",
        row_limit=None,
        materialize_node_ids={"api"},
    )

    api_out = raw_outputs.get("api")
    assert isinstance(api_out, dict), (
        "multi-port TARGET should materialise a dict[label, DataFrame]; got "
        f"{type(api_out).__name__}"
    )
    assert set(api_out) == {"policies", "drivers"}
    assert all(isinstance(v, pl.DataFrame) for v in api_out.values())


def test_multi_port_ancestor_node_frame_columns_via_route(isolated_root) -> None:
    """ANCESTOR per-frame columns: previewing a target whose multi-port
    apiInput is an UPSTREAM ANCESTOR (not the target) still surfaces the
    apiInput's per-port columns on the preview's ``node_frame_columns``.

    This is the user-facing payoff of the schema lookup: the OUTPUT editor
    learns every incoming frame's schema for an ancestor apiInput WITHOUT
    that ancestor being materialised. Asserting at the real route confirms
    the field survives Pydantic serialisation.
    """
    from fastapi.testclient import TestClient

    from haute.server import app

    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = _graph_apiinput_upstream_of_target(config)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/pipeline/preview",
        json={"graph": graph.model_dump(), "node_id": "d_policies"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The previewed target is downstream; the apiInput ancestor's per-port
    # columns must still appear under its node id.
    node_frame_columns = body["node_frame_columns"]
    assert "api" in node_frame_columns, (
        "ancestor apiInput missing from node_frame_columns — per-frame schema "
        "was not surfaced without materialisation"
    )
    api_frames = node_frame_columns["api"]
    assert set(api_frames) == {"policies", "drivers"}
    assert {c["name"] for c in api_frames["policies"]} == {"policy_id"}
    assert {c["name"] for c in api_frames["drivers"]} == {"driver_id", "age_band"}
    # dtypes flow through (name+dtype), not just names.
    assert all({"name", "dtype"} <= set(c) for c in api_frames["drivers"])


def test_multi_port_ancestor_row_limit_caps_collected_target(isolated_root) -> None:
    """CAP (ancestor variant): a preview ``row_limit`` reaches a multi-port
    ancestor's lazy per-port plan, so the head-cap survives into the
    collected downstream target. The ``drivers`` port has 3 rows; with
    ``row_limit=1`` the lazy-gated ancestor head-caps each port before its
    consumer collects, so the target sees 1.

    Pre-fix the dict branch ignored ``row_limit`` and the ancestor's ports
    flowed in full (this would assert 3 downstream).
    """
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _multi_port_config(data_path)
    _build_cache_for(isolated_root, data_path, config)

    graph = PipelineGraph(
        nodes=[
            _api_input_node("api", config),
            GraphNode(
                id="d_drivers",
                data=NodeData(label="d_drivers", nodeType=NodeType.POLARS, config={}),
            ),
        ],
        edges=[
            GraphEdge(id="e_d", source="api", target="d_drivers", sourceHandle="drivers"),
        ],
    )

    # target_preview_only=True engages the lazy gate on the apiInput ancestor.
    results = execute_graph(
        graph,
        target_node_id="d_drivers",
        row_limit=1,
        target_preview_only=True,
        include_schema_metadata=True,
    )
    assert results["d_drivers"].status == "ok", results["d_drivers"].error
    assert results["d_drivers"].row_count == 1  # capped from 3 through a lazy ancestor


def test_apiinput_preview_invalidates_when_cache_rebuilt(isolated_root) -> None:
    """Bug-class regression for the commit 1 invalidation: a v2 apiInput
    whose cache gets rebuilt mid-session must show fresh data on the next
    preview, not the stale failure cached from a previous attempt.
    """
    data_path = isolated_root / "data.json"
    data_path.write_text(json.dumps(_rating_records()))
    config = _single_port_config(data_path)

    graph = PipelineGraph(nodes=[_api_input_node("api", config)], edges=[])

    # 1. Preview before any cache build: expect error.
    first = execute_graph(graph, target_node_id="api")
    assert first["api"].status == "error"

    # 2. Build the cache out-of-band.
    _build_cache_for(isolated_root, data_path, config)

    # 3. Preview again — should now succeed (commit 1's per-data-file
    # mtime in the preview fingerprint forces a cache miss after the
    # meta.json appears).
    second = execute_graph(graph, target_node_id="api")
    assert second["api"].status == "ok", second["api"].error
    assert second["api"].row_count == 2
