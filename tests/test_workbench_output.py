"""The Workbench Output: the response's tables, filled from the frames connected to them.

Its tables are a copy of the response's tables the project's workbench supplies
(specs/workbench). Each is a port: the frame connected to it fills it, each column from
the frame's column of exactly its name, given its declared type, or with nulls when the frame
lacks it, holds it with a type that does not fit or nothing is connected. The node's result
is its tables; where the pipeline answers a request they become the response for one quote,
each table under its name.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi import HTTPException

from haute._builders import _build_node_fn
from haute._execution_admission import create_admitted_execution_context
from haute._node_apply import assemble_workbench_output_from_config
from haute._output_assembler import render_output_document
from haute._polars_utils import CollectEngine, execution_collect
from haute._sandbox import _get_project_root, set_project_root
from haute._source_cache import new_staging_token
from haute._types import (
    RESPONSE_NODE_TYPES,
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
)
from haute._workbench_output import (
    WorkbenchOutputError,
    WorkbenchOutputTables,
    as_response,
    column_fits,
)
from haute.codegen import graph_to_code_multi
from haute.deploy._config import DeployConfig, resolve_config
from haute.deploy._pruner import find_deploy_input_nodes, find_output_node
from haute.deploy._scorer import score_graph
from haute.errors import ConfigError, ExecutionError
from haute.execution import ExecutionProfile, execute_lazy_graph
from haute.executor import execute_graph
from haute.parser import parse_pipeline_file
from haute.routes._save_pipeline import SavePipelineService
from haute.trace import execute_trace
from tests.conftest import make_output_config

ONE_RESPONSE_NODE = (
    "Only one Quote Response or Workbench Output node is allowed per pipeline (found 2)."
)
NODE_TYPES_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "utils" / "nodeTypes.ts"


def _column(name: str, kind: str) -> dict[str, Any]:
    return {"name": name, "type": kind}


def _table(name: str, *columns: tuple[str, str], many: bool = False) -> dict[str, Any]:
    """A table in the shape the workbench supplies."""
    return {
        "name": name,
        "rows": "many" if many else "one",
        "columns": [_column(column, kind) for column, kind in columns],
    }


_REQUEST_TABLES = [
    _table("policy", ("state", "str"), ("limit", "int")),
    _table("items", ("item_id", "str"), ("value", "float"), many=True),
]
_SAMPLE = {
    "policy": {"state": "NY", "limit": 1000},
    "items": [{"item_id": "A", "value": 10.0}, {"item_id": "B", "value": 30.0}],
}
# `limit` is an integer upstream, which a Decimal column takes.
_RESPONSE_TABLES = [
    _table("pricing", ("premium", "float"), ("limit", "float"), ("referral", "str")),
    _table("item_prices", ("item_id", "str"), ("premium", "float"), many=True),
]
_RESPONSE = [
    {
        "pricing": {"premium": 100.0, "limit": 1000.0, "referral": "none"},
        "item_prices": [{"item_id": "A", "premium": 20.0}, {"item_id": "B", "premium": 60.0}],
    }
]
_PRICED = "df = policy.with_columns(premium=pl.col('limit') / 10, referral=pl.lit('none'))"
_ITEM_PREMIUMS = "df = items.with_columns(premium=pl.col('value') * 2)"


def _node(nid: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))


def _edge(
    source: str, target: str, *, frame: str | None = None, table: str | None = None
) -> GraphEdge:
    return GraphEdge(
        id=f"e_{source}_{target}_{frame or ''}_{table or ''}",
        source=source,
        target=target,
        sourceHandle=frame,
        targetHandle=table,
    )


def _priced(response_tables: list[dict[str, Any]] | None = None) -> PipelineGraph:
    """The sample quote priced: a premium per policy and per item, filling the response."""
    tables = copy.deepcopy(_RESPONSE_TABLES if response_tables is None else response_tables)
    return PipelineGraph(
        nodes=[
            _node(
                "request",
                NodeType.WORKBENCH_INPUT,
                {"tables": copy.deepcopy(_REQUEST_TABLES), "sample": copy.deepcopy(_SAMPLE)},
            ),
            _node("priced", NodeType.POLARS, {"code": _PRICED}),
            _node("item_premiums", NodeType.POLARS, {"code": _ITEM_PREMIUMS}),
            _node("response", NodeType.WORKBENCH_OUTPUT, {"tables": tables}),
        ],
        edges=[
            _edge("request", "priced", frame="policy"),
            _edge("request", "item_premiums", frame="items"),
            _edge("priced", "response", table="pricing"),
            _edge("item_premiums", "response", table="item_prices"),
        ],
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """One temporary project for the generated modules."""
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    try:
        yield tmp_path
    finally:
        set_project_root(original)


def _write(graph: PipelineGraph, project: Path, name: str = "") -> Path:
    """Write *graph*'s generated files and config files as save does; return the pipeline."""
    name = name or f"pipeline_{uuid.uuid4().hex}"
    files = {
        **graph_to_code_multi(graph, pipeline_name=name),
        **SavePipelineService._collect_node_configs_recursive(graph),
    }
    for relative, content in files.items():
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return project / f"{name}.py"


def _import(pipeline_file: Path) -> Any:
    spec = importlib.util.spec_from_file_location(pipeline_file.stem, pipeline_file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[pipeline_file.stem] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(pipeline_file.stem, None)
    return module


def _run(graph: PipelineGraph, read: Callable[[WorkbenchOutputTables], Any]) -> Any:
    """What *read* makes of the Workbench Output's result, in a walk of *graph*."""
    context = create_admitted_execution_context(
        operation="workbench_output", profile=ExecutionProfile.LAZY_SINK
    )
    try:
        outputs, _, _, _ = execute_lazy_graph(
            graph, _build_node_fn, source="live", execution_context=context
        )
        return read(outputs["response"])
    finally:
        context.release_admission(preserve_primary_error=True)


def _tables(graph: PipelineGraph) -> dict[str, pl.DataFrame]:
    return _run(graph, lambda tables: {label: _collected(frame) for label, frame in tables.items()})


def _response(graph: PipelineGraph) -> list[Any]:
    return _run(graph, lambda tables: render_output_document(_collected(as_response(tables))))


def _collected(frame: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    # Collected as Haute collects, so a failure inside a table's scan is raised as itself.
    return (
        execution_collect(frame, engine="streaming") if isinstance(frame, pl.LazyFrame) else frame
    )


def test_a_workbench_output_fills_its_tables_from_their_frames(project: Path) -> None:
    tables = _tables(_priced())

    # One frame per table, typed as declared, without the frames' other columns (`state`,
    # `value`); a request is answered with their response.
    assert list(tables) == ["pricing", "item_prices"]
    assert tables["pricing"].schema == pl.Schema(
        {"premium": pl.Float64, "limit": pl.Float64, "referral": pl.String}
    )
    assert tables["pricing"].to_dicts() == [_RESPONSE[0]["pricing"]]
    assert tables["item_prices"].to_dicts() == _RESPONSE[0]["item_prices"]
    assert _response(_priced()) == _RESPONSE


def _unfilled() -> PipelineGraph:
    """The priced graph, its pricing table declaring `referral`, a String upstream, an
    Integer, and two columns no frame column is named exactly: `discount` and `Premium`."""
    graph = _priced()
    pricing = graph.node_map["response"].data.config["tables"][0]
    pricing["columns"] = [
        _column("premium", "float"),
        _column("limit", "float"),
        _column("referral", "int"),
        _column("discount", "float"),
        _column("Premium", "float"),
    ]
    return graph


def test_a_column_the_frame_does_not_fill_is_nulls(project: Path) -> None:
    # A column of another type, one the frame lacks and one whose name differs only in case
    # are each a column of nulls of the declared type, and the run goes on.
    graph = _unfilled()

    tables = _tables(graph)

    assert tables["pricing"].schema == pl.Schema(
        {
            "premium": pl.Float64,
            "limit": pl.Float64,
            "referral": pl.Int64,
            "discount": pl.Float64,
            "Premium": pl.Float64,
        }
    )
    assert tables["pricing"].to_dicts() == [
        {"premium": 100.0, "limit": 1000.0, "referral": None, "discount": None, "Premium": None}
    ]
    assert _response(graph)[0]["pricing"] == {"premium": 100.0, "limit": 1000.0}


@pytest.mark.parametrize("rows", [0, 2])
def test_a_table_its_frame_fills_none_of_keeps_the_frames_rows(project: Path, rows: int) -> None:
    # Every column of `item_prices` is nulls, `item_id` a String declared an Integer and
    # `discount` no frame column, yet the table has as many rows as its frame.
    graph = _priced()
    graph.node_map["response"].data.config["tables"][1]["columns"] = [
        _column("item_id", "int"),
        _column("discount", "float"),
    ]
    if rows == 0:
        graph.node_map["item_premiums"].data.config["code"] = (
            f"{_ITEM_PREMIUMS}\ndf = df.filter(pl.lit(False))"
        )

    tables = _tables(graph)

    assert tables["item_prices"].to_dicts() == [{"item_id": None, "discount": None}] * rows


def test_a_column_of_nulls_fills_a_column_of_any_type(project: Path) -> None:
    # A frame column that is null throughout has no dtype to disagree with the declared one.
    graph = _priced()
    graph.node_map["priced"].data.config["code"] = (
        f"{_PRICED}\ndf = df.with_columns(referral=pl.lit(None))"
    )

    (pricing,) = _tables(graph)["pricing"].to_dicts()

    assert pricing == {"premium": 100.0, "limit": 1000.0, "referral": None}


def test_a_workbench_output_previews_before_the_workbench_has_a_sample(project: Path) -> None:
    # Each input table is then one row of nulls: each one-row table still has its one row.
    graph = _priced()
    del graph.node_map["request"].data.config["sample"]

    tables = _tables(graph)

    assert tables["pricing"].to_dicts() == [{"premium": None, "limit": None, "referral": "none"}]
    assert tables["item_prices"].to_dicts() == [{"item_id": None, "premium": None}]


def test_a_table_nothing_is_connected_to_is_nulls(project: Path) -> None:
    # A one-row table is one row of nulls and a many-row table no rows, beside a table that is
    # connected.
    graph = _priced()
    graph.edges = [edge for edge in graph.edges if edge.targetHandle != "item_prices"]

    tables = _tables(graph)

    assert tables["pricing"].to_dicts() == [_RESPONSE[0]["pricing"]]
    assert tables["item_prices"].schema == pl.Schema({"item_id": pl.String, "premium": pl.Float64})
    assert tables["item_prices"].height == 0
    assert _response(graph) == [{"pricing": _RESPONSE[0]["pricing"]}]


def test_a_workbench_output_with_nothing_connected_runs(project: Path) -> None:
    graph = _priced()
    graph.edges = [edge for edge in graph.edges if edge.target != "response"]
    pipeline_file = _write(graph, project, "main")

    tables = _tables(graph)

    assert tables["pricing"].to_dicts() == [{"premium": None, "limit": None, "referral": None}]
    assert tables["item_prices"].height == 0
    assert _response(graph) == [{}]
    # As Preview prices it, every table from one run of a saved pipeline with shared
    # snapshots, and as the pipeline file runs.
    previewed = execute_graph(
        parse_pipeline_file(pipeline_file),
        target_node_id="response",
        port_labels=["pricing", "item_prices"],
        shared_snapshots=True,
        staging_token=new_staging_token(),
    )["response"]
    assert previewed.status == "ok", previewed.error
    assert {label: each.preview for label, each in previewed.port_previews.items()} == {
        "pricing": [{"premium": None, "limit": None, "referral": None}],
        "item_prices": [],
    }
    pipeline = _import(pipeline_file).pipeline
    assert render_output_document(_collected(pipeline.run(source="live"))) == [{}]


def test_the_editor_previews_a_table_at_a_time(project: Path) -> None:
    # As the preview route runs it, on a saved pipeline with shared snapshots, where a node
    # with two inputs is a capture point unless its result is a bundle of frames.
    saved = parse_pipeline_file(_write(_priced(), project, "main"))
    assert saved.source_file
    results = execute_graph(
        saved,
        target_node_id="response",
        port_label="item_prices",
        shared_snapshots=True,
        staging_token=new_staging_token(),
    )

    previewed = results["response"]
    assert previewed.status == "ok", previewed.error
    assert list(previewed.frame_columns) == ["pricing", "item_prices"]
    assert previewed.preview == _RESPONSE[0]["item_prices"]


def _three_tables() -> PipelineGraph:
    """`_priced` with a third table, a summary of the policy's state and premium."""
    tables = [*_RESPONSE_TABLES, _table("summary", ("state", "str"), ("premium", "float"))]
    graph = _priced(tables)
    summary = "df = policy.select('state', premium=pl.col('limit') / 10)"
    graph.nodes.append(_node("summarised", NodeType.POLARS, {"code": summary}))
    graph.edges.append(_edge("request", "summarised", frame="policy"))
    graph.edges.append(_edge("summarised", "response", table="summary"))
    return graph


def test_the_editor_previews_every_table_in_one_run(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preview prices the sample in one run of the pipeline, however many tables the
    Workbench Output fills: each table's answer is what a preview of it alone gives."""
    import haute.executor as executor

    saved = parse_pipeline_file(_write(_three_tables(), project, "main"))
    runs: list[str | None] = []
    eager_execute = executor._eager_execute

    def counted(graph: PipelineGraph, target: str | None, *args: Any, **kwargs: Any) -> Any:
        runs.append(target)
        return eager_execute(graph, target, *args, **kwargs)

    monkeypatch.setattr(executor, "_eager_execute", counted)
    labels = ["pricing", "item_prices", "summary"]

    results = execute_graph(
        saved,
        target_node_id="response",
        port_labels=labels,
        shared_snapshots=True,
        staging_token=new_staging_token(),
    )

    previewed = results["response"]
    assert previewed.status == "ok", previewed.error
    assert runs == ["response"]
    assert list(previewed.port_previews) == labels
    assert previewed.port_previews["item_prices"].preview == _RESPONSE[0]["item_prices"]
    assert previewed.port_previews["summary"].preview == [{"state": "NY", "premium": 100.0}]
    # Each table's own columns, as its frame holds them.
    assert [(c.name, c.dtype) for c in previewed.port_previews["summary"].columns] == [
        ("state", "String"),
        ("premium", "Float64"),
    ]
    for label in labels:
        alone = execute_graph(
            saved,
            target_node_id="response",
            port_label=label,
            shared_snapshots=True,
            staging_token=new_staging_token(),
        )["response"]
        each = previewed.port_previews[label]
        assert (each.preview, each.row_count, each.preview_truncated) == (
            alone.preview,
            alone.row_count,
            alone.preview_truncated,
        )
        assert [column.name for column in each.columns] == [column.name for column in alone.columns]


def test_a_preview_of_a_table_the_workbench_output_does_not_fill_says_so(project: Path) -> None:
    saved = parse_pipeline_file(_write(_priced(), project, "main"))

    previewed = execute_graph(
        saved,
        target_node_id="response",
        port_labels=["pricing", "missing"],
        shared_snapshots=True,
        staging_token=new_staging_token(),
    )["response"]

    assert previewed.status == "error"
    assert previewed.error == "response has no table 'missing' to preview."


def test_the_preview_route_answers_every_table_asked_for_from_one_request(
    project: Path,
) -> None:
    from fastapi.testclient import TestClient

    from haute.server import app

    saved = parse_pipeline_file(_write(_three_tables(), project, "main"))
    client = TestClient(app)
    body = {"graph": saved.model_dump(), "node_id": "response", "row_limit": 100}

    answer = client.post(
        "/api/pipeline/preview", json={**body, "port_labels": ["pricing", "summary"]}
    )

    assert answer.status_code == 200, answer.text
    previews = answer.json()["port_previews"]
    assert list(previews) == ["pricing", "summary"]
    assert previews["summary"]["preview"] == [{"state": "NY", "premium": 100.0}]
    assert previews["summary"]["row_count"] == 1
    assert previews["summary"]["preview_truncated"] is False
    # One frame, or several: never both.
    both = client.post(
        "/api/pipeline/preview",
        json={**body, "port_label": "pricing", "port_labels": ["pricing"]},
    )
    assert both.status_code == 422


def test_a_workbench_outputs_cell_is_traced_from_the_node_filling_its_table(project: Path) -> None:
    # The table holds that node's rows: the same row, as the clicked values confirm.
    traced = execute_trace(
        _priced(),
        row_index=1,
        target_node_id="response",
        column="premium",
        row_values=_RESPONSE[0]["item_prices"][1],
        port_label="item_prices",
    )

    assert (traced.target_node_id, traced.column, traced.output_value) == (
        "item_premiums",
        "premium",
        60.0,
    )
    assert [step.node_id for step in traced.steps] == ["request", "item_premiums"]
    # A Workbench Output with one table needs no table named.
    one_table = _priced([_RESPONSE_TABLES[0]])
    one_table.edges = [edge for edge in one_table.edges if edge.source != "item_premiums"]
    traced = execute_trace(one_table, target_node_id="response", column="premium")
    assert (traced.target_node_id, traced.output_value) == ("priced", 100.0)


def test_a_workbench_outputs_cell_fed_straight_from_the_input_is_traced_from_its_frame(
    project: Path,
) -> None:
    # The table is one frame of the Workbench Input, which emits one frame per table.
    graph = _priced([_table("items", ("item_id", "str"), ("value", "float"), many=True)])
    graph.nodes = [node for node in graph.nodes if node.id in ("request", "response")]
    graph.edges = [_edge("request", "response", frame="items", table="items")]

    traced = execute_trace(
        graph,
        row_index=1,
        target_node_id="response",
        column="value",
        row_values={"item_id": "B", "value": 30.0},
        port_label="items",
    )

    assert (traced.target_node_id, traced.column, traced.output_value) == ("request", "value", 30.0)


def test_a_workbench_outputs_cell_beside_nulls_is_traced_from_its_frame(project: Path) -> None:
    # The clicked row's nulls are not its frame's values: `referral` holds "none" there, so
    # the row is matched by the columns the frame fills.
    graph = _unfilled()
    row = {"premium": 100.0, "limit": 1000.0, "referral": None, "discount": None, "Premium": None}

    traced = execute_trace(
        graph, target_node_id="response", column="premium", row_values=row, port_label="pricing"
    )

    assert (traced.target_node_id, traced.column, traced.output_value) == ("priced", "premium", 100)
    # A column of nulls has no value to trace.
    for unfilled in ("referral", "discount", "Premium"):
        with pytest.raises(ValueError, match=f"fills '{unfilled}' with nulls, as the frame"):
            execute_trace(graph, target_node_id="response", column=unfilled, port_label="pricing")


@pytest.mark.parametrize(
    ("port_label", "column", "message"),
    [
        (None, "premium", "name the table the row is from, one of 'pricing', 'item_prices'"),
        ("quotes", "premium", "with no 'quotes' table; its tables are 'pricing', 'item_prices'"),
        ("pricing", "state", "whose 'pricing' table has no 'state' column"),
    ],
)
def test_a_workbench_outputs_cell_it_cannot_place_says_why(
    project: Path, port_label: str | None, column: str, message: str
) -> None:
    refusal = f"^Target node 'response' is a Workbench Output.*{message}"
    with pytest.raises(ValueError, match=refusal):
        execute_trace(_priced(), target_node_id="response", column=column, port_label=port_label)


def test_a_workbench_output_table_with_nothing_connected_has_nothing_to_trace(
    project: Path,
) -> None:
    graph = _priced()
    graph.edges = [edge for edge in graph.edges if edge.targetHandle != "pricing"]

    with pytest.raises(ValueError, match="nothing connected to its 'pricing' table"):
        execute_trace(graph, target_node_id="response", column="premium", port_label="pricing")


def test_generated_code_runs_a_workbench_output(project: Path) -> None:
    pipeline_file = _write(_priced(), project)

    code = pipeline_file.read_text(encoding="utf-8")
    assert '@pipeline.workbench_output(config="config/workbench_output/response.json")' in code
    assert 'pipeline.connect("priced", "response", target_port="pricing")' in code
    parsed = parse_pipeline_file(pipeline_file)
    assert parsed.node_map["response"].data.nodeType is NodeType.WORKBENCH_OUTPUT
    assert parsed.node_map["response"].data.config["tables"] == _RESPONSE_TABLES
    assert sorted(
        (edge.source, edge.targetHandle) for edge in parsed.edges if edge.target == "response"
    ) == [("item_premiums", "item_prices"), ("priced", "pricing")]

    pipeline = _import(pipeline_file).pipeline
    assert render_output_document(_collected(pipeline.run(source="live"))) == _RESPONSE
    scored = pipeline.score(
        {
            "policy": pl.DataFrame({"state": ["TX"], "limit": [500]}),
            "items": pl.DataFrame({"item_id": ["Z"], "value": [7.0]}),
        }
    )
    assert render_output_document(_collected(scored)) == [
        {
            "pricing": {"premium": 50.0, "limit": 500.0, "referral": "none"},
            "item_prices": [{"item_id": "Z", "premium": 14.0}],
        }
    ]


def _one_table_quote() -> PipelineGraph:
    """One request table, which a deployed pipeline reads out of the request."""
    request = _table("quote", ("state", "str"), ("limit", "int"))
    return PipelineGraph(
        nodes=[
            _node("request", NodeType.WORKBENCH_INPUT, {"tables": [request]}),
            _node("priced", NodeType.POLARS, {"code": _PRICED.replace("policy", "quote")}),
            _node(
                "response",
                NodeType.WORKBENCH_OUTPUT,
                {"tables": [_table("pricing", ("premium", "float"), ("state", "str"))]},
            ),
        ],
        edges=[
            _edge("request", "priced", frame="quote"),
            _edge("priced", "response", table="pricing"),
        ],
    )


def test_deploy_answers_with_a_workbench_output(project: Path) -> None:
    graph = _one_table_quote()
    # One quote, as a deployed pipeline's request holds it: each table under its name.
    request = pl.DataFrame([{"quote": {"state": "TX", "limit": 500}}])

    assert find_output_node(graph) == "response"
    scored = score_graph(
        graph=graph,
        input_df=request,
        input_node_ids=find_deploy_input_nodes(graph),
        output_node_id="response",
    )
    assert render_output_document(scored) == [{"pricing": {"premium": 50.0, "state": "TX"}}]

    resolved = resolve_config(
        DeployConfig(pipeline_file=_write(graph, project, "main"), model_name="m")
    )
    assert resolved.output_node_id == "response"
    assert list(resolved.output_schema) == ["pricing"]

    # `[deploy].output_fields` names the response's fields, which a Workbench Output's tables
    # become.
    picked = score_graph(
        graph=graph,
        input_df=request,
        input_node_ids=find_deploy_input_nodes(graph),
        output_node_id="response",
        output_fields=["pricing"],
    )
    assert render_output_document(picked) == [{"pricing": {"premium": 50.0, "state": "TX"}}]


def test_deploy_answers_with_nulls_for_what_the_pipeline_does_not_fill(project: Path) -> None:
    # `state` is a String upstream declared an Integer, `discount` no frame column, and
    # `layers` a table nothing is connected to: each null, left out of the answer.
    graph = _one_table_quote()
    graph.node_map["response"].data.config["tables"] = [
        _table("pricing", ("premium", "float"), ("state", "int"), ("discount", "float")),
        _table("layers", ("layer", "int"), many=True),
    ]
    request = pl.DataFrame([{"quote": {"state": "TX", "limit": 500}}])

    scored = score_graph(
        graph=graph,
        input_df=request,
        input_node_ids=find_deploy_input_nodes(graph),
        output_node_id="response",
    )

    assert render_output_document(scored) == [{"pricing": {"premium": 50.0}}]


_IDENTITY = (("quote_reference", "str"), ("quote_option", "int"))


def _identified() -> PipelineGraph:
    """A quote whose tables lead with its reference and option, as the form makes them: an
    item's premium, its frame carrying a reference of its own, and a total that drops them."""
    details = _table(
        "quote_details", ("quote_reference", "str"), ("quote_option", "int"), ("name", "str")
    )
    items = _table("items", *_IDENTITY, ("item_id", "str"), ("value", "float"), many=True)
    sample = {"items": [{"item_id": "A", "value": 10.0}, {"item_id": "B", "value": 30.0}]}
    return PipelineGraph(
        nodes=[
            _node(
                "request", NodeType.WORKBENCH_INPUT, {"tables": [details, items], "sample": sample}
            ),
            _node(
                "item_premiums",
                NodeType.POLARS,
                {
                    "code": (
                        "df = items.with_columns(premium=pl.col('value') * 2, "
                        "quote_reference=pl.lit('X'))"
                    )
                },
            ),
            _node(
                "total",
                NodeType.POLARS,
                {"code": "df = items.select(premium=pl.col('value').sum())"},
            ),
            _node(
                "response",
                NodeType.WORKBENCH_OUTPUT,
                {
                    "tables": [
                        _table("pricing", *_IDENTITY, ("premium", "float")),
                        _table("item_prices", *_IDENTITY, ("item_id", "str"), many=True),
                    ]
                },
            ),
        ],
        edges=[
            _edge("request", "item_premiums", frame="items"),
            _edge("request", "total", frame="items"),
            _edge("total", "response", table="pricing"),
            _edge("item_premiums", "response", table="item_prices"),
        ],
    )


def test_the_response_gives_every_row_the_quotes_reference_and_option(project: Path) -> None:
    graph = _identified()
    request = pl.DataFrame(
        [
            {
                "quote_details": {
                    "quote_reference": "Q-000123",
                    "quote_option": 2,
                    "name": "Fleet",
                },
                "items": [{"item_id": "A", "value": 10.0}, {"item_id": "B", "value": 30.0}],
            }
        ]
    )

    scored = score_graph(
        graph=graph,
        input_df=request,
        input_node_ids=find_deploy_input_nodes(graph),
        output_node_id="response",
    )

    # The total dropped them and the items' frame held a reference of its own: the response
    # holds the quote's, whatever the pipeline did with them.
    identity = {"quote_reference": "Q-000123", "quote_option": 2}
    assert render_output_document(scored) == [
        {
            "pricing": {**identity, "premium": 40.0},
            "item_prices": [{**identity, "item_id": "A"}, {**identity, "item_id": "B"}],
        }
    ]
    # The Workbench Output's own tables hold them blank, as the editor shows them.
    tables = _tables(graph)
    assert (
        tables["item_prices"].select("quote_reference", "quote_option").to_dicts()
        == [{"quote_reference": None, "quote_option": None}] * 2
    )


def test_generated_code_answers_under_the_quote_details_frame_it_has(project: Path) -> None:
    pipeline = _import(_write(_identified(), project)).pipeline

    # Run, the sample is priced under the stand-in; scored without quote_details, which
    # nothing reads here, the identity is blank and left out of the answer.
    [ran] = render_output_document(_collected(pipeline.run(source="live")))
    assert ran["item_prices"][0] == {
        "quote_reference": "PREVIEW",
        "quote_option": 1,
        "item_id": "A",
    }
    scored = pipeline.score(
        pl.DataFrame(
            {"quote_reference": ["Q-1"], "quote_option": [1], "item_id": ["Z"], "value": [7.0]}
        )
    )
    assert render_output_document(_collected(scored)) == [
        {"pricing": {"premium": 7.0}, "item_prices": [{"item_id": "Z"}]}
    ]


def test_a_workbench_outputs_cell_beside_the_quotes_identity_is_traced_from_its_frame(
    project: Path,
) -> None:
    # The table holds the identity blank, its frame a reference of its own: the row is
    # matched by the columns the frame fills.
    row = {"quote_reference": None, "quote_option": None, "item_id": "B"}

    traced = execute_trace(
        _identified(),
        row_index=1,
        target_node_id="response",
        column="item_id",
        row_values=row,
        port_label="item_prices",
    )

    assert (traced.target_node_id, traced.column, traced.output_value) == (
        "item_premiums",
        "item_id",
        "B",
    )
    # The response gives the identity, so the table has none to trace.
    for blank in ("quote_reference", "quote_option"):
        with pytest.raises(ValueError, match=f"holds the quote's '{blank}' blank, as the"):
            execute_trace(
                _identified(), target_node_id="response", column=blank, port_label="item_prices"
            )


def _no_tables(graph: PipelineGraph) -> None:
    graph.node_map["response"].data.config["tables"] = []
    graph.edges = [edge for edge in graph.edges if edge.target != "response"]


def _no_columns(graph: PipelineGraph) -> None:
    for table in graph.node_map["response"].data.config["tables"]:
        table["columns"] = []
    graph.edges = [edge for edge in graph.edges if edge.target != "response"]


def _on_no_table(graph: PipelineGraph) -> None:
    graph.edges = [
        edge.model_copy(update={"targetHandle": "nope"}) if edge.targetHandle == "pricing" else edge
        for edge in graph.edges
    ]


def _fed(code: str) -> Callable[[PipelineGraph], None]:
    def change(graph: PipelineGraph) -> None:
        graph.node_map["priced"].data.config["code"] = code

    return change


def _fills_none_of_pricing(code: str) -> Callable[[PipelineGraph], None]:
    """`pricing` declaring only columns its frame does not fill, the frame made by *code*."""

    def change(graph: PipelineGraph) -> None:
        graph.node_map["response"].data.config["tables"][0]["columns"] = [
            _column("referral", "int"),
            _column("discount", "float"),
        ]
        graph.node_map["priced"].data.config["code"] = code

    return change


_FAILURES: dict[str, tuple[Callable[[PipelineGraph], None], str]] = {
    "no_tables": (
        _no_tables,
        "This Workbench Output has no tables: add output tables to the workbench's schema "
        "and save it.",
    ),
    "no_columns": (
        _no_columns,
        "This Workbench Output's tables have no columns yet: add their columns in the "
        "workbench's schema and save it.",
    ),
    "on_no_table": (_on_no_table, "on 'nope', which is none of its tables"),
    "two_rows": (
        _fed(f"{_PRICED}\ndf = pl.concat([df, df])"),
        "The Workbench Output's 'pricing' table has one row per quote, but the frame "
        "connected to it has 2 rows. A Workbench Output answers one quote per request.",
    ),
    "no_rows": (
        _fed(f"{_PRICED}\ndf = df.filter(pl.lit(False))"),
        "has one row per quote, but the frame connected to it has no rows.",
    ),
    # A table of nulls alone still counts its frame's rows.
    "two_rows_of_nulls": (
        _fills_none_of_pricing(f"{_PRICED}\ndf = pl.concat([df, df])"),
        "has one row per quote, but the frame connected to it has 2 rows.",
    ),
    "no_rows_of_nulls": (
        _fills_none_of_pricing(f"{_PRICED}\ndf = df.filter(pl.lit(False))"),
        "has one row per quote, but the frame connected to it has no rows.",
    ),
}


@pytest.mark.parametrize(("change", "says"), list(_FAILURES.values()), ids=list(_FAILURES))
def test_a_workbench_output_that_cannot_fill_its_tables_fails(
    project: Path, change: Callable[[PipelineGraph], None], says: str
) -> None:
    graph = _priced()
    change(graph)

    with pytest.raises(WorkbenchOutputError, match=re.escape(says)):
        _tables(graph)


@pytest.mark.parametrize(
    ("declared", "dtype", "fits"),
    [
        ("int", pl.Int32(), True),
        ("int", pl.UInt8(), True),
        ("int", pl.Float64(), False),
        ("float", pl.Int64(), True),
        ("float", pl.Decimal(12, 2), True),
        ("float", pl.String(), False),
        ("str", pl.String(), True),
        ("str", pl.Categorical(), True),
        ("str", pl.Enum(["a"]), True),
        ("str", pl.Int64(), False),
        ("bool", pl.Boolean(), True),
        ("bool", pl.Int64(), False),
        ("date", pl.Date(), True),
        ("date", pl.Datetime("us"), False),
        ("date", pl.String(), False),
        ("int", pl.Null(), True),
    ],
    ids=str,
)
def test_which_frame_columns_fill_a_declared_column(
    declared: str, dtype: pl.DataType, fits: bool
) -> None:
    """Any integer fills an Integer, any number a Decimal, a category Text; a null anything."""
    assert column_fits(declared, dtype) is fits


def test_a_workbench_output_config_with_a_mapping_is_refused(project: Path) -> None:
    # Columns are filled by name alone: a mapping left in a sidecar is refused wherever the
    # sidecar is read, rather than ignored, which would fill the columns differently.
    pipeline_file = _write(_priced(), project)
    sidecar = project / "config" / "workbench_output" / "response.json"
    config = json.loads(sidecar.read_text(encoding="utf-8"))
    sidecar.write_text(
        json.dumps({**config, "mapping": {"pricing": {"premium": "limit"}}}), encoding="utf-8"
    )
    removed = "workbenchOutput config contains the removed mapping field"

    with pytest.raises(ConfigError, match=removed):
        parse_pipeline_file(pipeline_file)
    # As the pipeline file runs on its own, which reads the sidecar as it runs.
    with pytest.raises(ConfigError, match=removed):
        _import(pipeline_file).pipeline.run(source="live")


def test_a_request_of_several_quotes_fails_a_workbench_output(project: Path) -> None:
    pipeline = _import(_write(_priced(), project)).pipeline

    with pytest.raises(WorkbenchOutputError, match="but the frame connected to it has 2 rows"):
        _collected(
            pipeline.score(
                {
                    "policy": pl.DataFrame({"state": ["TX", "CA"], "limit": [500, 700]}),
                    "items": pl.DataFrame({"item_id": ["Z"], "value": [7.0]}),
                }
            )
        )


def test_a_workbench_outputs_tables_are_read_lazily() -> None:
    # Two rows in a one-row table: reading it fails, reading its schema does not.
    pricing = pl.LazyFrame({"premium": [1.0, 2.0], "limit": [1, 2], "referral": ["a", "b"]})
    items = pl.LazyFrame({"item_id": ["A"], "premium": [3.0]})

    tables = assemble_workbench_output_from_config(
        pricing,
        items,
        config={"tables": copy.deepcopy(_RESPONSE_TABLES)},
        ports=["pricing", "item_prices"],
    )

    assert list(tables["pricing"].collect_schema()) == ["premium", "limit", "referral"]
    assert list(as_response(tables).collect_schema()) == ["pricing", "item_prices"]
    with pytest.raises(WorkbenchOutputError, match="has 2 rows"):
        execution_collect(tables["pricing"], engine="streaming")
    with pytest.raises(WorkbenchOutputError, match="runs only where its connections are known"):
        assemble_workbench_output_from_config(
            pricing, items, config={"tables": copy.deepcopy(_RESPONSE_TABLES)}
        )


@pytest.mark.parametrize("engine", ["streaming", "in-memory"])
def test_a_workbench_outputs_tables_read_the_same_on_either_engine(engine: CollectEngine) -> None:
    """A table is read inside a Python scan, which the two engines run on different threads."""
    pricing = pl.LazyFrame({"premium": [1.0], "limit": [1], "referral": ["a"]})
    items = pl.LazyFrame({"item_id": ["A", "B"], "premium": [3.0, 4.0]})
    tables = assemble_workbench_output_from_config(
        pricing,
        items,
        config={"tables": copy.deepcopy(_RESPONSE_TABLES)},
        ports=["pricing", "item_prices"],
    )

    pricing_rows = execution_collect(tables["pricing"], engine=engine)
    response = execution_collect(as_response(tables), engine=engine)

    assert pricing_rows.to_dicts() == [{"premium": 1.0, "limit": 1.0, "referral": "a"}]
    assert response.to_dicts() == [
        {
            "pricing": {"premium": 1.0, "limit": 1.0, "referral": "a"},
            "item_prices": [
                {"item_id": "A", "premium": 3.0},
                {"item_id": "B", "premium": 4.0},
            ],
        }
    ]


def test_the_editor_holds_the_same_response_nodes() -> None:
    text = NODE_TYPES_TS.read_text(encoding="utf-8")
    match = re.search(r"export const RESPONSE_TYPES = new Set<\w+>\(\[(.*?)\]\)", text, re.S)
    assert match, "nodeTypes.ts declares no RESPONSE_TYPES"
    editor = {NodeType[member] for member in re.findall(r"NODE_TYPES\.(\w+)", match.group(1))}

    assert editor == RESPONSE_NODE_TYPES == {NodeType.OUTPUT, NodeType.WORKBENCH_OUTPUT}


def test_a_pipeline_has_one_response_node(project: Path) -> None:
    graph = _priced()
    graph.nodes.append(
        _node(
            "quote_response", NodeType.OUTPUT, make_output_config(["premium"], source_port="priced")
        )
    )
    graph.edges.append(_edge("priced", "quote_response"))

    with pytest.raises(HTTPException) as refused:
        SavePipelineService(project).validate_graph(graph, source_file="main.py")
    assert refused.value.status_code == 400
    assert refused.value.detail == ONE_RESPONSE_NODE

    main = _write(graph, project, "main")
    with pytest.raises(ValueError, match=re.escape(ONE_RESPONSE_NODE)):
        resolve_config(DeployConfig(pipeline_file=main, model_name="m"))
    with pytest.raises(ExecutionError, match="multiple response nodes"):
        _import(main).pipeline.run(source="live")
