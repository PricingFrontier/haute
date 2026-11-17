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
    for node_id, node_type, config in [
        ("model", NodeType.MODELLING, model),
        ("out", NodeType.DATA_OUTPUT, output),
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
    [{"contract": {"outputs": ["promised"]}}, {"keyword_parameters": "not a list"}],
    ids=["malformed-contract", "builder-refusal"],
)
def test_no_job_over_a_copy_names_a_key_or_a_flat_name(scale: dict[str, Any]) -> None:
    """A training run, a node-data build and a Data Output write plan before they walk:
    what planning raises names the copy's child too."""
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
    }
    status_paths = {"node data": "/api/node-data/status", "training": "/api/modelling/train/status"}
    for name, (path, body) in jobs.items():
        started = client.post(path, json={"graph": graph, **body})
        assert started.status_code == 200, started.text
        answers[name] = _job_status(client, f"{status_paths[name]}/{started.json()['job_id']}")
    answers["Data Output write"] = client.post(
        "/api/pipeline/write-output",
        json={"graph": graph, "target": {"occurrence": None, "node_id": "out"}, "overwrite": True},
    ).json()

    # Each fails before it runs anything, and names no node by its key or flat name.
    assert answers["training"]["status"] == "contract_error", answers["training"]
    leaks = {name: _leaked_keys(answer) for name, answer in answers.items()}
    assert leaks == {name: [] for name in answers}
