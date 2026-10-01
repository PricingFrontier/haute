"""Every node card dry-runs, applies and executes through the real application service.

A node card teaches the model a node type's configuration with real values.
Each card configuration is written to a fresh synthetic project with the
card's own fixture frames and files, dry-run and applied through
``PipelineApplicationService`` exactly as an assistant plan is, then executed
by the one execution engine; a card that drifts from its validator fails here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._execution_context import ExecutionProfile
from haute._input_providers import build_input_snapshot
from haute._polars_io_registry import data_input_is_direct
from haute._sandbox import set_project_root
from haute._source_cache import SourceCacheStore
from haute._types import NodeType, PipelineGraph
from haute.assistant._node_cards import CARD_CONFIG_NAMES, node_card, node_card_fixture
from haute.assistant._tools import dry_run_graph_edits
from haute.assistant._wire_ops import OpValidationError
from haute.executor import execute_graph
from haute.graph_utils import flatten_graph
from haute.routes._helpers import parse_pipeline_to_graph
from tests.conftest import build_test_api_input_snapshots

_SOURCE_FILE = "pipeline.py"
_PIPELINE_SOURCE = """\
import haute

pipeline = haute.Pipeline("cards", description="Node card fixture.")
"""
_CARD_NODE = "card"

_AUTHORABLE = [node_type for node_type in NodeType if node_card(node_type)["authorable"]]
_CASES = [
    pytest.param(node_type, name, id=f"{node_type.value}-{name}")
    for node_type in _AUTHORABLE
    for name in CARD_CONFIG_NAMES
]


def _short_project(tmp_path: Path, label: str) -> Path:
    # Source-cache generations add long content-addressed components; keep the
    # project path short for Windows' legacy path limit.
    suffix = hashlib.sha256(f"{tmp_path}:{label}".encode()).hexdigest()[:12]
    destination = tmp_path.parent / f"nc-{suffix}"
    destination.mkdir()
    (destination / _SOURCE_FILE).write_text(_PIPELINE_SOURCE, encoding="utf-8")
    return destination


def _service(project: Path) -> Any:
    from haute.assistant._application import PipelineApplicationService

    return PipelineApplicationService(
        project_root=project,
        pipeline_root=project,
        mutations_readiness=lambda _root: (True, None),
        publish_document_update=lambda _source: "f" * 64,
    )


def _write_fixture(project: Path, fixture: dict[str, Any]) -> None:
    """Write the fixture's frames as Parquet files and its other files."""

    for name, frame in fixture.get("frames", {}).items():
        data = pl.DataFrame(frame["rows"]).with_columns(
            pl.col(column).str.to_date() for column in frame.get("dates", [])
        )
        path = project / "data" / f"{name}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        data.write_parquet(path)
    for relative, value in fixture.get("files", {}).items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def _card_inputs(card: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """The input edges *config* assumes: its own, else the card's."""

    return config.get("inputs", card["inputs"])


def _frame_source(name: str) -> dict[str, Any]:
    return {
        "op": "add_node",
        "node_type": "dataInput",
        "name": name,
        "config": {
            "inputType": "file",
            "format": "parquet",
            "mode": "scan",
            "path": f"data/{name}.parquet",
        },
    }


def _card_operations(
    card: dict[str, Any], config: dict[str, Any], fixture: dict[str, Any]
) -> list[dict[str, Any]]:
    """The plan adding *config* as the card node, wired as the card declares.

    A fixture frame becomes a Data Input only when the plan reads from it, so
    a frame one configuration does not use is not left disconnected.
    """

    edges = []
    for item in _card_inputs(card, config):
        edge = {"op": "add_edge", "source": item["edge"], "target": _CARD_NODE}
        for handle in ("source_handle", "target_handle"):
            if handle in item:
                edge[handle] = item[handle]
        edges.append(edge)
    around = [*fixture.get("upstream", []), *fixture.get("downstream", [])]
    read = {op["source"] for op in [*edges, *around] if op["op"] == "add_edge"}
    return [
        *(_frame_source(name) for name in fixture.get("frames", {}) if name in read),
        *fixture.get("upstream", []),
        {
            "op": "add_node",
            "node_type": card["node_type"],
            "name": _CARD_NODE,
            "config": config["config"],
        },
        *edges,
        *fixture.get("downstream", []),
    ]


def _log_model_run(project: Path, fixture: dict[str, Any]) -> str:
    """Log a tiny CatBoost model over the fixture frame to the project's MLflow folder.

    Returns the run id a card's ``run_id`` placeholder stands for. The target is
    synthetic: scoring needs a model over the features, not a meaningful one.
    """

    import mlflow
    from catboost import CatBoostRegressor

    run = fixture["model_run"]
    rows = fixture["frames"][run["frame"]]["rows"]
    features = pl.DataFrame(rows).select(run["features"])
    model = CatBoostRegressor(
        iterations=4,
        depth=2,
        verbose=0,
        allow_writing_files=False,
        random_seed=0,
        cat_features=[name for name, dtype in features.schema.items() if dtype == pl.String],
    )
    model.fit(features.to_pandas(), [float(index) for index in range(features.height)])
    model_path = project / "trained" / run["artifact"]
    model_path.parent.mkdir()
    model.save_model(str(model_path))
    client = mlflow.MlflowClient(tracking_uri=(project / "mlruns").as_uri())
    run_id = client.create_run(client.create_experiment("node_cards")).info.run_id
    client.log_artifact(run_id, str(model_path))
    client.set_terminated(run_id)
    return run_id


def _prepare_snapshots(project: Path, graph: PipelineGraph) -> None:
    """Publish the snapshots every non-direct input executes from, as a preview would.

    Only execution needs them: the dry-run resolved every input without one.
    """

    store = SourceCacheStore(project)
    for node in graph.nodes:
        config = node.data.config
        if node.data.nodeType is NodeType.DATA_INPUT and not data_input_is_direct(config):
            build_input_snapshot(
                config, store=store, base_dir=project, profile=ExecutionProfile.PREVIEW_EAGER
            )
        elif node.data.nodeType is NodeType.API_INPUT:
            build_test_api_input_snapshots(project / config["path"], config)


_TERMINAL_JOB_STATUSES = frozenset(
    {
        "cancelled",
        "completed",
        "contract_error",
        "error",
        "memory_limited",
        "superseded",
        "timed_out",
    }
)
#: The job route that runs a node type's own execution beyond its preview:
#: training for Model Training, the solve for Optimisation.
_JOB_ROUTES = {
    NodeType.MODELLING: "/api/modelling/train",
    NodeType.OPTIMISER: "/api/optimiser/solve",
}


def _run_job(client: Any, route: str, graph: PipelineGraph) -> dict[str, Any]:
    response = client.post(
        route, json={"graph": graph.model_dump(mode="json"), "node_id": _CARD_NODE}
    )
    assert response.status_code == 200, response.text
    status_url = f"{route}/status/{response.json()['job_id']}"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status = client.get(status_url).json()
        if status["status"] in _TERMINAL_JOB_STATUSES:
            assert status["status"] == "completed", status.get("message")
            return status
        time.sleep(0.05)
    raise TimeoutError(f"{route} did not finish")


def _executed(graph: PipelineGraph, node_id: str) -> Any:
    result = execute_graph(graph, target_node_id=node_id, row_limit=100)[node_id]
    assert result.status == "ok", result.error
    return result


@pytest.mark.parametrize(("node_type", "config_name"), _CASES)
def test_every_card_configuration_applies_and_executes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    node_type: NodeType,
    config_name: str,
):
    card = node_card(node_type)
    fixture = node_card_fixture(node_type)
    assert fixture is not None
    config = next(item for item in card["configs"] if item["name"] == config_name)
    project = _short_project(tmp_path, f"{node_type.value}-{config_name}")
    monkeypatch.chdir(project)
    set_project_root(project)
    _write_fixture(project, fixture)
    if "model_run" in fixture:
        # Model artifacts stay inside this test's project.
        monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        monkeypatch.setattr(
            "haute._mlflow_io._disk_cache_root", lambda: project / ".cache" / "models"
        )
        config = {
            **config,
            "config": {**config["config"], "run_id": _log_model_run(project, fixture)},
        }
    operations = _card_operations(card, config, fixture)
    service = _service(project)

    plan = service.dry_run(_SOURCE_FILE, operations, summary="Test plan.").plan
    # A Quote Input the plan adds has no snapshot yet: its tables resolve from
    # their declared contract.
    adds_api_input = any(
        op["op"] == "add_node" and op["node_type"] == NodeType.API_INPUT.value for op in operations
    )
    declared = [
        item for item in plan.verification_evidence if item["kind"] == "input_schema_declared"
    ]
    assert bool(declared) == adds_api_input
    asyncio.run(service.apply(_SOURCE_FILE, plan.plan_hash))

    graph = flatten_graph(parse_pipeline_to_graph(project / _SOURCE_FILE))
    saved = graph.node_map[_CARD_NODE].data.config
    assert {key: saved.get(key) for key in config["config"]} == config["config"]
    _prepare_snapshots(project, graph)
    for item in _card_inputs(card, config):
        upstream = _executed(graph, item["edge"])
        columns = upstream.frame_columns.get(item.get("source_handle"), upstream.columns)
        assert {column.name: column.dtype for column in columns} == item["columns"]
    result = _executed(graph, _CARD_NODE)
    # A multi-frame API Input reports each table's columns under its label.
    produced = {
        column.name
        for columns in (result.columns, *result.frame_columns.values())
        for column in columns
    }
    assert set(config["produces"]) <= produced
    expected = fixture.get("expect", {}).get(config_name)
    if expected is not None:
        keys = sorted({key for row in expected for key in row})
        assert [{key: row[key] for key in keys} for row in result.preview] == expected
    route = _JOB_ROUTES.get(node_type)
    if route is not None:
        _run_job(request.getfixturevalue("client"), route, graph)


@pytest.mark.parametrize(
    "node_type", [node_type for node_type in NodeType if node_type not in _AUTHORABLE]
)
def test_a_card_that_is_not_authorable_states_the_products_refusal(
    tmp_path: Path, node_type: NodeType
):
    project = _short_project(tmp_path, node_type.value)
    set_project_root(project)

    with pytest.raises(OpValidationError) as refused:
        _service(project).dry_run(
            _SOURCE_FILE,
            [{"op": "add_node", "node_type": node_type.value, "name": "card", "config": {}}],
            summary="Test plan.",
        ).plan

    reason = str(refused.value).split(": ", 1)[1]
    assert reason in node_card(node_type)["note"]
    assert node_card_fixture(node_type) is None


# The value shapes the assistant walkthroughs failed on before a model could read
# a node's values: breakpoint boundaries guessed as interval or operator rows,
# and response paths without the "$[:]" root. Each is refused through the tool
# the model calls, the card's field meanings state the fix, and the card's own
# configuration on the same request passes its first dry-run.
_WALKTHROUGH_GUESSES = [
    pytest.param(
        NodeType.BANDING,
        {
            "factors": [
                {
                    "banding": "breakpoints",
                    "column": "driver_age",
                    "outputColumn": "age_band",
                    "rules": [
                        {"lower": 17, "upper": 24, "label": "17-24"},
                        {"lower": 25, "upper": 59, "label": "25-59"},
                        {"lower": 60, "label": "60+"},
                    ],
                }
            ]
        },
        "at most one open-ended boundary",
        "Ordered {boundary, label} rows",
        id="banding-interval-rows",
    ),
    pytest.param(
        NodeType.BANDING,
        {
            "factors": [
                {
                    "banding": "continuous",
                    "column": "driver_age",
                    "outputColumn": "age_band",
                    "rules": [
                        {"op1": "<=", "val1": 24, "assignment": "17-24"},
                        {"op1": ">", "val1": 24, "op2": "<=", "val2": 59, "assignment": "25-59"},
                        {"op1": ">", "val1": 59, "assignment": "60+"},
                    ],
                }
            ]
        },
        "unsupported banding type 'continuous'",
        '"breakpoints" for a number or date column',
        id="banding-operator-rows",
    ),
    pytest.param(
        NodeType.OUTPUT,
        {
            "outputMapping": [
                {
                    "source_port": "priced",
                    "source_column": "quote_id",
                    "output_path": "quote_id",
                    "enabled": True,
                },
                {
                    "source_port": "priced",
                    "source_column": "premium",
                    "output_path": "premium",
                    "enabled": True,
                },
            ],
            "outputFormat": "json",
        },
        "output path must start with '$[:]'",
        'Always starts with "$[:]"',
        id="output-bare-names",
    ),
    pytest.param(
        NodeType.OUTPUT,
        {
            "outputMapping": [
                {
                    "source_port": "priced",
                    "source_column": "quote_id",
                    "output_path": "$.quote_id",
                    "enabled": True,
                },
                {
                    "source_port": "priced",
                    "source_column": "premium",
                    "output_path": "$.premium",
                    "enabled": True,
                },
            ],
            "outputFormat": "json",
        },
        "output path must start with '$[:]'",
        'Always starts with "$[:]"',
        id="output-object-root",
    ),
]


@pytest.mark.parametrize(("node_type", "guess", "refusal", "card_teaches"), _WALKTHROUGH_GUESSES)
def test_walkthrough_guesses_fail_where_the_card_shape_is_valid_first_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    node_type: NodeType,
    guess: dict[str, Any],
    refusal: str,
    card_teaches: str,
):
    card = node_card(node_type)
    fixture = node_card_fixture(node_type)
    assert fixture is not None
    minimal = card["configs"][0]
    project = _short_project(tmp_path, f"guess-{node_type.value}")
    monkeypatch.chdir(project)
    set_project_root(project)
    _write_fixture(project, fixture)

    refused = asyncio.run(
        dry_run_graph_edits(
            _SOURCE_FILE,
            _card_operations(card, {**minimal, "config": guess}, fixture),
            summary="Test plan.",
        )
    )
    accepted = asyncio.run(
        dry_run_graph_edits(
            _SOURCE_FILE, _card_operations(card, minimal, fixture), summary="Test plan."
        )
    )

    error = refused["error"]
    assert isinstance(error, dict)
    assert refusal in error["message"]
    assert any(card_teaches in meaning for meaning in card["fields"].values())
    assert "error" not in accepted, accepted
    assert accepted["verification_tier"] == "schema"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param({"extra": 1}, "unknown keys ['extra']", id="unknown-key"),
        pytest.param(
            {"configs": "reversed"}, "must list exactly ['minimal', 'realistic']", id="order"
        ),
        pytest.param({"fields": {}}, "fields must map config paths", id="no-fields"),
        pytest.param({"authorable": False}, "unknown keys", id="not-authorable-with-configs"),
    ],
)
def test_a_malformed_card_fails_loudly(change: dict[str, Any], message: str):
    from haute.assistant import _node_cards

    card = {**node_card(NodeType.BANDING), "fixture": node_card_fixture(NodeType.BANDING)}
    if change.get("configs") == "reversed":
        change = {"configs": list(reversed(card["configs"]))}
    with pytest.raises(_node_cards.NodeCardError, match=re.escape(message)):
        _node_cards._validate_card(NodeType.BANDING, {**card, **change})
