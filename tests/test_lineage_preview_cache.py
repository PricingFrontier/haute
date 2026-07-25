from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import polars as pl
import pytest

from haute._cache import (
    LineageCacheKeyRequest,
    lineage_cache_key,
    selected_live_switch_path,
)
from haute.projection import prepare_graph
from tests.conftest import make_edge, make_graph

pytestmark = pytest.mark.usefixtures("_widen_sandbox_root")


@pytest.fixture(autouse=True)
def _clear_preview_cache():
    from haute.executor import _preview_cache
    from haute.trace import _cache as trace_cache

    _preview_cache.invalidate()
    trace_cache.invalidate()
    yield
    _preview_cache.invalidate()
    trace_cache.invalidate()


def _graph():
    return make_graph(
        {
            "nodes": [
                {
                    "id": "source",
                    "data": {
                        "label": "source",
                        "nodeType": "dataInput",
                        "config": {
                            "inputType": "file",
                            "format": "parquet",
                            "mode": "scan",
                            "cacheMode": "direct",
                            "path": "input.parquet",
                            "arguments": {},
                        },
                    },
                },
                {
                    "id": "mid",
                    "data": {
                        "label": "mid",
                        "nodeType": "polars",
                        "config": {"code": "df = df.with_columns(y=pl.col('x') + 1)"},
                    },
                },
                {
                    "id": "target",
                    "data": {
                        "label": "target",
                        "nodeType": "polars",
                        "config": {"code": "df = df.select('y')"},
                    },
                },
                {
                    "id": "downstream",
                    "data": {
                        "label": "downstream",
                        "nodeType": "polars",
                        "config": {"code": "df = df.with_columns(z=pl.lit(1))"},
                    },
                },
                {
                    "id": "disconnected",
                    "data": {
                        "label": "disconnected",
                        "nodeType": "constant",
                        "config": {"values": [{"name": "u", "value": "1"}]},
                    },
                },
            ],
            "edges": [
                make_edge("source", "mid").model_dump(),
                make_edge("mid", "target").model_dump(),
                make_edge("target", "downstream").model_dump(),
            ],
            "preamble": "import polars as pl",
            "source_file": "pipelines/example.py",
            "preserved_blocks": ["# preserved"],
            "sources": ["live", "batch"],
            "active_source": "live",
        }
    )


def _request(graph=None, **updates) -> LineageCacheKeyRequest:
    graph = _graph() if graph is None else graph
    source = updates.pop("source", "live")
    prepared = updates.pop("prepared", prepare_graph(graph, "target", source=source))
    values = {
        "graph": graph,
        "prepared": prepared,
        "target_node_id": "target",
        "source": source,
        "requested_columns": ("y",),
        "initial_column_limit": None,
        "row_limit": 100,
        "port_label": None,
        "contract_fingerprint": "contracts:v1:on:target-only",
        "selected_live_switch_path": selected_live_switch_path(prepared),
        "runtime_input_fingerprint": "runtime:v1:source",
        "execution_semantics_version": "preview:v1",
    }
    values.update(updates)
    return LineageCacheKeyRequest(**values)


def _replace_node_config(graph, node_id: str, **config_updates):
    nodes = []
    for node in graph.nodes:
        if node.id != node_id:
            nodes.append(node)
            continue
        data = node.data.model_copy(update={"config": {**node.data.config, **config_updates}})
        nodes.append(node.model_copy(update={"data": data}))
    return graph.model_copy(update={"nodes": nodes})


def test_lineage_key_ignores_downstream_and_disconnected_edits() -> None:
    graph = _graph()
    baseline = lineage_cache_key(_request(graph))

    downstream = _replace_node_config(graph, "downstream", code="df = df.drop('z')")
    disconnected = _replace_node_config(
        graph,
        "disconnected",
        values=[{"name": "u", "value": "999"}],
    )

    assert lineage_cache_key(_request(downstream)) == baseline
    assert lineage_cache_key(_request(disconnected)) == baseline


def test_lineage_key_invalidates_relevant_config_and_wiring() -> None:
    graph = _graph()
    baseline = lineage_cache_key(_request(graph))
    changed_config = _replace_node_config(
        graph,
        "mid",
        code="df = df.with_columns(y=pl.col('x') + 2)",
    )
    rewired_edges = [
        edge.model_copy(update={"sourceHandle": "alternate"}) if edge.source == "source" else edge
        for edge in graph.edges
    ]
    changed_wiring = graph.model_copy(update={"edges": rewired_edges})

    assert lineage_cache_key(_request(changed_config)) != baseline
    assert lineage_cache_key(_request(changed_wiring)) != baseline


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("preamble", "import polars as pl\nVALUE = 1"),
        ("source_file", "pipelines/other.py"),
    ],
)
def test_lineage_key_invalidates_each_graph_level_identity_field(field, value) -> None:
    graph = _graph()
    changed = graph.model_copy(update={field: value})

    assert lineage_cache_key(_request(changed)) != lineage_cache_key(_request(graph))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pipeline_name", "other"),
        ("pipeline_description", "other description"),
        ("preserved_blocks", ["# other"]),
        ("sources", ["live", "stress"]),
        ("active_source", "batch"),
        ("warning", "display-only warning"),
    ],
)
def test_lineage_key_ignores_presentation_graph_fields(field, value) -> None:
    graph = _graph()
    changed = graph.model_copy(update={field: value})

    assert lineage_cache_key(_request(changed)) == lineage_cache_key(_request(graph))


def test_lineage_key_ignores_edge_display_id() -> None:
    graph = _graph()
    changed = graph.model_copy(
        update={
            "edges": [
                edge.model_copy(update={"id": f"display-{index}"})
                for index, edge in enumerate(graph.edges)
            ]
        }
    )

    assert lineage_cache_key(_request(changed)) == lineage_cache_key(_request(graph))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_node_id", "mid"),
        ("source", "batch"),
        ("requested_columns", ("x",)),
        ("requested_columns", ()),
        ("requested_columns", None),
        ("initial_column_limit", 200),
        ("row_limit", 101),
        ("port_label", "frame-b"),
        ("contract_fingerprint", "contracts:v1:off:target-only"),
        (
            "selected_live_switch_path",
            ({"switch_id": "s", "incoming_edges": ()},),
        ),
        ("runtime_input_fingerprint", "runtime:v1:changed"),
        ("execution_semantics_version", "preview:v2"),
    ],
)
def test_lineage_key_invalidates_each_mandatory_request_dimension(field, value) -> None:
    request = _request()

    assert lineage_cache_key(replace(request, **{field: value})) != lineage_cache_key(request)


def test_lineage_key_is_deterministic_for_graph_and_mapping_order() -> None:
    graph = _graph()
    reordered_nodes = list(reversed(graph.nodes))
    reordered_edges = list(reversed(graph.edges))
    mid = next(node for node in reordered_nodes if node.id == "mid")
    reordered_config = {key: mid.data.config[key] for key in reversed(list(mid.data.config))}
    reordered_mid = mid.model_copy(
        update={"data": mid.data.model_copy(update={"config": reordered_config})}
    )
    reordered_nodes = [reordered_mid if node.id == "mid" else node for node in reordered_nodes]
    reordered = graph.model_copy(update={"nodes": reordered_nodes, "edges": reordered_edges})

    assert lineage_cache_key(_request(reordered)) == lineage_cache_key(_request(graph))


def test_requested_column_duplicates_normalise_to_the_effective_order() -> None:
    request = _request(requested_columns=("y", "x"))

    assert lineage_cache_key(replace(request, requested_columns=("y", "x", "y"))) == (
        lineage_cache_key(request)
    )


def test_lineage_key_rejects_a_prepared_graph_that_does_not_match_the_original() -> None:
    graph = _graph()
    changed = _replace_node_config(graph, "mid", code="df = df")

    with pytest.raises(ValueError, match="prepared graph does not match"):
        lineage_cache_key(_request(graph, prepared=prepare_graph(changed, "target")))


def _graph_with_source_path(path: Path):
    graph = _graph()
    return _replace_node_config(graph, "source", path=str(path))


def test_preview_consumer_reuses_target_after_downstream_only_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import executor

    path = tmp_path / "input.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(path)
    graph = _graph_with_source_path(path)
    first = executor.execute_graph(
        graph,
        target_node_id="target",
        row_limit=10,
        target_preview_only=True,
    )
    fingerprint = executor._preview_cache.fingerprint
    assert first["target"].preview == [{"y": 2}]

    changed = _replace_node_config(graph, "downstream", code="df = df.drop('z')")
    monkeypatch.setattr(
        executor,
        "_eager_execute",
        lambda *_args, **_kwargs: pytest.fail("downstream edit caused target re-execution"),
    )

    second = executor.execute_graph(
        changed,
        target_node_id="target",
        row_limit=10,
        target_preview_only=True,
    )

    assert executor._preview_cache.fingerprint == fingerprint
    assert second["target"].preview == first["target"].preview


def test_preview_consumer_ignores_disconnected_runtime_input_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import executor

    input_path = tmp_path / "input.parquet"
    unrelated_path = tmp_path / "unrelated.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(input_path)
    pl.DataFrame({"u": [1]}).write_parquet(unrelated_path)
    graph = _graph_with_source_path(input_path)
    disconnected = graph.node_map["disconnected"]
    disconnected_data = disconnected.data.model_copy(
        update={
            "nodeType": "dataInput",
            "config": {
                "inputType": "file",
                "format": "parquet",
                "mode": "scan",
                "cacheMode": "direct",
                "path": str(unrelated_path),
                "arguments": {},
            },
        }
    )
    graph = graph.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"data": disconnected_data})
                if node.id == "disconnected"
                else node
                for node in graph.nodes
            ]
        }
    )
    executor.execute_graph(
        graph,
        target_node_id="target",
        row_limit=10,
        target_preview_only=True,
    )
    fingerprint = executor._preview_cache.fingerprint

    pl.DataFrame({"u": [999]}).write_parquet(unrelated_path)
    monkeypatch.setattr(
        executor,
        "_eager_execute",
        lambda *_args, **_kwargs: pytest.fail("unrelated runtime edit caused re-execution"),
    )
    executor.execute_graph(
        graph,
        target_node_id="target",
        row_limit=10,
        target_preview_only=True,
    )

    assert executor._preview_cache.fingerprint == fingerprint


def test_trace_reuses_a_full_preview_with_the_shared_lineage_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import executor, trace

    path = tmp_path / "input.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(path)
    graph = _graph_with_source_path(path)
    executor.execute_graph(
        graph,
        target_node_id="target",
        row_limit=10,
    )
    monkeypatch.setattr(
        trace,
        "_execute_eager_core",
        lambda *_args, **_kwargs: pytest.fail("trace did not reuse the full preview"),
    )

    result = trace.execute_trace(
        graph,
        target_node_id="target",
        row_limit=10,
        preview=executor._preview_cache,
    )

    assert result.output_value == {"y": 2}
    assert [step.node_id for step in result.steps] == ["source", "mid", "target"]


def test_trace_cache_survives_a_downstream_only_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from haute import trace

    path = tmp_path / "input.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(path)
    graph = _graph_with_source_path(path)
    first = trace.execute_trace(graph, target_node_id="target", row_limit=10)
    fingerprint = trace._cache.fingerprint
    changed = _replace_node_config(graph, "downstream", code="df = df.drop('z')")
    monkeypatch.setattr(
        trace,
        "_materialize_eager_outputs",
        lambda **_kwargs: pytest.fail("downstream edit invalidated the trace lineage"),
    )

    second = trace.execute_trace(changed, target_node_id="target", row_limit=10)

    assert trace._cache.fingerprint == fingerprint
    assert [step.output_values for step in second.steps] == [
        step.output_values for step in first.steps
    ]
