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
import math
import re
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from haute._builders import _build_node_fn
from haute._execution_admission import create_admitted_execution_context
from haute._node_apply import assemble_workbench_output_from_config
from haute._node_specs import RESPONSE_NODE_TYPES
from haute._output_assembler import render_output_document
from haute._polars_utils import CollectEngine, execution_collect
from haute._project_context import current_project
from haute._source_cache import new_staging_token
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute._workbench_input import WorkbenchInputError
from haute._workbench_nodes import with_tables
from haute._workbench_output import (
    WorkbenchOutputError,
    WorkbenchOutputTables,
    as_response,
    column_fits,
    quote_identity,
)
from haute._workbench_tables import workbench_tables
from haute.codegen import graph_to_code_multi
from haute.deploy._config import DeployConfig, resolve_config
from haute.deploy._pruner import find_deploy_input_nodes, find_output_node
from haute.deploy._schema import infer_output_schema
from haute.deploy._scorer import score_graph
from haute.errors import ConfigError, ExecutionError, RequestRefusedError
from haute.execution import ExecutionProfile, execute_lazy_graph
from haute.executor import execute_graph
from haute.parser import parse_pipeline_file
from haute.routes._save_pipeline import SavePipelineService
from haute.trace import execute_trace
from tests._project import project_app, use_project
from tests._workbench_helpers import (
    form_table,
    held_tables,
    tables_form,
    without_form_tables,
    workbench_project,
)
from tests.conftest import make_output_config

ONE_RESPONSE_NODE = (
    "Only one Quote Response or Workbench Output node is allowed per pipeline (found 2)."
)
NODE_TYPES_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "utils" / "nodeTypes.ts"


def _column(name: str, kind: str) -> dict[str, Any]:
    return {"name": name, "type": kind}


def _table(
    name: str,
    *columns: tuple[str, str],
    many: bool = False,
    side: str = "output",
    key: str | None = None,
    index_kind: str | None = None,
) -> dict[str, Any]:
    """A side of a table in the shape the workbench supplies: the Workbench Output's, or the
    Workbench Input's, its port ``<name>_<side>``. A many-row side is keyed by its index,
    *key*, its first column of its own unless given: an input side's null quote row numbered
    1, as a numbered index numbers it; an output side given without *key* is a table of
    outputs alone's, its index the pipeline's (*index_kind* ``typed``) unless said."""
    table: dict[str, Any] = {
        "name": name,
        "side": side,
        "port": f"{name}_{side}",
        "rows": "many" if many else "one",
        "columns": [_column(column, kind) for column, kind in columns],
    }
    if many:
        alone = key is None and side == "output"
        own = [column for column, _ in columns if column not in ("quote_reference", "quote_option")]
        key = key if key is not None else own[0]
        table["key"] = key
        kind = dict(columns)[key]
        if side == "input":
            table["null_key"] = 1 if kind == "int" else "1"
        elif alone or index_kind is not None:
            table["index_kind"] = index_kind or "typed"
    return table


_REQUEST_TABLES = [
    _table("policy", ("state", "str"), ("limit", "int"), side="input"),
    _table(
        "items",
        ("row_number", "int"),
        ("item_id", "str"),
        ("value", "float"),
        many=True,
        side="input",
        key="row_number",
    ),
]
_SAMPLE = {
    "policy": {"state": "NY", "limit": 1000},
    "items": [
        {"row_number": 1, "item_id": "A", "value": 10.0},
        {"row_number": 2, "item_id": "B", "value": 30.0},
    ],
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
#: `_priced`'s tables as its project's form defines them, and its sample as typed there: what
#: the pipeline file runs on, on its own, and what deploy reads.
FORM_TABLES = [
    form_table("policy", ("state", "str"), ("limit", "int")),
    form_table("items", ("item_id", "str"), ("value", "float"), many=True),
    form_table("pricing", outputs=[("premium", "float"), ("limit", "float"), ("referral", "str")]),
    form_table("item_prices", outputs=[("item_id", "str"), ("premium", "float")], many=True),
]
FORM_SAMPLE = {
    "policy": [{"state": "NY", "limit": "1,000"}],
    "items": [{"item_id": "A", "value": "10"}, {"item_id": "B", "value": "30"}],
}
#: The quote's identity as the sample is priced under it, which every row of a table the
#: form makes takes in the response.
_STAND_IN = {"quote_reference": "PREVIEW", "quote_option": 1}
#: Each node takes a Workbench Input's frame under its port, ``<table>_input``.
_PRICED = "df = policy_input.with_columns(premium=pl.col('limit') / 10, referral=pl.lit('none'))"
_ITEM_PREMIUMS = "df = items_input.with_columns(premium=pl.col('value') * 2)"


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
                "workbench_input",
                NodeType.WORKBENCH_INPUT,
                {"tables": copy.deepcopy(_REQUEST_TABLES), "sample": copy.deepcopy(_SAMPLE)},
            ),
            _node("priced", NodeType.POLARS, {"code": _PRICED}),
            _node("item_premiums", NodeType.POLARS, {"code": _ITEM_PREMIUMS}),
            _node("workbench_output", NodeType.WORKBENCH_OUTPUT, {"tables": tables}),
        ],
        edges=[
            _edge("workbench_input", "priced", frame="policy_input"),
            _edge("workbench_input", "item_premiums", frame="items_input"),
            _edge("priced", "workbench_output", table="pricing_output"),
            _edge("item_premiums", "workbench_output", table="item_prices_output"),
        ],
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """One temporary project for the generated modules."""
    original = current_project().root
    use_project(tmp_path)
    try:
        yield tmp_path
    finally:
        use_project(original)


def _write(graph: PipelineGraph, project: Path, name: str = "") -> Path:
    """Write *graph*'s generated files and config files as save does, its workbench nodes
    without the tables they are given from the form; return the pipeline."""
    name = name or f"pipeline_{uuid.uuid4().hex}"
    saved = without_form_tables(graph)
    files = {
        **graph_to_code_multi(saved, pipeline_name=name),
        **SavePipelineService._collect_node_configs_recursive(saved),
    }
    for relative, content in files.items():
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return project / f"{name}.py"


def _saved(graph: PipelineGraph, project: Path, name: str = "main") -> PipelineGraph:
    """*graph* written as save writes it and read back as a run reads it, its workbench nodes
    given the tables *graph*'s held, as a run gives them from a form defining them."""
    return with_tables(parse_pipeline_file(_write(graph, project, name)), held_tables(graph))


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
        return read(outputs["workbench_output"])
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

    # One frame per table, by its port, typed as declared, without the frames' other columns
    # (`state`, `value`); a request is answered with their response, by table name.
    assert list(tables) == ["pricing_output", "item_prices_output"]
    assert tables["pricing_output"].schema == pl.Schema(
        {"premium": pl.Float64, "limit": pl.Float64, "referral": pl.String}
    )
    assert tables["pricing_output"].to_dicts() == [_RESPONSE[0]["pricing"]]
    assert tables["item_prices_output"].to_dicts() == _RESPONSE[0]["item_prices"]
    assert _response(_priced()) == _RESPONSE


def _unfilled() -> PipelineGraph:
    """The priced graph, its pricing table declaring `referral`, a String upstream, an
    Integer, and two columns no frame column is named exactly: `discount` and `State`."""
    graph = _priced()
    pricing = graph.node_map["workbench_output"].data.config["tables"][0]
    pricing["columns"] = [
        _column("premium", "float"),
        _column("limit", "float"),
        _column("referral", "int"),
        _column("discount", "float"),
        _column("State", "str"),
    ]
    return graph


def test_a_column_the_frame_does_not_fill_is_nulls(project: Path) -> None:
    # A column of another type, one the frame lacks and one whose name differs only in case
    # are each a column of nulls of the declared type, and the run goes on.
    graph = _unfilled()

    tables = _tables(graph)

    assert tables["pricing_output"].schema == pl.Schema(
        {
            "premium": pl.Float64,
            "limit": pl.Float64,
            "referral": pl.Int64,
            "discount": pl.Float64,
            "State": pl.String,
        }
    )
    assert tables["pricing_output"].to_dicts() == [
        {"premium": 100.0, "limit": 1000.0, "referral": None, "discount": None, "State": None}
    ]
    assert _response(graph)[0]["pricing"] == {"premium": 100.0, "limit": 1000.0}


@pytest.mark.parametrize("rows", [0, 2])
def test_a_table_its_frame_fills_none_of_keeps_the_frames_rows(project: Path, rows: int) -> None:
    # Every column of `item_prices` is nulls, `item_id` a String declared an Integer and
    # `discount` no frame column, yet the table has as many rows as its frame.
    graph = _priced()
    graph.node_map["workbench_output"].data.config["tables"][1]["columns"] = [
        _column("item_id", "int"),
        _column("discount", "float"),
    ]
    if rows == 0:
        graph.node_map["item_premiums"].data.config["code"] = (
            f"{_ITEM_PREMIUMS}\ndf = df.filter(pl.lit(False))"
        )

    tables = _tables(graph)

    assert tables["item_prices_output"].to_dicts() == [{"item_id": None, "discount": None}] * rows


def test_a_column_of_nulls_fills_a_column_of_any_type(project: Path) -> None:
    # A frame column that is null throughout has no dtype to disagree with the declared one.
    graph = _priced()
    graph.node_map["priced"].data.config["code"] = (
        f"{_PRICED}\ndf = df.with_columns(referral=pl.lit(None))"
    )

    (pricing,) = _tables(graph)["pricing_output"].to_dicts()

    assert pricing == {"premium": 100.0, "limit": 1000.0, "referral": None}


def test_a_workbench_output_previews_before_the_workbench_has_a_sample(project: Path) -> None:
    # Each input table is then one row of nulls: each one-row table still has its one row.
    graph = _priced()
    del graph.node_map["workbench_input"].data.config["sample"]

    tables = _tables(graph)

    assert tables["pricing_output"].to_dicts() == [
        {"premium": None, "limit": None, "referral": "none"}
    ]
    assert tables["item_prices_output"].to_dicts() == [{"item_id": None, "premium": None}]


def test_a_table_nothing_is_connected_to_is_nulls(project: Path) -> None:
    # A one-row table is one row of nulls and a many-row table no rows, beside a table that is
    # connected.
    graph = _priced()
    graph.edges = [edge for edge in graph.edges if edge.targetHandle != "item_prices_output"]

    tables = _tables(graph)

    assert tables["pricing_output"].to_dicts() == [_RESPONSE[0]["pricing"]]
    assert tables["item_prices_output"].schema == pl.Schema(
        {"item_id": pl.String, "premium": pl.Float64}
    )
    assert tables["item_prices_output"].height == 0
    assert _response(graph) == [{"pricing": _RESPONSE[0]["pricing"]}]


def test_a_workbench_output_with_nothing_connected_runs(project: Path) -> None:
    graph = _priced()
    graph.edges = [edge for edge in graph.edges if edge.target != "workbench_output"]
    pipeline_file = _write(graph, project, "main")
    workbench_project(project, FORM_TABLES, FORM_SAMPLE)

    tables = _tables(graph)

    assert tables["pricing_output"].to_dicts() == [
        {"premium": None, "limit": None, "referral": None}
    ]
    assert tables["item_prices_output"].height == 0
    assert _response(graph) == [{}]
    # As Preview prices it, every table from one run of a saved pipeline with shared
    # snapshots, and as the pipeline file runs.
    previewed = execute_graph(
        with_tables(parse_pipeline_file(pipeline_file), held_tables(graph)),
        target_node_id="workbench_output",
        port_labels=["pricing_output", "item_prices_output"],
        shared_snapshots=True,
        staging_token=new_staging_token(),
    )["workbench_output"]
    assert previewed.status == "ok", previewed.error
    assert {label: each.preview for label, each in previewed.port_previews.items()} == {
        "pricing_output": [{"premium": None, "limit": None, "referral": None}],
        "item_prices_output": [],
    }
    # On its own, the form's tables lead with the quote's identity, which the response gives.
    pipeline = _import(pipeline_file).pipeline
    assert render_output_document(_collected(pipeline.run(source="live"))) == [
        {"pricing": _STAND_IN}
    ]


def test_the_editor_previews_a_table_at_a_time(project: Path) -> None:
    # As the preview route runs it, on a saved pipeline with shared snapshots, where a node
    # with two inputs is a capture point unless its result is a bundle of frames.
    saved = _saved(_priced(), project)
    assert saved.source_file
    results = execute_graph(
        saved,
        target_node_id="workbench_output",
        port_label="item_prices_output",
        shared_snapshots=True,
        staging_token=new_staging_token(),
    )

    previewed = results["workbench_output"]
    assert previewed.status == "ok", previewed.error
    assert list(previewed.frame_columns) == ["pricing_output", "item_prices_output"]
    assert previewed.preview == _RESPONSE[0]["item_prices"]


def _three_tables() -> PipelineGraph:
    """`_priced` with a third table, a summary of the policy's state and premium."""
    tables = [*_RESPONSE_TABLES, _table("summary", ("state", "str"), ("premium", "float"))]
    graph = _priced(tables)
    summary = "df = policy_input.select('state', premium=pl.col('limit') / 10)"
    graph.nodes.append(_node("summarised", NodeType.POLARS, {"code": summary}))
    graph.edges.append(_edge("workbench_input", "summarised", frame="policy_input"))
    graph.edges.append(_edge("summarised", "workbench_output", table="summary_output"))
    return graph


def test_the_editor_previews_every_table_in_one_run(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preview prices the sample in one run of the pipeline, however many tables the
    Workbench Output fills: each table's answer is what a preview of it alone gives."""
    import haute.executor as executor

    saved = _saved(_three_tables(), project)
    runs: list[str | None] = []
    eager_execute = executor._eager_execute

    def counted(graph: PipelineGraph, target: str | None, *args: Any, **kwargs: Any) -> Any:
        runs.append(target)
        return eager_execute(graph, target, *args, **kwargs)

    monkeypatch.setattr(executor, "_eager_execute", counted)
    labels = ["pricing_output", "item_prices_output", "summary_output"]

    results = execute_graph(
        saved,
        target_node_id="workbench_output",
        port_labels=labels,
        shared_snapshots=True,
        staging_token=new_staging_token(),
    )

    previewed = results["workbench_output"]
    assert previewed.status == "ok", previewed.error
    assert runs == ["workbench_output"]
    assert list(previewed.port_previews) == labels
    assert previewed.port_previews["item_prices_output"].preview == _RESPONSE[0]["item_prices"]
    assert previewed.port_previews["summary_output"].preview == [{"state": "NY", "premium": 100.0}]
    # Each table's own columns, as its frame holds them.
    assert [(c.name, c.dtype) for c in previewed.port_previews["summary_output"].columns] == [
        ("state", "String"),
        ("premium", "Float64"),
    ]
    for label in labels:
        alone = execute_graph(
            saved,
            target_node_id="workbench_output",
            port_label=label,
            shared_snapshots=True,
            staging_token=new_staging_token(),
        )["workbench_output"]
        each = previewed.port_previews[label]
        assert (each.preview, each.row_count, each.preview_truncated) == (
            alone.preview,
            alone.row_count,
            alone.preview_truncated,
        )
        assert [column.name for column in each.columns] == [column.name for column in alone.columns]


def test_a_preview_of_a_table_the_workbench_output_does_not_fill_says_so(project: Path) -> None:
    saved = _saved(_priced(), project)

    previewed = execute_graph(
        saved,
        target_node_id="workbench_output",
        port_labels=["pricing_output", "missing"],
        shared_snapshots=True,
        staging_token=new_staging_token(),
    )["workbench_output"]

    assert previewed.status == "error"
    assert previewed.error.startswith(
        "workbench_output has no table 'missing' to preview; its tables"
    )


def test_the_preview_route_answers_every_table_asked_for_from_one_request(
    project: Path,
) -> None:
    from fastapi.testclient import TestClient

    app = project_app()

    # The graph as the editor holds it, which the route gives the tables the project's form
    # defines: each table leads with the quote's identity, null in the node's own tables.
    saved = parse_pipeline_file(_write(_three_tables(), project, "main"))
    summary = form_table("summary", outputs=[("state", "str"), ("premium", "float")])
    workbench_project(project, [*FORM_TABLES, summary], FORM_SAMPLE)
    client = TestClient(app)
    body = {"graph": saved.model_dump(), "node_id": "workbench_output", "row_limit": 100}

    answer = client.post(
        "/api/pipeline/preview",
        json={**body, "port_labels": ["pricing_output", "summary_output"]},
    )

    assert answer.status_code == 200, answer.text
    previews = answer.json()["port_previews"]
    assert list(previews) == ["pricing_output", "summary_output"]
    assert previews["summary_output"]["preview"] == [
        {"quote_reference": None, "quote_option": None, "state": "NY", "premium": 100.0}
    ]
    assert previews["summary_output"]["row_count"] == 1
    assert previews["summary_output"]["preview_truncated"] is False
    # One frame, or several: never both.
    both = client.post(
        "/api/pipeline/preview",
        json={**body, "port_label": "pricing_output", "port_labels": ["pricing_output"]},
    )
    assert both.status_code == 422


def test_a_workbench_outputs_cell_is_traced_from_the_node_filling_its_table(project: Path) -> None:
    # The table holds that node's rows: the same row, as the clicked values confirm.
    traced = execute_trace(
        _priced(),
        row_index=1,
        target_node_id="workbench_output",
        column="premium",
        row_values=_RESPONSE[0]["item_prices"][1],
        port_label="item_prices_output",
    )

    assert (traced.target_node_id, traced.column, traced.output_value) == (
        "item_premiums",
        "premium",
        60.0,
    )
    assert [step.node_id for step in traced.steps] == ["workbench_input", "item_premiums"]
    # A Workbench Output with one table needs no table named.
    one_table = _priced([_RESPONSE_TABLES[0]])
    one_table.edges = [edge for edge in one_table.edges if edge.source != "item_premiums"]
    traced = execute_trace(one_table, target_node_id="workbench_output", column="premium")
    assert (traced.target_node_id, traced.output_value) == ("priced", 100.0)


def test_a_workbench_outputs_cell_fed_straight_from_the_input_is_traced_from_its_frame(
    project: Path,
) -> None:
    # The table is one frame of the Workbench Input, which emits one frame per table.
    graph = _priced([_table("items", ("item_id", "str"), ("value", "float"), many=True)])
    graph.nodes = [
        node for node in graph.nodes if node.id in ("workbench_input", "workbench_output")
    ]
    graph.edges = [
        _edge("workbench_input", "workbench_output", frame="items_input", table="items_output")
    ]

    traced = execute_trace(
        graph,
        row_index=1,
        target_node_id="workbench_output",
        column="value",
        row_values={"item_id": "B", "value": 30.0},
        port_label="items_output",
    )

    assert (traced.target_node_id, traced.column, traced.output_value) == (
        "workbench_input",
        "value",
        30.0,
    )


def test_a_workbench_outputs_cell_beside_nulls_is_traced_from_its_frame(project: Path) -> None:
    # The clicked row's nulls are not its frame's values: `referral` holds "none" there, so
    # the row is matched by the columns the frame fills.
    graph = _unfilled()
    row = {"premium": 100.0, "limit": 1000.0, "referral": None, "discount": None, "State": None}

    traced = execute_trace(
        graph,
        target_node_id="workbench_output",
        column="premium",
        row_values=row,
        port_label="pricing_output",
    )

    assert (traced.target_node_id, traced.column, traced.output_value) == ("priced", "premium", 100)
    # A column of nulls has no value to trace.
    for unfilled in ("referral", "discount", "State"):
        with pytest.raises(ValueError, match=f"fills '{unfilled}' with nulls, as the frame"):
            execute_trace(
                graph,
                target_node_id="workbench_output",
                column=unfilled,
                port_label="pricing_output",
            )


@pytest.mark.parametrize(
    ("port_label", "column", "message"),
    [
        (
            None,
            "premium",
            "name the table the row is from, one of 'pricing_output', 'item_prices_output'",
        ),
        (
            "quotes_output",
            "premium",
            "with no 'quotes_output' table; its tables are 'pricing_output', 'item_prices_output'",
        ),
        ("pricing_output", "state", "whose 'pricing_output' table has no 'state' column"),
    ],
)
def test_a_workbench_outputs_cell_it_cannot_place_says_why(
    project: Path, port_label: str | None, column: str, message: str
) -> None:
    refusal = f"^Target node 'workbench_output' is a Workbench Output.*{message}"
    with pytest.raises(ValueError, match=refusal):
        execute_trace(
            _priced(), target_node_id="workbench_output", column=column, port_label=port_label
        )


def test_a_workbench_output_table_with_nothing_connected_has_nothing_to_trace(
    project: Path,
) -> None:
    graph = _priced()
    graph.edges = [edge for edge in graph.edges if edge.targetHandle != "pricing_output"]

    with pytest.raises(ValueError, match="nothing connected to its 'pricing_output' table"):
        execute_trace(
            graph, target_node_id="workbench_output", column="premium", port_label="pricing_output"
        )


def test_generated_code_runs_a_workbench_output(project: Path) -> None:
    pipeline_file = _write(_priced(), project, "main")
    workbench_project(project, FORM_TABLES, FORM_SAMPLE)

    code = pipeline_file.read_text(encoding="utf-8")
    assert (
        '@pipeline.workbench_output(config="config/workbench_output/workbench_output.json")' in code
    )
    assert 'pipeline.connect("priced", "workbench_output", target_port="pricing_output")' in code
    parsed = parse_pipeline_file(pipeline_file)
    assert parsed.node_map["workbench_output"].data.nodeType is NodeType.WORKBENCH_OUTPUT
    # Its config file holds none of its tables: the form's are given where it runs.
    assert "tables" not in parsed.node_map["workbench_output"].data.config
    assert sorted(
        (edge.source, edge.targetHandle)
        for edge in parsed.edges
        if edge.target == "workbench_output"
    ) == [("item_premiums", "item_prices_output"), ("priced", "pricing_output")]

    # Run on its own, it reads the form's tables and sample; each row of a table the form
    # makes takes the quote's identity, the stand-in the sample is priced under.
    pipeline = _import(pipeline_file).pipeline
    assert render_output_document(_collected(pipeline.run(source="live"))) == [
        {
            "pricing": {**_STAND_IN, "premium": 100.0, "limit": 1000.0, "referral": "none"},
            # A table of outputs alone numbered by its index in the order it is answered.
            "item_prices": [
                {**_STAND_IN, "row_number": 1, "item_id": "A", "premium": 20.0},
                {**_STAND_IN, "row_number": 2, "item_id": "B", "premium": 60.0},
            ],
        }
    ]
    # Scored with frames by port and no quote_details, the identity is blank and left out.
    scored = pipeline.score(
        {
            "policy_input": pl.DataFrame({"state": ["TX"], "limit": [500]}),
            "items_input": pl.DataFrame({"row_number": [1], "item_id": ["Z"], "value": [7.0]}),
        }
    )
    assert render_output_document(_collected(scored)) == [
        {
            "pricing": {"premium": 50.0, "limit": 500.0, "referral": "none"},
            "item_prices": [{"row_number": 1, "item_id": "Z", "premium": 14.0}],
        }
    ]


def _one_table_quote() -> PipelineGraph:
    """One request table, which a deployed pipeline reads out of the request."""
    request = _table("quote", ("state", "str"), ("limit", "int"), side="input")
    return PipelineGraph(
        nodes=[
            _node("workbench_input", NodeType.WORKBENCH_INPUT, {"tables": [request]}),
            _node(
                "priced", NodeType.POLARS, {"code": _PRICED.replace("policy_input", "quote_input")}
            ),
            _node(
                "workbench_output",
                NodeType.WORKBENCH_OUTPUT,
                {"tables": [_table("pricing", ("premium", "float"), ("state", "str"))]},
            ),
        ],
        edges=[
            _edge("workbench_input", "priced", frame="quote_input"),
            _edge("priced", "workbench_output", table="pricing_output"),
        ],
    )


def test_deploy_answers_with_a_workbench_output(project: Path) -> None:
    graph = _one_table_quote()
    # One quote, as a deployed pipeline's request holds it: each table under its name.
    request = pl.DataFrame([{"quote": {"state": "TX", "limit": 500}}])

    assert find_output_node(graph) == "workbench_output"
    scored = score_graph(
        graph=graph,
        input_df=request,
        input_node_ids=find_deploy_input_nodes(graph),
        output_node_id="workbench_output",
    )
    assert render_output_document(scored) == [{"pricing": {"premium": 50.0, "state": "TX"}}]

    main = _write(graph, project, "main")
    workbench_project(
        project,
        [
            form_table("quote", ("state", "str"), ("limit", "int")),
            form_table("pricing", outputs=[("premium", "float"), ("state", "str")]),
        ],
    )
    resolved = resolve_config(DeployConfig(pipeline_file=main, model_name="m"))
    assert resolved.output_node_id == "workbench_output"
    assert list(resolved.output_schema) == ["pricing"]

    # `[deploy].output_fields` names the response's fields, which a Workbench Output's tables
    # become.
    picked = score_graph(
        graph=graph,
        input_df=request,
        input_node_ids=find_deploy_input_nodes(graph),
        output_node_id="workbench_output",
        output_fields=["pricing"],
    )
    assert render_output_document(picked) == [{"pricing": {"premium": 50.0, "state": "TX"}}]


def test_deploy_answers_with_nulls_for_what_the_pipeline_does_not_fill(project: Path) -> None:
    # `state` is a String upstream declared an Integer, `discount` no frame column, and
    # `layers` a table nothing is connected to: each null, left out of the answer.
    graph = _one_table_quote()
    graph.node_map["workbench_output"].data.config["tables"] = [
        _table("pricing", ("premium", "float"), ("state", "int"), ("discount", "float")),
        _table("layers", ("layer", "int"), many=True),
    ]
    request = pl.DataFrame([{"quote": {"state": "TX", "limit": 500}}])

    scored = score_graph(
        graph=graph,
        input_df=request,
        input_node_ids=find_deploy_input_nodes(graph),
        output_node_id="workbench_output",
    )

    assert render_output_document(scored) == [{"pricing": {"premium": 50.0}}]


_IDENTITY = (("quote_reference", "str"), ("quote_option", "int"))


def _identified() -> PipelineGraph:
    """A quote whose tables lead with its reference and option, as the form makes them: an
    item's premium, its frame carrying a reference of its own, and a total that drops them."""
    details = _table(
        "quote_details",
        ("quote_reference", "str"),
        ("quote_option", "int"),
        ("name", "str"),
        side="input",
    )
    items = _table(
        "items",
        *_IDENTITY,
        ("row_number", "int"),
        ("item_id", "str"),
        ("value", "float"),
        many=True,
        side="input",
        key="row_number",
    )
    sample = {
        "items": [
            {"row_number": 1, "item_id": "A", "value": 10.0},
            {"row_number": 2, "item_id": "B", "value": 30.0},
        ]
    }
    return PipelineGraph(
        nodes=[
            _node(
                "workbench_input",
                NodeType.WORKBENCH_INPUT,
                {"tables": [details, items], "sample": sample},
            ),
            _node(
                "item_premiums",
                NodeType.POLARS,
                {
                    "code": (
                        "df = items_input.with_columns(premium=pl.col('value') * 2, "
                        "quote_reference=pl.lit('X'))"
                    )
                },
            ),
            _node(
                "total",
                NodeType.POLARS,
                {"code": "df = items_input.select(premium=pl.col('value').sum())"},
            ),
            _node(
                "workbench_output",
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
            _edge("workbench_input", "item_premiums", frame="items_input"),
            _edge("workbench_input", "total", frame="items_input"),
            _edge("total", "workbench_output", table="pricing_output"),
            _edge("item_premiums", "workbench_output", table="item_prices_output"),
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
                "items": [
                    {"row_number": 1, "item_id": "A", "value": 10.0},
                    {"row_number": 2, "item_id": "B", "value": 30.0},
                ],
            }
        ]
    )

    scored = score_graph(
        graph=graph,
        input_df=request,
        input_node_ids=find_deploy_input_nodes(graph),
        output_node_id="workbench_output",
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
        tables["item_prices_output"].select("quote_reference", "quote_option").to_dicts()
        == [{"quote_reference": None, "quote_option": None}] * 2
    )


def test_generated_code_answers_under_the_quote_details_frame_it_has(project: Path) -> None:
    main = _write(_identified(), project, "main")
    workbench_project(
        project,
        [
            form_table("items", ("item_id", "str"), ("value", "float"), many=True),
            form_table("pricing", outputs=[("premium", "float")]),
            form_table("item_prices", outputs=[("item_id", "str")], many=True),
        ],
        {"items": [{"item_id": "A", "value": "10"}, {"item_id": "B", "value": "30"}]},
    )
    pipeline = _import(main).pipeline

    # Run, the sample is priced under the stand-in; scored without quote_details, which
    # nothing reads here, the identity is blank and left out of the answer.
    [ran] = render_output_document(_collected(pipeline.run(source="live")))
    assert ran["item_prices"][0] == {
        "quote_reference": "PREVIEW",
        "quote_option": 1,
        "row_number": 1,
        "item_id": "A",
    }
    scored = pipeline.score(
        pl.DataFrame(
            {
                "quote_reference": ["Q-1"],
                "quote_option": [1],
                "row_number": [1],
                "item_id": ["Z"],
                "value": [7.0],
            }
        )
    )
    assert render_output_document(_collected(scored)) == [
        {"pricing": {"premium": 7.0}, "item_prices": [{"row_number": 1, "item_id": "Z"}]}
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
        target_node_id="workbench_output",
        column="item_id",
        row_values=row,
        port_label="item_prices_output",
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
                _identified(),
                target_node_id="workbench_output",
                column=blank,
                port_label="item_prices_output",
            )


def _no_tables(graph: PipelineGraph) -> None:
    graph.node_map["workbench_output"].data.config["tables"] = []
    graph.edges = [edge for edge in graph.edges if edge.target != "workbench_output"]


def _no_columns(graph: PipelineGraph) -> None:
    for table in graph.node_map["workbench_output"].data.config["tables"]:
        # A table of no columns has none to key many rows by.
        table.update(columns=[], rows="one", key=None, null_key=None, index_kind=None)
        table["index_values"] = []
    graph.edges = [edge for edge in graph.edges if edge.target != "workbench_output"]


def _on_no_table(graph: PipelineGraph) -> None:
    graph.edges = [
        edge.model_copy(update={"targetHandle": "nope"})
        if edge.targetHandle == "pricing_output"
        else edge
        for edge in graph.edges
    ]


def _fed(code: str) -> Callable[[PipelineGraph], None]:
    def change(graph: PipelineGraph) -> None:
        graph.node_map["priced"].data.config["code"] = code

    return change


def _fills_none_of_pricing(code: str) -> Callable[[PipelineGraph], None]:
    """`pricing` declaring only columns its frame does not fill, the frame made by *code*."""

    def change(graph: PipelineGraph) -> None:
        graph.node_map["workbench_output"].data.config["tables"][0]["columns"] = [
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
    pipeline_file = _write(_priced(), project, "main")
    workbench_project(project, FORM_TABLES, FORM_SAMPLE)
    sidecar = project / "config" / "workbench_output" / "workbench_output.json"
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
    main = _write(_priced(), project, "main")
    workbench_project(project, FORM_TABLES, FORM_SAMPLE)
    pipeline = _import(main).pipeline

    with pytest.raises(WorkbenchOutputError, match="but the frame connected to it has 2 rows"):
        _collected(
            pipeline.score(
                {
                    "policy_input": pl.DataFrame({"state": ["TX", "CA"], "limit": [500, 700]}),
                    "items_input": pl.DataFrame(
                        {"row_number": [1], "item_id": ["Z"], "value": [7.0]}
                    ),
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
        ports=["pricing_output", "item_prices_output"],
    )

    assert list(tables["pricing_output"].collect_schema()) == ["premium", "limit", "referral"]
    assert list(as_response(tables).collect_schema()) == ["pricing", "item_prices"]
    with pytest.raises(WorkbenchOutputError, match="has 2 rows"):
        execution_collect(tables["pricing_output"], engine="streaming")
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
        ports=["pricing_output", "item_prices_output"],
    )

    pricing_rows = execution_collect(tables["pricing_output"], engine=engine)
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


@pytest.mark.parametrize("engine", ["streaming", "in-memory"])
def test_the_quotes_identity_and_a_tables_one_row_read_the_same_on_either_engine(
    engine: CollectEngine,
) -> None:
    """The response's identity and a one-row table's check are read inside Python scans too."""
    identified = [
        _table("pricing", *_IDENTITY, ("premium", "float")),
        _table("item_prices", *_IDENTITY, ("item_id", "str"), many=True),
    ]

    def filled(pricing: pl.LazyFrame) -> WorkbenchOutputTables:
        return assemble_workbench_output_from_config(
            pricing,
            pl.LazyFrame({"item_id": ["A", "B"]}),
            config={"tables": copy.deepcopy(identified)},
            ports=["pricing_output", "item_prices_output"],
        )

    def quote(**columns: list[Any]) -> dict[str, pl.LazyFrame]:
        """A quote's Workbench Input frames, by port: its quote_details of *columns*."""
        return {"quote_details_input": pl.LazyFrame(columns)}

    tables = filled(pl.LazyFrame({"premium": [1.0]}))
    given = quote(quote_reference=["Q-1"], quote_option=[2], name=["Fleet"])

    response = execution_collect(as_response(tables, given), engine=engine)

    named = {"quote_reference": "Q-1", "quote_option": 2}
    assert response.to_dicts() == [
        {
            "pricing": {**named, "premium": 1.0},
            "item_prices": [{**named, "item_id": "A"}, {**named, "item_id": "B"}],
        }
    ]
    # The identity is read from quote_details' port; a quote_details without the identity
    # gives none, and one of two rows is refused.
    assert quote_identity(given) is not None
    assert quote_identity({"quote_details": given["quote_details_input"]}) is None
    assert quote_identity(quote(name=["Fleet"])) is None
    two = quote(quote_reference=["Q-1", "Q-2"], quote_option=[1, 1])
    with pytest.raises(WorkbenchOutputError, match="quote_details has 2 rows"):
        execution_collect(as_response(tables, two), engine=engine)
    # A one-row table is read with exactly its one row.
    for premiums, held in (([1.0, 2.0], "2 rows"), ([], "no rows")):
        refused = filled(pl.LazyFrame({"premium": premiums}, schema={"premium": pl.Float64}))
        with pytest.raises(WorkbenchOutputError, match=f"connected to it has {held}"):
            execution_collect(refused["pricing_output"], engine=engine)


# -- An input table's outputs on its rows --------------------------------------------------


#: A table of inputs and outputs' output side, keyed by its index renamed item_number, and a
#: table of outputs alone, which takes the rows pricing gives as they come.
_EQUIPMENT_OUTPUT = _table(
    "equipment",
    ("item_number", "int"),
    ("premium", "float"),
    ("loading", "float"),
    many=True,
    key="item_number",
)


#: A table of outputs alone numbered by the Workbench Output, its rows taken as they come.
_LAYERS_OUTPUT = _table(
    "layers",
    ("layer_no", "int"),
    ("layer", "int"),
    ("premium", "float"),
    many=True,
    key="layer_no",
    index_kind="numbered",
)


_KEYED_PORTS = ["equipment_output", "layers_output"]


def _keyed_response(
    equipment: dict[str, list[Any]],
    requested: list[int | None] | None = None,
    *,
    layers: dict[str, list[Any]] | None = None,
) -> pl.LazyFrame:
    """The response to a quote whose equipment rows hold the item numbers *requested*, its
    tables filled from *equipment* and *layers*; a node preview's, with no quote, while
    *requested* is None."""
    schema = {"item_number": pl.Int64, "premium": pl.Float64, "loading": pl.Float64}
    tables = assemble_workbench_output_from_config(
        pl.LazyFrame(equipment, schema={name: schema[name] for name in equipment}),
        pl.LazyFrame(
            layers or {"layer": [], "premium": []},
            schema={"layer": pl.Int64, "premium": pl.Float64},
        ),
        config={"tables": copy.deepcopy([_EQUIPMENT_OUTPUT, _LAYERS_OUTPUT])},
        ports=_KEYED_PORTS,
    )
    if requested is None:
        return as_response(tables)
    quote = {
        "equipment_input": pl.LazyFrame(
            {"item_number": requested}, schema={"item_number": pl.Int64}
        )
    }
    return as_response(tables, quote)


@pytest.mark.parametrize("engine", ["streaming", "in-memory"])
def test_an_input_tables_outputs_are_answered_under_its_name_with_their_key(
    engine: CollectEngine,
) -> None:
    # Answered in another order than the quote's rows, and none for item 2: each row is
    # answered with its key, which puts it on its row, and item 2 is answered nothing.
    response = _keyed_response(
        {"item_number": [3, 1], "premium": [30.0, 10.0], "loading": [1.5, 1.0]}, [1, 2, 3]
    )

    assert render_output_document(execution_collect(response, engine=engine)) == [
        {
            "equipment": [
                {"item_number": 3, "premium": 30.0, "loading": 1.5},
                {"item_number": 1, "premium": 10.0, "loading": 1.0},
            ]
        }
    ]


@pytest.mark.parametrize(
    ("numbers", "says"),
    [
        pytest.param(
            [1, 7],
            "equipment_output does not fit equipment's rows: item_number 7 is on no row priced.",
            id="a key on no row",
        ),
        pytest.param(
            [1, 2, 1],
            "equipment_output does not fit equipment's rows: item_number 1 is given twice.",
            id="a key twice",
        ),
        pytest.param(
            [None, 1, None],
            "equipment_output does not fit equipment's rows: 2 rows have no item_number.",
            id="a key missing",
        ),
        pytest.param(
            [None, 4, 4, 9],
            "equipment_output does not fit equipment's rows: 1 row has no item_number; "
            "item_number 4 is given twice; item_number 9 is on no row priced.",
            id="all three",
        ),
    ],
)
@pytest.mark.parametrize("engine", ["streaming", "in-memory"])
def test_an_answer_misfitting_its_quotes_rows_fails_the_pricing(
    numbers: list[int | None], says: str, engine: CollectEngine
) -> None:
    response = _keyed_response(
        {"item_number": numbers, "premium": [1.0] * len(numbers)}, [1, 2, 3, 4]
    )

    with pytest.raises(WorkbenchOutputError, match=re.escape(says)):
        execution_collect(response, engine=engine)


@pytest.mark.parametrize("engine", ["streaming", "in-memory"])
def test_an_answer_row_holding_no_output_value_is_left_out_as_a_row_answered_nothing(
    engine: CollectEngine,
) -> None:
    nan, inf = float("nan"), float("inf")
    response = _keyed_response(
        {
            "item_number": [1, 2, 3, 4, 5, 6],
            # All null, all NaN, all infinite either way: no value the store keeps. The last
            # holds one finite output beside a NaN, and stays.
            "premium": [None, nan, inf, -inf, inf, 5.0],
            "loading": [None, nan, inf, -inf, None, nan],
        },
        [1, 2, 3, 4, 5, 6],
        # A table of outputs alone takes its rows as they come, holding nothing included.
        layers={"layer": [1, 1, None], "premium": [None, nan, 2.0]},
    )

    (answered,) = execution_collect(response, engine=engine).to_dicts()

    (kept,) = answered["equipment"]
    assert (kept["item_number"], kept["premium"]) == (6, 5.0)
    assert math.isnan(kept["loading"])
    assert [(row["layer_no"], row["layer"]) for row in answered["layers"]] == [
        (1, 1),
        (2, 1),
        (3, None),
    ]
    assert answered["layers"][0]["premium"] is None
    assert math.isnan(answered["layers"][1]["premium"])
    assert answered["layers"][2]["premium"] == 2.0


def test_a_numbered_table_of_outputs_alone_is_answered_as_it_comes_whatever_its_rows() -> None:
    # Two rows of one layer: a numbered index is Haute's, so nothing holds the rows to it.
    response = _keyed_response(
        {"item_number": [], "premium": []},
        [1],
        layers={"layer": [2, 2], "premium": [5.0, 6.0]},
    )

    assert render_output_document(_collected(response)) == [
        {
            "layers": [
                {"layer_no": 1, "layer": 2, "premium": 5.0},
                {"layer_no": 2, "layer": 2, "premium": 6.0},
            ]
        }
    ]


@pytest.mark.parametrize("engine", ["streaming", "in-memory"])
def test_a_node_preview_answers_a_misfit_without_refusing(engine: CollectEngine) -> None:
    # A node preview has no quote to hold the rows to: they are answered as they are.
    response = _keyed_response({"item_number": [None, 1, 1], "premium": [1.0, 2.0, None]})

    assert render_output_document(execution_collect(response, engine=engine)) == [
        {
            "equipment": [
                {"premium": 1.0},
                {"item_number": 1, "premium": 2.0},
                {"item_number": 1},
            ]
        }
    ]


#: A table of outputs alone whose index is renamed layer_no: the Workbench Output numbers it.
_NUMBERED_LAYERS = _table(
    "layers",
    ("layer_no", "int"),
    ("layer", "int"),
    ("premium", "float"),
    many=True,
    key="layer_no",
    index_kind="numbered",
)


@pytest.mark.parametrize("engine", ["streaming", "in-memory"])
@pytest.mark.parametrize(
    "frame",
    [
        pytest.param({"layer": [3, 1, 2], "premium": [30.0, 10.0, 20.0]}, id="no index held"),
        pytest.param(
            {"layer_no": [9, 9, None], "layer": [3, 1, 2], "premium": [30.0, 10.0, 20.0]},
            id="a misleading one held",
        ),
        pytest.param(
            {"layer_no": ["x", "y", "z"], "layer": [3, 1, 2], "premium": [30.0, 10.0, 20.0]},
            id="one of another type held",
        ),
    ],
)
@pytest.mark.parametrize("quoted", [False, True], ids=["a node preview", "a priced quote"])
def test_a_table_of_outputs_alone_numbers_its_index_in_the_order_it_is_answered(
    frame: dict[str, list[Any]], quoted: bool, engine: CollectEngine
) -> None:
    tables = assemble_workbench_output_from_config(
        pl.LazyFrame(frame),
        config={"tables": [copy.deepcopy(_NUMBERED_LAYERS)]},
        ports=["layers_output"],
    )
    quote = {"layers_input": pl.LazyFrame({"layer": [1]})} if quoted else None

    (answered,) = execution_collect(as_response(tables, quote), engine=engine).to_dicts()

    # Numbered 1, 2, 3 as the rows come, whatever the frame held under its name.
    assert [(row["layer_no"], row["layer"]) for row in answered["layers"]] == [
        (1, 3),
        (2, 1),
        (3, 2),
    ]
    assert (tables.tables[0].key, tables.tables[0].index_kind) == ("layer_no", "numbered")


#: The project's equipment: a table of inputs and outputs keyed by its index, renamed
#: item_number, each item's value typed and its premium priced.
_EQUIPMENT_FORM = [
    form_table(
        "equipment",
        ("value", "float"),
        outputs=[("premium", "float")],
        many=True,
        index="item_number",
    ),
]


def _equipment_pipeline(code: str) -> PipelineGraph:
    """The equipment priced by *code*, given ``equipment_input`` and filling
    ``equipment_output``."""
    return PipelineGraph(
        nodes=[
            _node("workbench_input", NodeType.WORKBENCH_INPUT, {}),
            _node("premiums", NodeType.POLARS, {"code": code}),
            _node("workbench_output", NodeType.WORKBENCH_OUTPUT, {}),
        ],
        edges=[
            _edge("workbench_input", "premiums", frame="equipment_input"),
            _edge("premiums", "workbench_output", table="equipment_output"),
        ],
    )


def _equipment_project(project: Path, code: str) -> Any:
    """The equipment pipeline written as save writes it, beside its form; its module's
    pipeline, as the pipeline file runs it."""
    main = _write(_equipment_pipeline(code), project, "main")
    workbench_project(project, _EQUIPMENT_FORM)
    return _import(main).pipeline


@pytest.mark.parametrize(
    ("numbers", "says"),
    [
        pytest.param(
            [1, 1],
            "equipment's rows 1 and 2 hold item_number 1; each row's item_number is its own",
            id="two rows of one key",
        ),
        pytest.param([1, None], "equipment's row 2 holds no item_number", id="a row of none"),
    ],
)
def test_score_refuses_frames_whose_keyed_rows_miss_or_repeat_their_key(
    project: Path, numbers: list[int | None], says: str
) -> None:
    # The pipeline answers one row, item 1, whatever it is given: an answer of unique keys
    # each on a row given. The frames are refused before it runs all the same.
    pipeline = _equipment_project(
        project,
        "df = equipment_input.head(1).with_columns(premium=pl.col('value') * 2)",
    )
    given = pl.DataFrame(
        {"item_number": numbers, "value": [10.0, 20.0]},
        schema={"item_number": pl.Int64, "value": pl.Float64},
    )

    with pytest.raises(WorkbenchInputError, match=re.escape(says)):
        pipeline.score({"equipment_input": given})
    # A bare frame is the one port connected's, held alike.
    with pytest.raises(WorkbenchInputError, match=re.escape(says)):
        pipeline.score(given)
    # Each row its own key, the same pipeline prices.
    fitting = given.with_columns(item_number=pl.Series([1, 2]))
    assert render_output_document(_collected(pipeline.score(fitting))) == [
        {"equipment": [{"item_number": 1, "premium": 20.0}]}
    ]


def test_score_refuses_the_frames_before_anything_downstream_runs(project: Path) -> None:
    # The pricing would fail on any frame it ran on.
    pipeline = _equipment_project(project, "df = no_such_frame")
    given = pl.DataFrame({"item_number": [2, 2], "value": [10.0, 20.0]})

    with pytest.raises(WorkbenchInputError, match="hold item_number 2"):
        pipeline.score({"equipment_input": given})


def test_a_pipeline_dropping_the_key_column_fails_every_priced_quote(project: Path) -> None:
    dropping = "df = equipment_input.select(premium=pl.col('value') * 2)"
    says = "equipment_output does not fit equipment's rows: 2 rows have no item_number."
    pipeline = _equipment_project(project, dropping)
    given = pl.DataFrame({"item_number": [1, 2], "value": [10.0, 20.0]})

    with pytest.raises(WorkbenchOutputError, match=re.escape(says)):
        _collected(pipeline.score({"equipment_input": given}))
    # As deploy's scorer answers a request, the graph given the form's tables.
    graph = with_tables(
        _equipment_pipeline(dropping),
        workbench_tables(tables_form(_EQUIPMENT_FORM)),
    )
    request = pl.DataFrame(
        [
            {
                "quote_details": {"quote_reference": "Q-1", "quote_option": 1},
                "equipment": [{"item_number": 1, "value": 10.0}, {"item_number": 2, "value": 20.0}],
            }
        ]
    )
    with pytest.raises(WorkbenchOutputError, match=re.escape(says)):
        score_graph(
            graph=graph,
            input_df=request,
            input_node_ids=find_deploy_input_nodes(graph),
            output_node_id="workbench_output",
        )


@pytest.mark.parametrize(
    ("form", "carried"),
    [
        pytest.param(_EQUIPMENT_FORM, "item_number", id="an index renamed item_number"),
        pytest.param(
            [
                form_table(
                    "equipment",
                    {"name": "layer", "type": "int", "index": "typed"},
                    ("value", "float"),
                    outputs=[("premium", "float")],
                    many=True,
                    index=None,
                )
            ],
            "layer",
            id="a typed Integer index",
        ),
    ],
)
def test_with_no_sample_the_null_quote_prices_a_pipeline_carrying_the_key_through(
    project: Path, form: list[dict[str, Any]], carried: str
) -> None:
    code = f"df = equipment_input.select('{carried}', premium=pl.col('value') * 2)"
    main = _write(_equipment_pipeline(code), project, "main")
    spec = workbench_project(project, form)

    # Run with no sample, as Preview prices it: the null quote's one row holds its index, 1
    # for a numbered or typed Integer one, and the answer carrying it through fits; its premium is
    # null, so the row holds no output.
    assert render_output_document(_collected(_import(main).pipeline.run(source="live"))) == [{}]
    # Deploy's dry run reads the null quote too, and fits.
    graph = with_tables(_equipment_pipeline(code), workbench_tables(spec))
    schema = infer_output_schema(
        graph, "workbench_output", ["workbench_input"], project_root=project
    )
    assert list(schema) == ["equipment"]


@pytest.mark.meta
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

    with pytest.raises(RequestRefusedError) as refused:
        SavePipelineService(project).validate_graph(graph, source_file="main.py")
    assert refused.value.status_code == 400
    assert refused.value.detail == ONE_RESPONSE_NODE

    main = _write(graph, project, "main")
    workbench_project(project, FORM_TABLES, FORM_SAMPLE)
    with pytest.raises(ValueError, match=re.escape(ONE_RESPONSE_NODE)):
        resolve_config(DeployConfig(pipeline_file=main, model_name="m"))
    with pytest.raises(ExecutionError, match="multiple response nodes"):
        _import(main).pipeline.run(source="live")


def _products(kind: str, values: list[str] | None = None) -> dict[str, Any]:
    """A table of outputs alone keyed by the product, its index the pipeline's."""
    table = _table(
        "products",
        ("product", "str"),
        ("premium", "float"),
        many=True,
        key="product",
        index_kind=kind,
    )
    if values is not None:
        table["index_values"] = values
    return table


@pytest.mark.parametrize("engine", ["streaming", "in-memory"])
@pytest.mark.parametrize(
    ("table", "frame", "says"),
    [
        pytest.param(
            _products("typed"),
            {"product": ["Fire", "Flood"], "premium": [1.0, 2.0]},
            None,
            id="typed, each its own",
        ),
        pytest.param(
            _products("typed"),
            {
                "product": ["Fire", None, "Fire", "x" * 300, "", "   "],
                "premium": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            },
            "products_output does not fit products's rows: 3 rows have no product; product "
            f'"Fire" is given twice; product "{"x" * 40}" is longer than 255 characters.',
            id="typed, missing, twice and too long",
        ),
        pytest.param(
            _products("values", ["Fire", "Flood", "Hail"]),
            {"product": ["Hail", "Fire"], "premium": [1.0, 2.0]},
            None,
            id="of values, one not answered",
        ),
        pytest.param(
            _products("values", ["Fire", "Flood"]),
            {"product": ["Fire", "Fire", "Hail"], "premium": [1.0, 2.0, 3.0]},
            'products_output does not fit products\'s rows: product "Fire" is given twice; '
            'product "Hail" is none of its values.',
            id="of values, twice and none of them",
        ),
    ],
)
def test_a_table_of_outputs_alone_takes_its_index_from_the_pipeline_by_its_kinds_rules(
    table: dict[str, Any], frame: dict[str, list[Any]], says: str | None, engine: CollectEngine
) -> None:
    tables = assemble_workbench_output_from_config(
        pl.LazyFrame(frame, schema={"product": pl.String, "premium": pl.Float64}),
        config={"tables": [table]},
        ports=["products_output"],
    )
    # A priced quote holds the rows to the index's rules; a node preview takes them as they
    # are, checking nothing.
    quote = {"policy_input": pl.LazyFrame({"x": [1]})}
    preview = execution_collect(as_response(tables), engine=engine).to_dicts()
    assert [row["product"] for row in preview[0]["products"]] == frame["product"]
    if says is None:
        (answered,) = execution_collect(as_response(tables, quote), engine=engine).to_dicts()
        assert [row["product"] for row in answered["products"]] == frame["product"]
    else:
        with pytest.raises(WorkbenchOutputError, match=re.escape(says)):
            execution_collect(as_response(tables, quote), engine=engine)


@pytest.mark.parametrize("engine", ["streaming", "in-memory"])
def test_a_numbered_text_index_of_outputs_alone_is_numbered_as_text(engine: CollectEngine) -> None:
    table = _table(
        "layers",
        ("layer_no", "str"),
        ("premium", "float"),
        many=True,
        key="layer_no",
        index_kind="numbered",
    )
    tables = assemble_workbench_output_from_config(
        pl.LazyFrame({"layer_no": [7, 7], "premium": [1.0, 2.0]}),
        config={"tables": [table]},
        ports=["layers_output"],
    )

    (answered,) = execution_collect(as_response(tables), engine=engine).to_dicts()

    assert [row["layer_no"] for row in answered["layers"]] == ["1", "2"]
