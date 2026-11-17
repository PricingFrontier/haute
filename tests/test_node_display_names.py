"""A message names a node by its label, and a submodel child by its label and its
occurrence's ("Banding 1 in pricing"), never by the key the flat graph runs it under
(specs/server-api/low-level.md, "Node addresses", item 7)."""

from __future__ import annotations

import pickle
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest

from haute._execution_context import (
    ExecutionAdmission,
    ExecutionContext,
    ExecutionProfile,
    execution_node_name,
)
from haute._flatten import flatten_graph
from haute._node_addresses import node_display_name, node_reference
from haute._project_context import current_project
from haute._ram_estimate import MaterialisationEstimate
from haute._submodel_instances import qualified_runtime_node_id
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.errors import (
    ConfigError,
    GroupByExecutionUnsupportedError,
    RequestRefusedError,
    SnapshotCorruptError,
)
from haute.execution import ProjectionRequest, plan_execution_strategy
from haute.routes._helpers import find_typed_node
from tests._preview_targets import node_target
from tests._project import project_app
from tests.test_node_address_contracts import NODE_KEY_POSITIONS, OPAQUE_KEY_STRINGS
from tests.test_node_addresses import _pipeline
from tests.test_submodel_instances import _reuse_graph


@pytest.fixture
def client() -> Any:
    from fastapi.testclient import TestClient

    return TestClient(project_app())


@pytest.fixture
def flat() -> PipelineGraph:
    return flatten_graph(_pipeline())


def test_a_node_is_named_by_its_label_and_a_child_with_its_occurrence(
    flat: PipelineGraph,
) -> None:
    copy_rate = qualified_runtime_node_id("pricing_2", "rate")

    assert node_display_name(flat, "upstream") == "Upstream"
    # A copy's child runs under a flat name of its own; it is named as authored.
    assert flat.node_map[copy_rate].data.label != "Rate"
    assert node_display_name(flat, copy_rate) == "Rate in pricing_2"
    assert node_reference(flat, copy_rate) == {
        "key": copy_rate,
        "occurrence": "pricing_2",
        "node_id": "rate",
        "label": "Rate",
    }
    # A key the graph does not hold is named as it is, so the message still reads.
    assert node_display_name(flat, "gone") == "gone"


def test_a_refusal_of_the_wrong_node_type_names_the_child_by_label(flat: PipelineGraph) -> None:
    with pytest.raises(RequestRefusedError) as refused:
        find_typed_node(
            flat, qualified_runtime_node_id("pricing_2", "rate"), NodeType.MODELLING, "modelling"
        )

    assert refused.value.status_code == 400
    assert refused.value.detail == "'Rate in pricing_2' is not a modelling node (got polars)"


def _broken_copy() -> PipelineGraph:
    """The reuse pipeline with its definition's first child broken."""
    graph = _reuse_graph()
    assert graph.submodels is not None
    scale = graph.submodels["definition_scoring"].graph.node_map["scale"]
    scale.data.config["code"] = "df = policy.with_columns(pl.col('missing'))"
    return graph


def test_an_upstream_failure_names_the_failed_child_by_label_and_occurrence(client: Any) -> None:
    preview = client.post(
        "/api/pipeline/preview",
        json={
            "graph": _broken_copy().model_dump(mode="json", by_alias=True),
            "target": node_target("finish", occurrence="rating_ie"),
        },
    ).json()

    assert preview["status"] == "error"
    assert preview["error"].startswith("Upstream node(s) failed: scale in rating_ie: ")
    assert "submodel_runtime" not in preview["error"]
    assert "rating_ie__scale" not in preview["error"]


def test_a_trace_of_a_failing_child_names_it_and_carries_its_reference(client: Any) -> None:
    graph = _broken_copy()
    target = node_target("scale", occurrence="rating_ie")
    traced = client.post(
        "/api/pipeline/trace",
        json={
            "graph": graph.model_dump(mode="json", by_alias=True),
            "target": target,
            "row_index": 0,
            "seed_plan": [],
        },
    )

    assert traced.status_code == 422, traced.text
    detail = traced.json()["detail"]
    key = qualified_runtime_node_id("rating_ie", "scale")
    assert detail["error_code"] == "trace_node_failed"
    assert detail["message"].startswith("'scale in rating_ie' failed: ")
    assert detail["node"] == {
        "key": key,
        "occurrence": "rating_ie",
        "node_id": "scale",
        "label": "scale",
    }


# ---------------------------------------------------------------- the copies' children

_COPY_SCALE = qualified_runtime_node_id("rating_ie", "scale")
_COPY_FINISH = qualified_runtime_node_id("rating_ie", "finish")


def _copy_with(**scale: Any) -> PipelineGraph:
    """The reuse pipeline with its definition's first child, ``scale``, configured so."""
    graph = _reuse_graph()
    assert graph.submodels is not None
    graph.submodels["definition_scoring"].graph.node_map["scale"].data.config.update(scale)
    return graph


def _flat_copy_with(**scale: Any) -> PipelineGraph:
    flat = flatten_graph(_copy_with(**scale))
    # A copy's child runs under a flat name of its own, which no message may show.
    assert flat.node_map[_COPY_SCALE].data.label == "rating_ie__scale"
    return flat


def _post(client: Any, path: str, graph: PipelineGraph, **body: Any) -> Any:
    return client.post(path, json={"graph": graph.model_dump(mode="json", by_alias=True), **body})


def _preview(client: Any, graph: PipelineGraph, child: str) -> dict[str, Any]:
    return _post(
        client, "/api/pipeline/preview", graph, target=node_target(child, occurrence="rating_ie")
    ).json()


def _trace(client: Any, graph: PipelineGraph, child: str) -> Any:
    return _post(
        client,
        "/api/pipeline/trace",
        graph,
        target=node_target(child, occurrence="rating_ie"),
        row_index=0,
        seed_plan=[],
    )


def test_an_execution_names_the_nodes_it_walked() -> None:
    context = ExecutionContext("naming", ExecutionProfile.PREVIEW_EAGER)
    context.provenance.record_node_addresses(_flat_copy_with())

    assert context.provenance.node_name(_COPY_SCALE) == "scale in rating_ie"
    assert context.provenance.node_name("source_2") == "source_2"
    assert context.provenance.node_name("gone", "Gone") == "Gone"
    assert execution_node_name(_COPY_SCALE, context=context) == "scale in rating_ie"
    with context.stage("naming"):
        assert execution_node_name(_COPY_SCALE) == "scale in rating_ie"
    # With no execution current, a node is named as the caller would name it.
    assert execution_node_name(_COPY_SCALE, "scale") == "scale"
    assert execution_node_name(_COPY_SCALE) == _COPY_SCALE


def test_an_error_raised_in_an_execution_names_its_node_not_its_key() -> None:
    context = ExecutionContext("naming", ExecutionProfile.PREVIEW_EAGER)
    context.provenance.record_node_addresses(_flat_copy_with())

    with context.stage("build", node_id=_COPY_SCALE):
        error = ConfigError("Its input names no table.", node_id=_COPY_SCALE, setting="x")

    # Rendered after the execution, and in another process, it still names the node.
    assert str(error) == "Its input names no table. (node=scale in rating_ie, setting=x)"
    assert str(pickle.loads(pickle.dumps(error))) == str(error)
    assert error.context["node_id"] == _COPY_SCALE
    assert str(ConfigError("Outside.", node_id=_COPY_SCALE)) == f"Outside. (node_id={_COPY_SCALE})"


def test_a_preview_labels_a_copys_children_by_label_and_occurrence(client: Any) -> None:
    preview = _preview(client, _copy_with(), "finish")

    assert preview["status"] == "ok", preview.get("error")
    expected = {_COPY_SCALE: "scale in rating_ie", _COPY_FINISH: "finish in rating_ie"}
    for field in ("timings", "memory"):
        labels = {entry["node_id"]: entry["label"] for entry in preview[field]}
        assert {key: labels[key] for key in expected} == expected, field


def test_a_seed_plan_entry_names_a_copys_child_by_label_and_occurrence() -> None:
    from haute.routes.pipeline import _preview_seed_plan_entries

    generation = SimpleNamespace(
        node_id=_COPY_SCALE,
        identity=SimpleNamespace(digest="d" * 64),
        generation_id="generation",
        columns=SimpleNamespace(names=None),
        created_at=0.0,
        kind="captured",
    )

    [entry] = _preview_seed_plan_entries(_flat_copy_with(), (generation,))  # type: ignore[arg-type]

    assert (entry.node_id, entry.node_label) == (_COPY_SCALE, "scale in rating_ie")


@pytest.mark.parametrize(
    ("path", "body", "refusal"),
    [
        ("/api/pipeline/write-output", {}, "'scale in rating_ie' is not a Data Output"),
        (
            "/api/output-assemble/dry-run",
            {"output_mapping": []},
            "'scale in rating_ie' is not an OUTPUT node",
        ),
    ],
    ids=["data-output", "output"],
)
def test_a_copys_child_refused_as_an_output_is_named_by_label(
    client: Any, path: str, body: dict[str, Any], refusal: str
) -> None:
    response = _post(
        client, path, _copy_with(), target={"occurrence": "rating_ie", "node_id": "scale"}, **body
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == refusal


def test_an_unreadable_snapshot_of_a_copys_child_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seed plan's resolution reads each point's latest generation from the store.

    The capture's publication, the other site naming an unreadable snapshot, is reached
    only by a shared-snapshot run that captures the child; it names the node the same way.
    """
    from haute._node_snapshots import NodeSnapshotStore
    from haute._seed_plans import SeedPlanRequest, _Resolver
    from haute._source_cache import SourceCacheCorruptError

    def unreadable(*_args: Any, **_kwargs: Any) -> Any:
        raise SourceCacheCorruptError("generation is unreadable")

    store = NodeSnapshotStore(tmp_path)
    monkeypatch.setattr(store, "latest_generation", unreadable)
    request = SeedPlanRequest(
        graph=_flat_copy_with(),
        target_node_id=_COPY_FINISH,
        source="live",
        profile=ExecutionProfile.TRAINING_PREP,
    )

    with pytest.raises(SnapshotCorruptError) as raised:
        _Resolver(request, store).latest_for(_COPY_SCALE)

    assert raised.value.node_id == _COPY_SCALE
    assert raised.value.node_label == "scale in rating_ie"
    assert raised.value.to_payload()["message"].startswith(
        "The cached data for 'scale in rating_ie' is unreadable."
    )


@pytest.mark.parametrize("adopted", [False, True], ids=["leased", "adopted-by-a-worker"])
def test_a_seed_found_unreadable_at_its_lease_names_the_copys_child(
    monkeypatch: pytest.MonkeyPatch, adopted: bool
) -> None:
    """Resolution read the seed's generation; leasing it, in the parent or a worker
    adopting the plan, finds it unreadable."""
    from haute._data_points import DataPointResolver
    from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotOwner, NodeSnapshotStore
    from haute._seed_plans import (
        SeedPlan,
        SeedPlanRequest,
        open_resolved_seed_plan,
        resolve_seed_plan,
    )
    from haute._source_cache import SourceCacheCorruptError

    flat = _flat_copy_with()
    store = NodeSnapshotStore(current_project().root)
    resolver = DataPointResolver(flat, source="live", store=store)
    identity = resolver.node_output_slot(_COPY_SCALE).identity(
        resolver.node_output_signature(_COPY_SCALE)
    )
    artifact = store.stage_node_output(identity)
    pl.DataFrame({"x": [10]}).write_parquet(artifact.part_path(0))
    with store.publish_node_output(
        identity,
        artifact,
        columns=NodeSnapshotColumns.all(),
        dependencies={},
        explicit=False,
        profile=ExecutionProfile.TRAINING_PREP,
        refresh=False,
        owner=NodeSnapshotOwner.of(flat, _COPY_SCALE),
    ) as publication:
        assert publication.outcome == "published"
    request = SeedPlanRequest(
        graph=flat,
        target_node_id=_COPY_FINISH,
        source="live",
        profile=ExecutionProfile.TRAINING_PREP,
    )
    parent = open_resolved_seed_plan(request, store=store) if adopted else None
    if parent is None:
        assert set(resolve_seed_plan(request, store=store).seeds) == {_COPY_SCALE}

    def unreadable(*_args: Any, **_kwargs: Any) -> Any:
        raise SourceCacheCorruptError("generation is unreadable")

    monkeypatch.setattr(NodeSnapshotStore, "lease_generation", unreadable)
    try:
        with pytest.raises(SnapshotCorruptError) as raised:
            if parent is not None:
                SeedPlan.adopt(pickle.loads(pickle.dumps(parent.handoff())))
            else:
                open_resolved_seed_plan(request, store=store)
    finally:
        if parent is not None:
            parent.close()

    assert raised.value.node_id == _COPY_SCALE
    assert raised.value.to_payload()["message"].startswith(
        "The cached data for 'scale in rating_ie' is unreadable."
    )
    # A job's failure message is the error's whole text, which names no key either.
    assert str(raised.value) == (
        "The cached data for 'scale in rating_ie' is unreadable. Refresh that node to rebuild "
        "it, or clear it to run without a cache. (node=scale in rating_ie, "
        "node_label=scale in rating_ie)"
    )
    assert _leaked_keys(str(pickle.loads(pickle.dumps(raised.value)))) == []


def test_a_copys_child_that_returns_no_frame_is_named_by_label(client: Any) -> None:
    preview = _preview(client, _copy_with(code="df = 1"), "scale")

    assert preview["status"] == "error"
    assert preview["error"] == "'scale in rating_ie' returned int; expected a Polars frame."


def test_a_copys_child_missing_a_promised_column_is_named_by_label(client: Any) -> None:
    preview = _preview(
        client, _copy_with(contract={"inputs": ["x"], "outputs": ["promised"]}), "finish"
    )

    assert preview["status"] == "error"
    assert preview["error"] == (
        "'scale in rating_ie' did not create the column 'promised', which its contract says "
        "it outputs."
    )


def test_a_trace_diagnostic_names_a_copys_children_by_label(client: Any) -> None:
    graph = _reuse_graph()
    # A child that drops a row, so its parent's row is matched by value: one of two
    # identical rows.
    graph.node_map["source_2"].data.config["code"] = "df = pl.DataFrame({'x': [2, 2, -1]}).lazy()"
    assert graph.submodels is not None
    finish = graph.submodels["definition_scoring"].graph.node_map["finish"]
    finish.data.config["code"] = "df = scale.filter(pl.col('x') > 0).with_columns(y=pl.lit(1))"

    traced = _trace(client, graph, "finish")

    assert traced.status_code == 200, traced.text
    messages = [item["message"] for item in traced.json()["trace"]["correlation_diagnostics"]]
    assert any(
        "node 'scale in rating_ie' for child node 'finish in rating_ie'" in message
        for message in messages
    ), messages
    assert not [message for message in messages if "rating_ie__" in message]


def test_a_trace_of_a_copys_child_names_its_steps_by_label(client: Any) -> None:
    traced = _trace(client, _reuse_graph(), "finish")

    assert traced.status_code == 200, traced.text
    trace = traced.json()["trace"]
    names = {step["node_id"]: step["node_name"] for step in trace["steps"]}
    assert names[_COPY_SCALE] == "scale in rating_ie"
    assert names[_COPY_FINISH] == "finish in rating_ie"
    # Nothing else the trace shows (its waterfall, its input sources) names a key either.
    assert _leaked_keys(traced.json()) == []


def test_a_trace_of_a_copys_child_its_builder_refuses_names_it(client: Any) -> None:
    """A trace's walk is no stage of its execution: it names its nodes from its graph."""
    traced = _trace(client, _copy_with(keyword_parameters="not a list"), "scale")

    assert traced.status_code == 422, traced.text
    assert traced.json()["detail"]["message"] == (
        "Node 'scale in rating_ie' (polars) setting 'keyword_parameters': "
        "Input should be a valid list."
    )


def test_a_walk_inside_no_execution_names_its_nodes_from_its_graph() -> None:
    from haute._graph_walker import CollectPolicy, walk_graph
    from haute.errors import NodeConfigError
    from haute.executor import _build_node_fn

    with pytest.raises(NodeConfigError) as refused:
        walk_graph(
            _flat_copy_with(keyword_parameters="not a list"),
            _build_node_fn,
            policy=CollectPolicy.display(record_failures=True),
            target_node_id=_COPY_SCALE,
        )

    assert refused.value.message.startswith(
        "Node 'scale in rating_ie' (polars) setting 'keyword_parameters'"
    )


def test_a_copys_child_with_a_stale_selection_is_named_by_label(client: Any) -> None:
    refused = _preview(client, _copy_with(selected_columns=["gone"]), "scale")

    assert refused["detail"]["error_code"] == "node_config_invalid"
    assert refused["detail"]["message"].startswith(
        "Node 'scale in rating_ie' keeps columns its input no longer has any of: 'gone'."
    )


def test_a_copys_child_holding_a_wide_integer_is_named_by_label(client: Any) -> None:
    graph = _copy_with(code="df = policy.with_columns(pl.col('x').cast(pl.Int128))")

    preview = _preview(client, graph, "scale")

    assert preview["status"] == "error"
    assert preview["error"].endswith("(column=x, node=scale in rating_ie)")


def test_an_input_a_copys_child_lacks_is_refused_naming_the_child() -> None:
    from haute._graph_utils import incoming_edge_for_name

    with pytest.raises(ValueError, match="^Input 'nope' is not connected to node 'finish in "):
        incoming_edge_for_name(_flat_copy_with(), _COPY_FINISH, "nope")


def test_a_data_point_on_a_port_a_copys_child_lacks_names_the_child() -> None:
    from haute._data_points import DataPoint, NodeDataPointInvalidError, point_kind

    with pytest.raises(NodeDataPointInvalidError) as refused:
        point_kind(_flat_copy_with(), DataPoint(_COPY_SCALE, port_label="other"))

    assert str(refused.value) == "Node 'scale in rating_ie' has no output port 'other'."


def _strategy_with(
    code: str, profile: ExecutionProfile, **request: Any
) -> tuple[PipelineGraph, ProjectionRequest]:
    flat = _flat_copy_with(code=code)
    return flat, ProjectionRequest(
        graph=flat, target_node_id=_COPY_FINISH, profile=profile, **request
    )


@pytest.mark.parametrize("hard_cap", [None, "test-cap"], ids=["refused", "conservative"])
def test_a_materialisation_remediation_names_a_copys_child_by_label(
    monkeypatch: pytest.MonkeyPatch, hard_cap: str | None
) -> None:
    import haute.execution as execution

    monkeypatch.setattr(execution, "current_native_memory_backend", lambda: hard_cap)
    _flat, request = _strategy_with(
        "df = policy.group_by('x').agg(pl.len())", ExecutionProfile.LAZY_SINK
    )
    headroom = 1 << 30
    context = ExecutionContext(
        "naming",
        ExecutionProfile.LAZY_SINK,
        admission=ExecutionAdmission(
            operation="naming",
            profile=ExecutionProfile.LAZY_SINK,
            memory_limit_bytes=headroom,
            rss_at_admission_bytes=None,
            rss_limit_bytes=None,
            headroom_bytes=headroom,
            config_key="test",
        ),
    )
    # The estimator's detail names the node it could not measure by key.
    estimate = MaterialisationEstimate.unavailable(f"{_COPY_SCALE}:source_row_count_unavailable")

    if hard_cap is None:
        with pytest.raises(GroupByExecutionUnsupportedError) as refused:
            plan_execution_strategy(
                request, execution_context=context, materialisation_estimate=estimate
            )
        remediation = refused.value.remediation
        assert refused.value.node_id == _COPY_SCALE
        assert "Estimator reported: scale in rating_ie:source_row_count_unavailable." in (
            remediation
        )
    else:
        planned = plan_execution_strategy(
            request, execution_context=context, materialisation_estimate=estimate
        )
        remediation = planned.diagnostic.remediation or ""
        assert "unavailable (scale in rating_ie:source_row_count_unavailable)" in remediation
        assert "rewrite 'group_by' at 'scale in rating_ie'" in remediation
        # The evidence keeps the estimator's own detail.
        assert f"proof_gap={_COPY_SCALE}:source_row_count_unavailable" in (
            planned.diagnostic.assumptions
        )
    assert "submodel_runtime" not in remediation


def test_a_projection_remediation_names_a_copys_child_by_label() -> None:
    _flat, request = _strategy_with(
        "df = policy.with_columns(pl.max_horizontal(pl.col(['x'])).alias('burn'))",
        ExecutionProfile.LAZY_SINK,
        required_columns_by_node={_COPY_FINISH: {"x"}},
    )

    diagnostic = plan_execution_strategy(request).diagnostic

    assert diagnostic.projection_cause is not None
    assert diagnostic.projection_cause.node_id == _COPY_SCALE
    assert diagnostic.remediation is not None
    assert diagnostic.remediation.startswith(
        "Haute can't follow which columns the code in 'scale in rating_ie' reads"
    )


# ------------------------------------------------------------------------- the sweep

# Where an answer names a node by key: every field the address contracts list, its
# echoed opaque strings, its own address table and an error's structured node.
_KEY_FIELDS = frozenset(
    {field for _, field in NODE_KEY_POSITIONS if not field.endswith("{}")}
    | {field for _, field in OPAQUE_KEY_STRINGS}
    | {"node_addresses", "key"}
)


def _leaked_keys(value: Any, path: str = "") -> list[str]:
    """Every string of *value* outside a key position that names a runtime key or a
    copy's flat name. A mapping keyed by node is read by its values, never its keys."""
    if isinstance(value, dict):
        return [
            leak
            for field, item in value.items()
            if field not in _KEY_FIELDS
            for leak in _leaked_keys(item, f"{path}.{field}")
        ]
    if isinstance(value, list):
        return [leak for i, item in enumerate(value) for leak in _leaked_keys(item, f"{path}[{i}]")]
    if isinstance(value, str) and ("submodel_runtime" in value or "rating_ie__" in value):
        return [f"{path}: {value}"]
    return []


@pytest.mark.parametrize(
    "scale",
    [
        {"code": "df = policy.with_columns(pl.col('missing'))"},
        {"code": "df = 1"},
        {"contract": {"inputs": ["x"], "outputs": ["promised"]}},
        {"contract": {"outputs": ["promised"]}},
        {"keyword_parameters": "not a list"},
        {"selected_columns": ["gone"]},
        {"code": "df = policy.with_columns(pl.col('x').cast(pl.Int128))"},
    ],
    ids=[
        "polars-error",
        "no-frame",
        "contract",
        "malformed-contract",
        "builder-refusal",
        "stale-selection",
        "wide-integer",
    ],
)
def test_no_failing_answer_over_a_copy_names_a_key_or_a_flat_name(
    client: Any, scale: dict[str, Any]
) -> None:
    graph = _copy_with(**scale)
    answers = {
        f"preview of {child}": _preview(client, graph, child) for child in ("finish", "scale")
    }
    answers["trace of scale"] = _trace(client, graph, "scale").json()

    # Each fails: a preview with its error, or a refusal (a public contract error).
    for name, answer in answers.items():
        assert answer.get("status") == "error" or "detail" in answer, (name, answer)
    leaks = {name: _leaked_keys(answer) for name, answer in answers.items()}
    assert leaks == {name: [] for name in answers}


def _jobs_graph(**scale: Any) -> dict[str, Any]:
    """The reuse pipeline feeding a model and a Data Output, ``scale`` configured so."""
    graph = _copy_with(**scale)
    graph.node_map["consumer"].data.config["code"] = (
        "df = pl.concat([premium, rating_ie, rating_fr]).with_columns(f=pl.lit('a'))"
    )
    model = {
        "algorithm": "catboost",
        "task": "regression",
        "target": "x",
        "feature_columns": ["f"],
        "loss_function": "RMSE",
        "params": {"iterations": 1, "depth": 1},
        "evaluation": {
            "schema_version": 1,
            "strategy": "random",
            "seed": 42,
            "validation": {"method": "single", "size": 0.2},
        },
    }
    output = {
        "output_type": "file",
        "format": "parquet",
        "path": str(current_project().root / "out" / "result.parquet"),
    }
    optimiser = {
        "mode": "online",
        "objective": "x",
        "constraints": {"volume": {"min": 0.90}},
        "quote_id": "quote_id",
        "scenario_index": "scenario_index",
        "scenario_value": "scenario_value",
        "max_iter": 20,
        "tolerance": 1e-4,
    }
    for node_id, node_type, config in [
        ("model", NodeType.MODELLING, model),
        ("out", NodeType.DATA_OUTPUT, output),
        ("opt", NodeType.OPTIMISER, optimiser),
    ]:
        graph.nodes.append(
            GraphNode(id=node_id, data=NodeData(label=node_id, nodeType=node_type, config=config))
        )
        graph.edges.append(GraphEdge(id=f"to_{node_id}", source="consumer", target=node_id))
    return graph.model_dump(mode="json", by_alias=True)


def _job_status(client: Any, path: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = client.get(path).json()
        if status.get("status") not in {"pending", "running", "queued", "preparing"}:
            break
        time.sleep(0.05)
    return status


@pytest.mark.parametrize(
    "scale",
    [
        {"contract": {"outputs": ["promised"]}},
        {"keyword_parameters": "not a list"},
        {"code": "df = policy.with_columns(pl.col('missing'))"},
    ],
    ids=["malformed-contract", "builder-refusal", "polars-error"],
)
def test_no_job_over_a_copy_names_a_key_or_a_flat_name(scale: dict[str, Any]) -> None:
    """A training run, a node-data build, a solve, an auto range and a Data Output write
    over a copy whose child fails, in their planning or their walk: what they report names
    the copy's child by label, never by its key or flat name."""
    from fastapi.testclient import TestClient

    client = TestClient(project_app(), raise_server_exceptions=False)
    graph = _jobs_graph(**scale)
    answers: dict[str, Any] = {}
    jobs = {
        "node data": (
            "/api/node-data/run",
            {"target": {"occurrence": "rating_ie", "node_id": "finish"}, "refresh": True},
        ),
        "training": ("/api/modelling/train", {"target": {"occurrence": None, "node_id": "model"}}),
        "solve": ("/api/optimiser/solve", {"target": {"occurrence": None, "node_id": "opt"}}),
        "auto range": (
            "/api/optimiser/frontier/auto-range/start",
            {"target": {"occurrence": None, "node_id": "opt"}},
        ),
    }
    status_paths = {
        "node data": "/api/node-data/status",
        "training": "/api/modelling/train/status",
        "solve": "/api/optimiser/solve/status",
        "auto range": "/api/optimiser/frontier/auto-range/status",
    }
    for name, (path, body) in jobs.items():
        started = client.post(path, json={"graph": graph, **body})
        assert started.status_code == 200, started.text
        answers[name] = _job_status(client, f"{status_paths[name]}/{started.json()['job_id']}")
    answers["Data Output write"] = client.post(
        "/api/pipeline/write-output",
        json={"graph": graph, "target": {"occurrence": None, "node_id": "out"}, "overwrite": True},
    ).json()

    # Each fails as the user's to fix, and names no node by its key or flat name.
    for name in ("training", "node data", "solve", "auto range"):
        assert answers[name]["status"] == "contract_error", (name, answers[name])
    # No answer carries the plan Polars appends to a failure, whose scans print cache paths.
    assert [name for name, answer in answers.items() if "Resolved plan" in str(answer)] == []
    assert "detail" in answers["Data Output write"], answers["Data Output write"]
    leaks = {name: _leaked_keys(answer) for name, answer in answers.items()}
    assert leaks == {name: [] for name in answers}


# ------------------------------------------------- what a walk, a trace and a job report


def _unfed_copy(node_type: NodeType) -> PipelineGraph:
    """The reuse pipeline whose definition's ``scale``, made a *node_type* node, nothing
    feeds: its input port feeds ``finish`` instead."""
    from haute._types import SubmodelEndpoint

    graph = _copy_with()
    assert graph.submodels is not None
    definition = graph.submodels["definition_scoring"]
    definition.input_ports[0].targets = [SubmodelEndpoint(node_id="finish")]
    definition.graph.node_map["finish"].data.config["code"] = "df = policy"
    scale = definition.graph.node_map["scale"]
    scale.data.nodeType = node_type
    scale.data.config = {}
    return graph


def _joined_copy() -> PipelineGraph:
    """The reuse pipeline whose definition's ``finish`` also reads its input port, so the
    copy's ``finish`` joins two frames, which a shared-snapshot preview captures."""
    from haute._types import SubmodelEndpoint

    graph = _copy_with()
    # Only a graph read from a pipeline file previews under a seed plan.
    graph.source_file = "main.py"
    assert graph.submodels is not None
    definition = graph.submodels["definition_scoring"]
    definition.input_ports[0].targets.append(SubmodelEndpoint(node_id="finish"))
    definition.graph.node_map["finish"].data.config["code"] = "df = pl.concat([scale, policy])"
    return graph


def _unmatched_copy() -> PipelineGraph:
    """The reuse pipeline whose copy's ``finish`` drops a row and rewrites every column its
    parent holds, so no row of ``scale`` can be told for the traced one."""
    graph = _reuse_graph()
    graph.node_map["source_2"].data.config["code"] = "df = pl.DataFrame({'x': [2, -1, 3]}).lazy()"
    assert graph.submodels is not None
    finish = graph.submodels["definition_scoring"].graph.node_map["finish"]
    finish.data.config["code"] = (
        "df = scale.filter(pl.col('x') > 0).with_columns(y=pl.col('x') + 1, x=pl.lit(0))"
    )
    return graph


def _flat_copy_writing(output: dict[str, Any], **scale: Any) -> PipelineGraph:
    """The reuse pipeline, flattened, with a Data Output ``out`` after its definition's
    ``finish``, configured with *output* over a parquet file.

    Every occurrence would write the one destination, which the routes refuse before they
    write, so a test writes it through the executor."""
    graph = _copy_with(**scale)
    assert graph.submodels is not None
    definition = graph.submodels["definition_scoring"].graph
    config = {
        "output_type": "file",
        "format": "parquet",
        "path": str(current_project().root / "out" / "result.parquet"),
        **output,
    }
    definition.nodes.append(
        GraphNode(
            id="out", data=NodeData(label="out", nodeType=NodeType.DATA_OUTPUT, config=config)
        )
    )
    definition.edges.append(GraphEdge(id="to_out", source="finish", target="out"))
    return flatten_graph(graph)


def test_a_walks_progress_names_a_copys_children_by_label() -> None:
    from haute._execution_admission import create_admitted_execution_context
    from haute._step_progress import StepProgress
    from haute.executor import execute_graph

    steps: list[StepProgress] = []
    context = create_admitted_execution_context(
        operation="naming", profile=ExecutionProfile.PREVIEW_EAGER
    )
    context.step_progress = steps.append
    try:
        results = execute_graph(
            _flat_copy_with(), target_node_id=_COPY_FINISH, execution_context=context
        )
    finally:
        context.release_admission()

    assert results[_COPY_FINISH].status == "ok", results[_COPY_FINISH].error
    labels = [step.label for step in steps if step.label]
    assert "Computing scale in rating_ie" in labels
    assert "Computing finish in rating_ie" in labels
    assert _leaked_keys(labels) == []


def test_a_copys_child_with_no_input_data_is_named_by_label(client: Any) -> None:
    preview = _preview(client, _unfed_copy(NodeType.BANDING), "scale")

    assert preview["status"] == "error"
    assert preview["error"] == "No input data available for node 'scale in rating_ie'"


def test_a_shape_refusal_of_a_copys_child_names_no_flat_name(client: Any) -> None:
    """An Explore child nothing feeds is refused by the graph's shape check, outside the
    messages item 7 lists."""
    preview = _preview(client, _unfed_copy(NodeType.EXPLORE), "scale")

    assert preview["status"] == "error"
    assert preview["error"].startswith(
        "Explore nodes must have exactly one incoming edge. (graph=execution, "
        "node=scale in rating_ie, "
    )
    assert _leaked_keys(preview) == []


def test_a_trace_of_a_copys_child_emitting_several_frames_names_it(client: Any) -> None:
    traced = _trace(client, _copy_with(code="df = {'a': policy, 'b': policy}"), "scale")

    assert traced.status_code == 400, traced.text
    assert traced.json()["detail"] == (
        "Target node 'scale in rating_ie' emits multiple frames; trace a node downstream of "
        "a specific frame instead."
    )


def test_a_traces_omission_and_input_source_name_a_copys_child(client: Any) -> None:
    traced = _post(
        client,
        "/api/pipeline/trace",
        _unmatched_copy(),
        target=node_target("finish", occurrence="rating_ie"),
        row_index=0,
        seed_plan=[],
        column="y",
    )

    assert traced.status_code == 200, traced.text
    trace = traced.json()["trace"]
    omissions = {omission["node_id"]: omission["node_name"] for omission in trace["omissions"]}
    assert omissions == {_COPY_SCALE: "scale in rating_ie"}
    [finish] = [step for step in trace["steps"] if step["node_id"] == _COPY_FINISH]
    # The formula for y reads the x of scale, whose row the trace could not tell.
    assert finish["calculation"]["input_sources"]["x"] == {
        "node_id": _COPY_SCALE,
        "node_name": "scale in rating_ie",
    }


def test_a_trace_diagnostic_of_an_untold_row_names_a_copys_children(client: Any) -> None:
    traced = _trace(client, _unmatched_copy(), "finish")

    assert traced.status_code == 200, traced.text
    [message] = [
        diagnostic["message"]
        for diagnostic in traced.json()["trace"]["correlation_diagnostics"]
        if diagnostic["code"] == "row_scope_unproven"
    ]
    assert message == (
        "Row correlation for node 'scale in rating_ie' could not identify the row that fed "
        "child 'finish in rating_ie'."
    )


@pytest.mark.in_process
def test_a_seed_continuation_diagnostic_names_a_copys_child(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trace seeded from the copy's ``finish`` proves its snapshot row by a recompute; one
    that holds no equal row leaves the ancestors untraced, naming whose snapshot."""
    import haute.trace as trace_module

    graph = _joined_copy()
    preview = _preview(client, graph, "finish")
    assert [(entry["node_id"], entry["kind"]) for entry in preview["seed_plan"]] == [
        (_COPY_FINISH, "captured")
    ]
    seeds = [
        {
            field: entry[field]
            for field in ("node_id", "port_label", "identity_digest", "generation_id")
        }
        for entry in preview["seed_plan"]
    ]
    monkeypatch.setattr(trace_module, "_lookup_clicked_row", lambda *_args: pl.DataFrame())

    traced = _post(
        client,
        "/api/pipeline/trace",
        graph,
        target=node_target("finish", occurrence="rating_ie"),
        row_index=0,
        seed_plan=seeds,
    )

    assert traced.status_code == 200, traced.text
    trace = traced.json()["trace"]
    [finish] = trace["steps"]
    assert finish["snapshot_generation_id"] == seeds[0]["generation_id"]
    messages = {item["node_id"]: item["message"] for item in trace["correlation_diagnostics"]}
    assert messages[_COPY_SCALE] == (
        "Not traced above the snapshot of finish in rating_ie: a recompute holds no row equal "
        "to the snapshot's."
    )
    assert _leaked_keys(traced.json()) == []


def test_a_seed_continuations_conflicting_row_names_the_copys_children() -> None:
    """Format only: the trace hands the merge its namer, which the test above checks."""
    import functools

    from haute.trace import _merge_seed_continuations, _SeedContinuation

    continuation = _SeedContinuation(
        seed_id=_COPY_FINISH,
        ancestors=(_COPY_SCALE,),
        parents_of={},
        rows={_COPY_SCALE: {"x": 30}},
    )
    diagnostics: list[dict[str, Any]] = []

    _merge_seed_continuations(
        [continuation],
        node_name=functools.partial(node_display_name, _flat_copy_with()),
        cached_rows={_COPY_SCALE: {"x": 20}},
        row_positions={},
        frames={},
        row_frames={},
        diagnostics=diagnostics,
        unresolved={},
    )

    assert [diagnostic["message"] for diagnostic in diagnostics] == [
        "Tracing above the snapshot of finish in rating_ie reached another row of node "
        "'scale in rating_ie' than the trace did through its other children; neither is shown."
    ]


def test_a_schema_warning_names_a_copys_child_by_label(client: Any) -> None:
    """``rating_fr`` reads ``rating_ie``'s output, whose ``finish`` fails, so its shared input
    cannot be compared with the owner's."""
    graph = _copy_with(code="df = policy.with_columns(pl.col('missing'))")
    edge = next(edge for edge in graph.edges if edge.id == "input_3")
    edge.source, edge.sourceHandle = "rating_ie", "out__premium"

    preview = _post(
        client, "/api/pipeline/preview", graph, target=node_target("finish", occurrence="rating_fr")
    ).json()

    [warning] = preview["node_schema_warnings"]["rating_fr"]
    assert (warning["input"], warning["status"]) == ("policy", "unavailable")
    assert warning["detail"].startswith(
        "Cannot resolve schema of 'finish in rating_ie': Upstream node(s) failed: "
        "scale in rating_ie: "
    )
    assert _leaked_keys(preview) == []


def test_a_data_output_refusing_a_copys_child_names_it() -> None:
    from haute.executor import data_output_seed_plan_request

    with pytest.raises(ValueError, match=r"^'scale in rating_ie' is not a Data Output$"):
        data_output_seed_plan_request(_flat_copy_with(), _COPY_SCALE)


@pytest.mark.parametrize(
    ("scale", "output", "message"),
    [
        (
            {"code": "df = policy.with_columns((pl.col('x') - 100).cast(pl.UInt8, strict=True))"},
            {},
            "Writing Data Output 'out in rating_ie' failed while computing its rows: ",
        ),
        (
            {},
            {"bogus": 1},
            "Node 'out in rating_ie' has dataOutput config keys the node type does not declare: ",
        ),
    ],
    ids=["rows", "config"],
)
def test_a_data_output_write_names_a_copys_child_by_label(
    scale: dict[str, Any], output: dict[str, Any], message: str
) -> None:
    from haute.errors import HauteError
    from haute.executor import write_data_output

    flat = _flat_copy_writing(output, **scale)

    with pytest.raises(HauteError) as raised:
        write_data_output(flat, qualified_runtime_node_id("rating_ie", "out"), overwrite=True)

    assert str(raised.value).startswith(message)
    assert _leaked_keys(str(raised.value)) == []


@pytest.mark.in_process
def test_a_capture_found_unreadable_at_its_publication_names_the_copys_child(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview captures the copy's ``finish``; publishing it meets a corrupt
    generation, reported against the node rather than the store's identity."""
    from haute._node_snapshots import NodeSnapshotStore
    from haute._source_cache import SourceCacheCorruptError

    def unreadable(*_args: Any, **_kwargs: Any) -> Any:
        raise SourceCacheCorruptError("generation is unreadable")

    monkeypatch.setattr(NodeSnapshotStore, "publish_node_output", unreadable)

    refused = _preview(client, _joined_copy(), "finish")

    assert refused["detail"] == {
        "error_code": "snapshot_corrupt",
        "message": (
            "The cached data for 'finish in rating_ie' is unreadable. Refresh that node to "
            "rebuild it, or clear it to run without a cache."
        ),
        "node_id": _COPY_FINISH,
        "node_label": "finish in rating_ie",
    }


def test_a_data_point_of_a_copys_child_with_two_inputs_names_it(client: Any) -> None:
    from haute._types import SubmodelEndpoint

    graph = _copy_with()
    assert graph.submodels is not None
    definition = graph.submodels["definition_scoring"]
    definition.input_ports[0].targets.append(SubmodelEndpoint(node_id="finish"))
    finish = definition.graph.node_map["finish"]
    finish.data.nodeType = NodeType.EXPLORE
    finish.data.config = {}

    response = _post(
        client,
        "/api/node-data/point",
        graph,
        target={"occurrence": "rating_ie", "node_id": "finish"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == (
        "Node 'finish in rating_ie' must have exactly one incoming connection to read its "
        "data (found 2)."
    )


def test_a_training_joins_downsampling_warning_names_the_copys_children() -> None:
    from haute._ram_estimate import RamEstimate
    from haute.routes._training_lifecycle import _downsampling_warning

    estimate = RamEstimate(
        safe_row_limit=1_000,
        total_rows=5_000,
        estimated_bytes=1 << 30,
        available_bytes=2 << 30,
        bytes_per_row=100.0,
        was_downsampled=False,
        warning=None,
        unbounded_join_node_ids=(_COPY_SCALE, _COPY_FINISH),
    )

    assert _downsampling_warning(_flat_copy_with(), estimate, 1_000) == (
        "Dataset downsampled to 1,000 rows to fit in available RAM (2.0 GB). Its row count is "
        "not proven: 'scale in rating_ie' and 'finish in rating_ie' have no key contract, so "
        "up to 5,000 rows could arrive."
    )
