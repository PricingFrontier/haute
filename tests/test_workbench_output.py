"""The Workbench Output: the response's tables, filled from the frames connected to them.

Its tables are a copy of the response's tables the project's workbench supplies
(specs/workbench). Each is a port: the frame connected to it fills it, each column from
the frame column its mapping picks or else the same-named one, given its declared type. The
node's result is its tables; where the pipeline answers a request they become the response
for one quote, each table under its name.
"""

from __future__ import annotations

import copy
import importlib.util
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
from haute._polars_utils import execution_collect
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
    _fits,
    as_response,
)
from haute.codegen import graph_to_code_multi
from haute.deploy._config import DeployConfig, resolve_config
from haute.deploy._pruner import find_deploy_input_nodes, find_output_node
from haute.deploy._scorer import score_graph
from haute.errors import ExecutionError
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
    return execution_collect(frame) if isinstance(frame, pl.LazyFrame) else frame


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


def _mapped(mapping: dict[str, Any], *extra: tuple[str, str]) -> PipelineGraph:
    """The priced graph, its pricing table given *extra* columns and its mapping *mapping*."""
    graph = _priced()
    config = graph.node_map["response"].data.config
    config["tables"][0]["columns"] += [_column(name, kind) for name, kind in extra]
    config["mapping"] = mapping
    return graph


def test_the_mapping_picks_what_fills_each_column(project: Path) -> None:
    # Two picks swap names, `referral` is filled by nothing, and `discount`, which no column of
    # the frame is named, is filled by nothing too: each nothing a column of nulls.
    graph = _mapped(
        {"pricing": {"premium": "limit", "limit": "premium", "referral": None}},
        ("discount", "float"),
    )

    (pricing,) = _tables(graph)["pricing"].to_dicts()

    assert pricing == {"premium": 1000.0, "limit": 100.0, "referral": None, "discount": None}
    assert _response(graph)[0]["pricing"] == {"premium": 1000.0, "limit": 100.0}


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


def test_a_workbench_output_with_nothing_connected_says_what_to_connect(project: Path) -> None:
    graph = _priced()
    graph.edges = [edge for edge in graph.edges if edge.target != "response"]
    saved = parse_pipeline_file(_write(graph, project, "main"))

    with pytest.raises(WorkbenchOutputError, match="Connect a frame to the Workbench Output's"):
        _tables(graph)
    # As the editor previews it, on a saved pipeline with shared snapshots.
    results = execute_graph(
        saved, target_node_id="response", shared_snapshots=True, staging_token=new_staging_token()
    )
    assert results["response"].status == "error"
    assert "Connect a frame to the Workbench Output's 'pricing' table." in str(
        results["response"].error
    )


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


def test_a_workbench_outputs_cell_is_traced_from_the_node_filling_its_table(project: Path) -> None:
    with pytest.raises(ValueError, match="trace the node connected to the table instead"):
        execute_trace(_priced(), row_index=0, target_node_id="response", column="premium")

    traced = execute_trace(_priced(), row_index=0, target_node_id="item_premiums", column="premium")
    assert [step.node_id for step in traced.steps] == ["request", "item_premiums"]


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


def _no_tables(graph: PipelineGraph) -> None:
    graph.node_map["response"].data.config["tables"] = []
    graph.edges = [edge for edge in graph.edges if edge.target != "response"]


def _no_columns(graph: PipelineGraph) -> None:
    for table in graph.node_map["response"].data.config["tables"]:
        table["columns"] = []
    graph.edges = [edge for edge in graph.edges if edge.target != "response"]


def _unconnected(graph: PipelineGraph) -> None:
    graph.edges = [edge for edge in graph.edges if edge.targetHandle != "item_prices"]


def _on_no_table(graph: PipelineGraph) -> None:
    graph.edges = [
        edge.model_copy(update={"targetHandle": "nope"}) if edge.targetHandle == "pricing" else edge
        for edge in graph.edges
    ]


def _declares(column: tuple[str, str]) -> Callable[[PipelineGraph], None]:
    def change(graph: PipelineGraph) -> None:
        pricing = graph.node_map["response"].data.config["tables"][0]
        pricing["columns"] = [
            entry for entry in pricing["columns"] if entry["name"] != column[0]
        ] + [_column(*column)]

    return change


def _remapped(mapping: dict[str, Any]) -> Callable[[PipelineGraph], None]:
    def change(graph: PipelineGraph) -> None:
        graph.node_map["response"].data.config["mapping"] = mapping

    return change


def _fed(code: str) -> Callable[[PipelineGraph], None]:
    def change(graph: PipelineGraph) -> None:
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
    "unconnected": (_unconnected, "Connect a frame to the Workbench Output's 'item_prices' table."),
    "on_no_table": (_on_no_table, "on 'nope', which is none of its tables"),
    "mapped_from_a_missing_column": (
        _remapped({"pricing": {"premium": "nope"}}),
        "The Workbench Output's 'pricing' table fills 'premium' from 'nope', which the frame "
        "connected to it doesn't have; it has state, limit, premium, referral.",
    ),
    "mapping_a_column_it_lacks": (
        _remapped({"pricing": {"discount": "premium"}}),
        "The Workbench Output's mapping has a 'discount' column in its 'pricing' table, which "
        "has no such column.",
    ),
    "mapping_not_an_object": (
        _remapped("premium"),
        "The Workbench Output's mapping must be an object of tables, each an object of columns.",
    ),
    "mapping_a_list": (
        _remapped([]),
        "The Workbench Output's mapping must be an object of tables, each an object of columns.",
    ),
    "mapping_a_table_it_lacks": (
        _remapped({"nope": {"premium": "premium"}}),
        "The Workbench Output's mapping has a 'nope' table, which is none of its tables "
        "(pricing, item_prices).",
    ),
    "mapping_entries_not_an_object": (
        _remapped({"pricing": "premium"}),
        "The Workbench Output's mapping for its 'pricing' table must be an object of columns.",
    ),
    "mapping_source_not_a_name": (
        _remapped({"pricing": {"premium": 5}}),
        "The Workbench Output's mapping for 'pricing'.'premium' must name a column, or be null "
        "for none.",
    ),
    "mistyped_column": (
        _declares(("referral", "int")),
        "The Workbench Output's 'pricing' table declares 'referral' as int, but 'referral', "
        "which fills it, is String.",
    ),
    "mistyped_bool": (
        _declares(("referral", "bool")),
        "declares 'referral' as bool, but 'referral', which fills it, is String.",
    ),
    "mistyped_date": (
        _declares(("referral", "date")),
        "declares 'referral' as date, but 'referral', which fills it, is String.",
    ),
    "two_rows": (
        _fed(f"{_PRICED}\ndf = pl.concat([df, df])"),
        "The Workbench Output's 'pricing' table has one row per quote, but the frame "
        "connected to it has 2 rows. A Workbench Output answers one quote per request.",
    ),
    "no_rows": (
        _fed(f"{_PRICED}\ndf = df.filter(pl.lit(False))"),
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
    assert _fits(declared, dtype) is fits


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
        execution_collect(tables["pricing"])
    with pytest.raises(WorkbenchOutputError, match="runs only where its connections are known"):
        assemble_workbench_output_from_config(
            pricing, items, config={"tables": copy.deepcopy(_RESPONSE_TABLES)}
        )


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
