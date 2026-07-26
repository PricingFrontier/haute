"""Trace through a multi-frame (≥2-table) apiInput.

Multi-frame sources store ``dict[label, DataFrame]`` in ``eager_outputs``,
but the trace correlation assumed bare DataFrames and crashed with
``AttributeError: 'dict' object has no attribute 'columns'`` — surfaced to
the user as an opaque 500. The edge's ``sourceHandle`` names the frame each
child consumes (per ``_pick_source_frame``); the correlation walk must use
the same selection.

The selection is per-EDGE, not per (source, target) pair: a multi-frame
source can feed the SAME child through several edges, each consuming a
distinct frame (the canonical four-port apiInput → OUTPUT topology, or a
join of two data-levels straight off the input). Collapsing the edges to
one frame per pair correlates the source step against whichever frame's
edge happened to come last — silent-wrong lineage.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient

from haute._json_flatten import _json_cache_dir
from haute._json_shred import build_per_port_cache
from haute._sandbox import _get_project_root, set_project_root
from haute._trace_correlation import _correlate_rows_posthoc
from haute._types import NodeType
from haute.executor import _preview_cache
from haute.trace import execute_trace
from tests.conftest import make_graph
from tests.test_output_nested_roundtrip import _FIXTURE, _api_input_config, _output_mapping


def _multi_frame_graph(api_config: dict[str, Any], code: str) -> dict[str, Any]:
    """A multi-frame apiInput feeding a transform via the ``policies`` frame."""
    return {
        "nodes": [
            {
                "id": "api",
                "data": {"label": "api", "nodeType": "apiInput", "config": api_config},
            },
            {
                "id": "t",
                "data": {"label": "t", "nodeType": "polars", "config": {"code": code}},
            },
        ],
        "edges": [
            {"id": "e1", "source": "api", "target": "t", "sourceHandle": "policies"},
        ],
    }


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolated tmp project with the nested data-model fixture + built cache."""
    monkeypatch.chdir(tmp_path)
    original = _get_project_root()
    set_project_root(tmp_path)
    _preview_cache.clear()

    data_path = tmp_path / "data" / "data_model_example.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(_FIXTURE.read_text())
    build_per_port_cache(
        data_path,
        _api_input_config(data_path),
        _json_cache_dir(data_path, "working"),
    )
    yield data_path
    set_project_root(original)
    _preview_cache.clear()


def test_edge_join_with_multi_frame_base_correlates_join_parent() -> None:
    node_map = {
        "base": SimpleNamespace(
            id="base",
            data=SimpleNamespace(nodeType=NodeType.API_INPUT, config={}),
        ),
        "join": SimpleNamespace(
            id="join",
            data=SimpleNamespace(nodeType=NodeType.POLARS, config={}),
        ),
        "edge": SimpleNamespace(
            id="edge",
            data=SimpleNamespace(
                nodeType=NodeType.EDGE_JOIN,
                config={
                    "baseInput": "base",
                    "joinInput": "join",
                    "how": "left",
                    "on": ["policy_id"],
                    "suffix": "_right",
                },
            ),
        ),
    }
    eager_outputs = {
        "base": {
            "policies": pl.DataFrame({"policy_id": ["P1"], "territory": ["north"]}),
            "drivers": pl.DataFrame({"driver_id": ["D1"], "age": [42]}),
        },
        "join": pl.DataFrame({"policy_id": ["P1"], "territory": ["joined"], "rate": [1.2]}),
        "edge": pl.DataFrame(
            {
                "policy_id": ["P1"],
                "territory": ["north"],
                "territory_right": ["joined"],
                "rate": [1.2],
            }
        ),
    }

    rows = _correlate_rows_posthoc(
        eager_outputs,
        order=["base", "join", "edge"],
        parents_of={"edge": ["base", "join"]},
        target_node_id="edge",
        row_index=0,
        node_map=node_map,
        source_frames_of={("base", "edge"): ["policies"]},
        traced_column="rate",
    )

    assert rows["base"] == {"policy_id": "P1", "territory": "north"}
    assert rows["join"] == {
        "policy_id": "P1",
        "territory": "joined",
        "rate": 1.2,
    }


def test_trace_through_multi_frame_source_succeeds(project: Path) -> None:
    """Tracing a node downstream of a multi-frame apiInput must correlate
    through the frame the edge's sourceHandle names — not crash."""
    graph = make_graph(
        _multi_frame_graph(
            _api_input_config(project),
            "df = df.with_columns(pid2=pl.col('policy_id') * 2)",
        )
    )

    result = execute_trace(graph, row_index=0, target_node_id="t", column="pid2")

    steps = {s.node_id: s for s in result.steps}
    assert "t" in steps and "api" in steps
    t_row = steps["t"].output_values
    api_row = steps["api"].output_values
    # The api step's row comes from the policies frame and matches the
    # traced child row on the shared key.
    assert api_row.get("policy_id") == t_row.get("policy_id")
    assert t_row.get("pid2") == t_row.get("policy_id") * 2


def test_trace_target_on_multi_frame_source_is_clear_error(project: Path) -> None:
    """Tracing the bundle node itself cannot pick a frame — must be a clear
    ValueError, not an AttributeError crash."""
    graph = make_graph(
        _multi_frame_graph(
            _api_input_config(project),
            "df = df.with_columns(pid2=pl.col('policy_id') * 2)",
        )
    )

    with pytest.raises(ValueError, match="multiple frames"):
        execute_trace(graph, row_index=0, target_node_id="api", column="policy_id")


def test_trace_route_multi_frame_not_opaque_500(project: Path) -> None:
    """The trace route must not map a multi-frame trace to an opaque 500."""
    from haute.server import app

    client = TestClient(app)
    graph = _multi_frame_graph(
        _api_input_config(project),
        "df = df.with_columns(pid2=pl.col('policy_id') * 2)",
    )

    resp = client.post(
        "/api/pipeline/trace",
        json={"graph": graph, "row_index": 0, "target_node_id": "t", "column": "pid2"},
    )
    assert resp.status_code == 200, resp.text

    # Bundle-node target: a clear 4xx naming the problem, not a 500.
    resp = client.post(
        "/api/pipeline/trace",
        json={
            "graph": graph,
            "row_index": 0,
            "target_node_id": "api",
            "column": "policy_id",
        },
    )
    assert resp.status_code == 400
    assert "frame" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Per-edge frame selection: one source feeding one child via SEVERAL edges
# ---------------------------------------------------------------------------

_PORTS = ["policies", "drivers", "licenses", "vehicles"]


def _multi_edge_output_graph(api_config: dict[str, Any]) -> dict[str, Any]:
    """The canonical four-port apiInput → OUTPUT topology: four edges between
    the SAME (source, target) pair, each with a distinct ``sourceHandle``."""
    return {
        "nodes": [
            {
                "id": "api",
                "data": {
                    "label": "api",
                    "nodeType": NodeType.API_INPUT.value,
                    "config": api_config,
                },
            },
            {
                "id": "out",
                "data": {
                    "label": "out",
                    "nodeType": NodeType.OUTPUT.value,
                    "config": {"outputMapping": _output_mapping(), "outputFormat": "json"},
                },
            },
        ],
        "edges": [
            {"id": f"e_{p}", "source": "api", "target": "out", "sourceHandle": p} for p in _PORTS
        ],
    }


def _same_source_join_graph(api_config: dict[str, Any]) -> dict[str, Any]:
    """One apiInput feeding a single polars node through TWO edges (drivers +
    vehicles) — a join of two data-levels straight off the input.

    Each edge's frame label is also its executable argument name, so the code
    joins ``drivers`` with ``vehicles`` on their shared ancestor key.
    """
    return {
        "nodes": [
            {
                "id": "api",
                "data": {
                    "label": "api",
                    "nodeType": NodeType.API_INPUT.value,
                    "config": api_config,
                },
            },
            {
                "id": "j",
                "data": {
                    "label": "j",
                    "nodeType": "polars",
                    "config": {"code": "df = drivers.join(vehicles, on='policy_id')"},
                },
            },
        ],
        "edges": [
            {"id": "e_d", "source": "api", "target": "j", "sourceHandle": "drivers"},
            {"id": "e_v", "source": "api", "target": "j", "sourceHandle": "vehicles"},
        ],
    }


def test_trace_multi_edge_output_correlates_source_to_root_frame(project: Path) -> None:
    """Four edges api→out: the source step must correlate against the frame
    that actually identifies the traced row (the root ``policies`` frame),
    not whichever edge's sourceHandle happened to come last in the edge list.
    """
    graph = make_graph(_multi_edge_output_graph(_api_input_config(project)))

    result = execute_trace(graph, row_index=0, target_node_id="out", column="policy_id")

    steps = {s.node_id: s for s in result.steps}
    assert "api" in steps, f"api step missing — source frame unresolved: {result.steps}"
    out_row = steps["out"].output_values
    api_row = steps["api"].output_values
    # The policies frame carries exactly one column; its row for this
    # document is the correct lineage row.
    assert api_row == {"policy_id": out_row["policy_id"]}


def test_trace_same_source_join_resolves_frame_per_traced_column(project: Path) -> None:
    """Two edges api→j consuming distinct frames: tracing a drivers-frame
    column must correlate the source step to the drivers row that fed the
    child row; tracing a vehicles-frame column to the vehicles row."""
    graph = make_graph(_same_source_join_graph(_api_input_config(project)))

    result_d = execute_trace(graph, row_index=0, target_node_id="j", column="age_band")
    steps_d = {s.node_id: s for s in result_d.steps}
    assert "api" in steps_d, f"api step missing when tracing age_band: {result_d.steps}"
    j_row = steps_d["j"].output_values
    api_row = steps_d["api"].output_values
    assert set(api_row) == {"policy_id", "driver_id", "main", "age_band"}
    assert api_row["policy_id"] == j_row["policy_id"]
    assert api_row["driver_id"] == j_row["driver_id"]
    assert api_row["age_band"] == j_row["age_band"]

    result_v = execute_trace(graph, row_index=0, target_node_id="j", column="engine_size")
    steps_v = {s.node_id: s for s in result_v.steps}
    assert "api" in steps_v, f"api step missing when tracing engine_size: {result_v.steps}"
    j_row_v = steps_v["j"].output_values
    api_row_v = steps_v["api"].output_values
    assert set(api_row_v) == {"policy_id", "vehicle_id", "engine_size", "class_of_use"}
    assert api_row_v["policy_id"] == j_row_v["policy_id"]
    assert api_row_v["vehicle_id"] == j_row_v["vehicle_id"]
    assert api_row_v["engine_size"] == j_row_v["engine_size"]


# ---------------------------------------------------------------------------
# Enrichment: bundle-aware row counts and per-edge dtype scoping
# ---------------------------------------------------------------------------


def test_bundle_output_row_count_counts_rows_not_frames() -> None:
    """A multi-frame node's output row count derives from its frames' rows
    (max across frames, mirroring the parent-side handling in
    ``enrich_steps``) — never ``len(dict)``, which counts FRAMES.

    Guards the row-lineage input for a bundle node appearing as an
    intermediate step; today ``detect_row_lineage_type`` short-circuits
    apiInput to "created" so the miscount is masked, but the wrong count
    must not survive to bite the next non-apiInput bundle emitter.
    """
    from haute._trace_enrichment import _node_output_row_count

    frames = {
        "a": pl.DataFrame({"x": [1, 2, 3]}),
        "b": pl.DataFrame({"y": [1, 2, 3, 4, 5]}),
    }
    assert _node_output_row_count(frames) == 5
    assert _node_output_row_count(pl.DataFrame({"x": [1, 2]})) == 2
    assert _node_output_row_count(None) == 0
    assert _node_output_row_count({}) == 0


def test_banding_factor_dtypes_scoped_to_consumed_frame(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A banding node fed by ONE frame of a multi-frame source must resolve
    factor dtypes from that frame only — not a dict-iteration-order merge of
    every emitted frame, where a column name recurring across frames with a
    different dtype wins by table order and drives the Float32-faithful
    continuous-rule re-match in the wrong numeric domain."""
    # Rebuild the cache with the drivers frame's copy of the ancestor key
    # declared float — the policies frame keeps it int, and policies comes
    # FIRST in table (and so dict-iteration) order.
    config = copy.deepcopy(_api_input_config(project))
    drivers_table = next(t for t in config["tables"] if t["label"] == "drivers")
    next(c for c in drivers_table["columns"] if c["name"] == "policy_id")["type"] = "float"
    build_per_port_cache(project, config, _json_cache_dir(project, "working"))
    _preview_cache.clear()

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "api",
                    "data": {
                        "label": "api",
                        "nodeType": NodeType.API_INPUT.value,
                        "config": config,
                    },
                },
                {
                    "id": "band",
                    "data": {"label": "band", "nodeType": "banding", "config": {"factors": []}},
                },
            ],
            "edges": [
                {"id": "e1", "source": "api", "target": "band", "sourceHandle": "drivers"},
            ],
        }
    )

    captured: dict[str, Any] = {}

    def _spy_enrich_banding(*args: Any, **kwargs: Any) -> None:
        captured["factor_input_dtypes"] = kwargs.get("factor_input_dtypes")
        return None

    import haute._trace_enrichment as trace_enrichment

    monkeypatch.setattr(trace_enrichment, "enrich_banding", _spy_enrich_banding)

    execute_trace(graph, row_index=0, target_node_id="band")

    dtypes = captured["factor_input_dtypes"]
    assert dtypes is not None
    # Scoped to the drivers frame the edge consumes — no columns leaked in
    # from the other three frames.
    assert set(dtypes) == {"policy_id", "driver_id", "main", "age_band"}
    # And the shared ancestor key resolves in the CONSUMED frame's dtype.
    assert dtypes["policy_id"] == pl.Float64


def test_rating_factor_dtypes_scoped_to_consumed_frame(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rating enrichment uses the same consumed-frame dtype scoping."""
    config = copy.deepcopy(_api_input_config(project))
    drivers_table = next(t for t in config["tables"] if t["label"] == "drivers")
    next(c for c in drivers_table["columns"] if c["name"] == "policy_id")["type"] = "float"
    build_per_port_cache(project, config, _json_cache_dir(project, "working"))
    _preview_cache.clear()

    graph = make_graph(
        {
            "nodes": [
                {
                    "id": "api",
                    "data": {
                        "label": "api",
                        "nodeType": NodeType.API_INPUT.value,
                        "config": config,
                    },
                },
                {
                    "id": "rating",
                    "data": {
                        "label": "rating",
                        "nodeType": NodeType.RATING_STEP.value,
                        "config": {"tables": []},
                    },
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "api",
                    "target": "rating",
                    "sourceHandle": "drivers",
                },
            ],
        }
    )

    captured: dict[str, Any] = {}

    def _spy_enrich_rating_step(*args: Any, **kwargs: Any) -> None:
        captured["factor_input_dtypes"] = kwargs.get("factor_input_dtypes")
        return None

    import haute._trace_enrichment as trace_enrichment

    monkeypatch.setattr(trace_enrichment, "enrich_rating_step", _spy_enrich_rating_step)

    execute_trace(graph, row_index=0, target_node_id="rating")

    dtypes = captured["factor_input_dtypes"]
    assert dtypes is not None
    assert set(dtypes) == {"policy_id", "driver_id", "main", "age_band"}
    assert dtypes["policy_id"] == pl.Float64
