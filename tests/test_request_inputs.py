"""Request inputs: the Quote Input and the Workbench Input are the pipeline's request input alike.

A Quote Input reads a JSON request through its tables' paths; a Workbench Input's tables
come from the project's workbench (specs/workbench) and read a quote's tables as
dataframes. Every context treats either as the request input exactly alike: code that
means "the request input" asks ``REQUEST_INPUT_NODE_TYPES`` rather than naming one type,
which the checker here enforces, and a pipeline holds at most one request input of
either type.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import re
import sys
import uuid
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

import polars as pl
import pytest
from fastapi import HTTPException
from polars.testing import assert_frame_equal
from pydantic import ValidationError

from haute._api_input_schema import ApiInputSchemaError, validate_v2_schema
from haute._builders import _build_node_fn
from haute._data_points import DataPoint, DataPointResolver, point_kind
from haute._editor_identities import (
    recoverable_api_input_source_handles,
    recoverable_request_input_source_handles,
)
from haute._execution_admission import create_admitted_execution_context
from haute._graph_utils import REQUEST_INPUT_KINDS, is_frame_label
from haute._input_preparation import snapshot_backed_inputs
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._sandbox import _get_project_root, set_project_root
from haute._trace_enrichment import detect_row_lineage_type
from haute._types import (
    REQUEST_INPUT_NODE_TYPES,
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
)
from haute._workbench_input import (
    WorkbenchInputError,
    read_quote,
    workbench_table_frames,
    workbench_table_labels,
)
from haute._workbench_tables import WorkbenchTablesError, parse_workbench_tables
from haute.codegen import graph_to_code_multi
from haute.deploy._config import DeployConfig, resolve_config
from haute.deploy._pruner import find_deploy_input_nodes
from haute.deploy._schema import _read_sample_row, infer_input_schema
from haute.deploy._scorer import score_graph
from haute.deploy._validators import score_test_quotes
from haute.execution import ExecutionProfile, execute_lazy_graph
from haute.parser import parse_pipeline_file
from haute.routes._save_pipeline import SavePipelineService
from haute.routes.node_data import node_data_service
from haute.schemas import EditorIdentityRequestNode
from tests._source_files import source_files
from tests.conftest import build_test_api_input_snapshots, make_output_config

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "haute"
NODE_TYPES_TS = ROOT / "frontend" / "src" / "utils" / "nodeTypes.ts"
ONE_REQUEST_INPUT = (
    "Only one Quote Input or Workbench Input node is allowed per pipeline (found 2)."
)
DECORATORS = {NodeType.API_INPUT: "api_input", NodeType.WORKBENCH_INPUT: "workbench_input"}
FOLDERS = {NodeType.API_INPUT: "quote_input", NodeType.WORKBENCH_INPUT: "workbench_input"}
REQUEST_INPUTS = pytest.mark.parametrize(
    "node_type", [NodeType.API_INPUT, NodeType.WORKBENCH_INPUT], ids=lambda t: t.value
)


# ---------------------------------------------------------------------------
# The checker: a check that means "the request input" names neither type alone
# ---------------------------------------------------------------------------


class Report(NamedTuple):
    path: str
    line: int


def _is_member(node: ast.AST, member: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == member
        and isinstance(node.value, ast.Name)
        and node.value.id == "NodeType"
    )


def _is_api_input_value(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "apiInput"


def _assigned(body: list[ast.stmt], name: str) -> ast.expr | None:
    for statement in body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in statement.targets
        ):
            return statement.value
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
        ):
            return statement.value
    return None


def _named(body: list[ast.stmt], kind: type, name: str) -> Any:
    return next((s for s in body if isinstance(s, kind) and s.name == name), None)


def _everything(node: ast.AST | None) -> set[int]:
    return {id(child) for child in ast.walk(node)} if node is not None else set()


def _allowed(tree: ast.Module, path: str) -> set[int]:
    """The references *path* may hold, each matched by its syntax (specs/workbench)."""
    allowed: set[int] = set()
    if path == "_types.py":
        node_type = _named(tree.body, ast.ClassDef, "NodeType")
        allowed |= {id(_assigned(node_type.body, "API_INPUT"))} if node_type else set()
        decorators = _assigned(tree.body, "DECORATOR_TO_NODE_TYPE")
        if isinstance(decorators, ast.Dict):
            allowed |= {
                id(value)
                for key, value in zip(decorators.keys, decorators.values, strict=True)
                if isinstance(key, ast.Constant) and key.value == "api_input"
            }
        allowed |= _everything(_assigned(tree.body, "REQUEST_INPUT_NODE_TYPES"))
    if path == "_graph_utils.py":
        allowed |= _everything(_assigned(tree.body, "REQUEST_INPUT_KINDS"))
    if path == "execution.py":
        # The file each node type reads: a Workbench Input reads none.
        for name in (
            "_SOURCE_PATH_CONFIG_BY_NODE_TYPE",
            "_LOCAL_RUNTIME_INPUT_PATH_FIELDS_BY_NODE_TYPE",
        ):
            mapping = _assigned(tree.body, name)
            if isinstance(mapping, ast.Dict):
                allowed |= {
                    id(key)
                    for key in mapping.keys
                    if key is not None and _is_member(key, "API_INPUT")
                }
    if path == "pipeline.py":
        registry = _named(tree.body, ast.ClassDef, "NodeRegistry")
        method = _named(registry.body, ast.FunctionDef, "api_input") if registry else None
        for call in ast.walk(method) if method else ():
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "_register_node":
                allowed |= {id(kw.value) for kw in call.keywords if kw.arg == "_node_type"}
    if path == "schemas.py":
        request = _named(tree.body, ast.ClassDef, "InputCacheSourceRequest")
        for statement in request.body if request else ():
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.target.id == "node_type"
                and isinstance(statement.annotation, ast.Subscript)
                and getattr(statement.annotation.value, "id", "") == "Literal"
            ):
                allowed |= _everything(statement.annotation)
    if path == "routes/input_cache.py":
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Attribute)
                and node.left.attr == "node_type"
                and getattr(node.left.value, "id", "") == "body"
            ):
                allowed |= {id(c) for c in node.comparators if _is_api_input_value(c)}
            if isinstance(node, ast.Dict):
                allowed |= {
                    id(value)
                    for key, value in zip(node.keys, node.values, strict=True)
                    if isinstance(key, ast.Constant) and key.value == "node_type"
                }
    for node in ast.walk(tree):
        # A mapping keyed by both request inputs; in the Quote Input's value, its
        # type only as the first argument of a call that is the whole value.
        if isinstance(node, ast.Dict) and any(
            key is not None and _is_member(key, "WORKBENCH_INPUT") for key in node.keys
        ):
            for key, value in zip(node.keys, node.values, strict=True):
                if key is not None and _is_member(key, "API_INPUT"):
                    allowed.add(id(key))
                    if isinstance(value, ast.Call) and value.args:
                        allowed |= (
                            {id(value.args[0])} if _is_member(value.args[0], "API_INPUT") else set()
                        )
        # A registration decorator twinned with the same one for the Workbench Input.
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            calls = [d for d in node.decorator_list if isinstance(d, ast.Call) and d.args]
            twins = {ast.dump(d.func) for d in calls if _is_member(d.args[0], "WORKBENCH_INPUT")}
            allowed |= {
                id(d.args[0])
                for d in calls
                if _is_member(d.args[0], "API_INPUT") and ast.dump(d.func) in twins
            }
    return allowed


def request_input_reports(source: str, path: str) -> list[Report]:
    """Each Quote Input reference in *source* outside the forms *path* may hold."""
    tree = ast.parse(source)
    allowed = _allowed(tree, path)
    return sorted(
        Report(path, getattr(node, "lineno", 0))
        for node in ast.walk(tree)
        if (_is_member(node, "API_INPUT") or _is_api_input_value(node)) and id(node) not in allowed
    )


_ACCEPTED = [
    ("execution.py", "M = {NodeType.API_INPUT: 'path', NodeType.WORKBENCH_INPUT: 'path'}\n"),
    (
        "projection.py",
        "M = {\n"
        "    NodeType.API_INPUT: _coverage(NodeType.API_INPUT, R),\n"
        "    NodeType.WORKBENCH_INPUT: _coverage(NodeType.WORKBENCH_INPUT, R),\n"
        "}\n",
    ),
    (
        "_builders.py",
        "@_register(NodeType.WORKBENCH_INPUT, cost='source')\n"
        "@_register(NodeType.API_INPUT, cost='source')\n"
        "def build(ctx): ...\n",
    ),
    (
        "_types.py",
        "class NodeType(StrEnum):\n    API_INPUT = 'apiInput'\n\n"
        "DECORATOR_TO_NODE_TYPE = {'api_input': NodeType.API_INPUT}\n"
        "REQUEST_INPUT_NODE_TYPES = frozenset({NodeType.API_INPUT, NodeType.WORKBENCH_INPUT})\n",
    ),
    ("_graph_utils.py", "REQUEST_INPUT_KINDS = frozenset({'apiInput', 'workbenchInput'})\n"),
    (
        "pipeline.py",
        "class NodeRegistry:\n"
        "    def api_input(self, fn=None, **config):\n"
        "        return self._register_node(fn, _node_type=NodeType.API_INPUT, **config)\n",
    ),
    (
        "schemas.py",
        "class InputCacheSourceRequest(BaseModel):\n"
        "    node_type: Literal['dataInput', 'apiInput'] = 'dataInput'\n",
    ),
    (
        "routes/input_cache.py",
        "def build(body):\n"
        "    if body.node_type == 'apiInput':\n"
        "        return {'node_type': 'apiInput'}\n",
    ),
]

_REPORTED = [
    ("trace.py", "if node.data.nodeType == NodeType.API_INPUT:\n    pass\n", [1]),
    ("deploy/_pruner.py", "found = node_type in {NodeType.API_INPUT, NodeType.DATA_INPUT}\n", [1]),
    ("execution.py", "M = {NodeType.API_INPUT: 'path'}\n", [1]),
    (
        "projection.py",
        "M = {\n"
        "    NodeType.API_INPUT: (lambda n: n == NodeType.API_INPUT),\n"
        "    NodeType.WORKBENCH_INPUT: None,\n"
        "}\n",
        [2],
    ),
    (
        "routes/input_cache.py",
        "def kind(node):\n    return node.data.nodeType == 'apiInput'\n",
        [2],
    ),
    ("_builders.py", "@_register(NodeType.API_INPUT, cost='source')\ndef build(ctx): ...\n", [1]),
    (
        "pipeline.py",
        "class Node:\n    def seeded(self):\n"
        "        return self.config.get('_node_type') == NodeType.API_INPUT\n",
        [3],
    ),
    ("codegen.py", "KIND = 'apiInput'\n", [1]),
]


@pytest.mark.parametrize(("path", "source"), _ACCEPTED, ids=[path for path, _ in _ACCEPTED])
def test_the_checker_accepts_the_specified_forms(path: str, source: str) -> None:
    assert request_input_reports(source, path) == []


@pytest.mark.parametrize(
    ("path", "source", "lines"), _REPORTED, ids=[f"{p}:{n[0]}" for p, _, n in _REPORTED]
)
def test_the_checker_reports_every_other_form(path: str, source: str, lines: list[int]) -> None:
    assert request_input_reports(source, path) == [Report(path, line) for line in lines]


def test_request_input_checks_use_the_shared_set() -> None:
    reports = [
        report
        for module in source_files(SRC)
        for report in request_input_reports(
            module.read_text(encoding="utf-8"), module.relative_to(SRC).as_posix()
        )
    ]
    assert reports == [], (
        "These name the Quote Input alone. Where the code means the request input, "
        "test membership of REQUEST_INPUT_NODE_TYPES (specs/workbench):\n"
        + "\n".join(f"src/haute/{r.path}:{r.line}" for r in reports)
    )


def _editor_set(name: str) -> set[str]:
    """A node-type set the editor declares, read as tests/test_assistant_catalog.py reads one."""
    text = NODE_TYPES_TS.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = new Set<\w+>\(\[(.*?)\]\)", text, re.S)
    assert match, f"nodeTypes.ts declares no {name}"
    return {NodeType[member].value for member in re.findall(r"NODE_TYPES\.(\w+)", match.group(1))}


def test_request_input_kinds_match_the_node_types() -> None:
    values = {node_type.value for node_type in REQUEST_INPUT_NODE_TYPES}

    assert values == {"apiInput", "workbenchInput"}
    assert REQUEST_INPUT_KINDS == values
    assert _editor_set("REQUEST_INPUT_TYPES") == values


@pytest.mark.parametrize(
    ("label", "allowed"),
    [
        ("policy", True),
        ("_x1", True),
        ("class", False),
        ("1st", False),
        ("naïve", False),
        ("my table", False),
        ("", False),
    ],
)
def test_a_frame_label_follows_one_rule_everywhere(label: str, allowed: bool) -> None:
    """A Quote Input's table labels, a Workbench Input's table names and the editor's
    source handles are held to `is_frame_label` alike."""
    assert is_frame_label(label) is allowed
    quote_table = {
        "path": "$[:]",
        "label": label,
        "emit": True,
        "columns": [{"name": "x", "path": "$[:].x", "type": "int", "selected": True}],
    }
    workbench_table = {"name": label, "rows": "one", "columns": [{"name": "x", "type": "int"}]}

    refused: list[str] = []
    try:
        validate_v2_schema({"tables": [quote_table]})
    except ApiInputSchemaError:
        refused.append("quote input")
    try:
        parse_workbench_tables([workbench_table], owner="Workbench Input")
    except WorkbenchTablesError:
        refused.append("workbench input")
    try:
        EditorIdentityRequestNode(
            node_id="n", label="n", node_type=NodeType.WORKBENCH_INPUT, source_handles=[label]
        )
    except ValidationError:
        refused.append("identity")

    assert refused == ([] if allowed else ["quote input", "workbench input", "identity"])
    # Recovery derives a request input's handles by its type, held to the same rule.
    expected = (label,) if allowed else ()
    assert recoverable_api_input_source_handles({"tables": [quote_table]}) == expected
    assert (
        recoverable_request_input_source_handles(NodeType.API_INPUT, {"tables": [quote_table]})
        == expected
    )
    assert (
        recoverable_request_input_source_handles(
            NodeType.WORKBENCH_INPUT, {"tables": [workbench_table]}
        )
        == expected
    )


# ---------------------------------------------------------------------------
# The request inputs, side by side
# ---------------------------------------------------------------------------


def _column(name: str, kind: str) -> dict[str, Any]:
    return {"name": name, "type": kind}


# A one-row and a many-row table, as the workbench supplies them.
_TABLES: list[dict[str, Any]] = [
    {
        "name": "policy",
        "rows": "one",
        "columns": [_column("state", "str"), _column("limit", "int")],
    },
    {
        "name": "items",
        "rows": "many",
        "columns": [_column("item_id", "str"), _column("value", "float")],
    },
]


def _quote_column(table: str, name: str, kind: str, *, many: bool = False) -> dict[str, Any]:
    """A Quote Input's column at the level the workbench's shape puts it."""
    level = f"$[:].{table}[:]" if many else f"$[:].{table}"
    return {
        "name": name,
        "path": f"{level}.{name}",
        "type": kind,
        "status": "Confirmed",
        "selected": True,
        "levels": None,
    }


# The same two tables as a Quote Input declares them: JSON paths into each quote.
_QUOTE_TABLES: list[dict[str, Any]] = [
    {
        "path": "$[:]",
        "label": "policy",
        "emit": True,
        "row_id_column": None,
        "columns": [
            _quote_column("policy", "state", "str"),
            _quote_column("policy", "limit", "int"),
        ],
    },
    {
        "path": "$[:].items[:]",
        "label": "items",
        "emit": True,
        "row_id_column": "item_id",
        "columns": [
            _quote_column("items", "item_id", "str", many=True),
            _quote_column("items", "value", "float", many=True),
        ],
    },
]
_SAMPLE = [
    {
        "policy": {"state": "NY", "limit": 1000},
        "items": [{"item_id": "A", "value": 10.0}, {"item_id": "B", "value": 30.0}],
    }
]
# Every input in one column, stacked: no join, so no materialisation to admit.
# `score` hands seeded inputs over as data frames and the Constant's as lazy.
_LISTED = (
    "df = pl.concat([policy.lazy().select(pl.col('state').alias('name')), "
    "items.lazy().select(pl.col('item_id').alias('name')), "
    "rates.lazy().select(pl.col('loading').cast(pl.String).alias('name'))])"
)


def _node(nid: str, node_type: NodeType, config: dict[str, Any]) -> GraphNode:
    return GraphNode(id=nid, data=NodeData(label=nid, nodeType=node_type, config=config))


def _edge(source: str, target: str, port: str | None = None) -> GraphEdge:
    return GraphEdge(
        id=f"e_{source}_{target}_{port or 'frame'}",
        source=source,
        target=target,
        sourceHandle=port,
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """One temporary project for the generated modules and the input snapshots."""
    original = _get_project_root()
    monkeypatch.chdir(tmp_path)
    set_project_root(tmp_path)
    try:
        yield tmp_path
    finally:
        set_project_root(original)


def _pricing(project: Path, node_type: NodeType) -> PipelineGraph:
    """A request input's two tables listed beside a Constant's own value.

    A Quote Input reads its sample file; a Workbench Input reads none.
    """
    workbench = node_type is NodeType.WORKBENCH_INPUT
    config: dict[str, Any] = {"tables": copy.deepcopy(_TABLES if workbench else _QUOTE_TABLES)}
    if not workbench:
        sample = project / "quotes.json"
        sample.write_text(json.dumps(_SAMPLE), encoding="utf-8")
        config["path"] = str(sample)
        build_test_api_input_snapshots(sample, config)
    return PipelineGraph(
        nodes=[
            _node("request", node_type, config),
            _node("rates", NodeType.CONSTANT, {"values": [{"name": "loading", "value": "2.0"}]}),
            _node("listed", NodeType.POLARS, {"code": _LISTED}),
        ],
        edges=[
            _edge("request", "listed", "policy"),
            _edge("request", "listed", "items"),
            _edge("rates", "listed"),
        ],
    )


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


def _preview(graph: PipelineGraph, target: str) -> pl.DataFrame:
    context = create_admitted_execution_context(
        operation="request_input_parity", profile=ExecutionProfile.LAZY_SINK
    )
    try:
        outputs, _, _, _ = execute_lazy_graph(
            graph, _build_node_fn, source="live", execution_context=context
        )
        frame = outputs[target]
        return frame.collect() if isinstance(frame, pl.LazyFrame) else frame
    finally:
        context.release_admission(preserve_primary_error=True)


@REQUEST_INPUTS
def test_a_workbench_input_reads_a_request_as_a_quote_input_does(
    project: Path, node_type: NodeType
) -> None:
    graph = _pricing(project, node_type)
    folder = FOLDERS[node_type]
    reads_a_file = node_type is not NodeType.WORKBENCH_INPUT

    pipeline_file = _write(graph, project)
    code = pipeline_file.read_text(encoding="utf-8")
    assert f'@pipeline.{DECORATORS[node_type]}(config="config/{folder}/request.json")' in code
    parsed = parse_pipeline_file(pipeline_file).node_map["request"]
    assert parsed.data.nodeType is node_type
    assert parsed.data.config["tables"] == (_QUOTE_TABLES if reads_a_file else _TABLES)
    assert ("path" in parsed.data.config) is reads_a_file

    # In the editor, a Quote Input reads its sample and a Workbench Input yields nulls.
    names = ["NY", "A", "B", "2.0"] if reads_a_file else [None, None, "2.0"]
    assert_frame_equal(_preview(graph, "listed"), pl.DataFrame({"name": names}))
    assert [
        (node_id, kind)
        for node_id, kind, _ in snapshot_backed_inputs(
            ["request", "rates", "listed"], graph.node_map
        )
    ] == ([("request", "api_input")] if reads_a_file else [])
    assert point_kind(graph, DataPoint("request", "items")) == "api_input_table"

    # Two sources: only a request input recognised as one is seeded with the live frames.
    scored = _import(pipeline_file).pipeline.score(
        {
            "policy": pl.DataFrame({"state": ["TX"], "limit": [5]}),
            "items": pl.DataFrame({"item_id": ["Z"], "value": [7.0]}),
        }
    )
    assert scored["name"].to_list() == ["TX", "Z", "2.0"]
    assert detect_row_lineage_type(node_type=node_type.value) == "created"


@REQUEST_INPUTS
def test_deploy_scoring_seeds_the_request_input(project: Path, node_type: NodeType) -> None:
    if node_type is NodeType.WORKBENCH_INPUT:
        table = {
            "name": "quote",
            "rows": "one",
            "columns": [_column("state", "str"), _column("limit", "int")],
        }
        config: dict[str, Any] = {"tables": [table]}
        # One quote, its one-row table an object under its name, read into the table's frame.
        request = pl.DataFrame([{"quote": {"state": "TX", "limit": 5}}])
    else:
        sample = project / "quotes.json"
        sample.write_text(json.dumps([{"state": "NY", "limit": 1000}]), encoding="utf-8")
        table = {
            "path": "$[:]",
            "label": "quote",
            "emit": True,
            "row_id_column": None,
            "columns": [
                {**_quote_column("quote", "state", "str"), "path": "$[:].state"},
                {**_quote_column("quote", "limit", "int"), "path": "$[:].limit"},
            ],
        }
        config = {"tables": [table], "path": str(sample)}
        # The request's records, which deploy hands the port whole (BUG-31).
        request = pl.DataFrame({"state": ["TX"], "limit": [5]})
    graph = PipelineGraph(
        nodes=[
            _node("request", node_type, config),
            _node("rates", NodeType.CONSTANT, {"values": [{"name": "loading", "value": "2.0"}]}),
            _node(
                "out", NodeType.OUTPUT, make_output_config(["state", "limit"], source_port="quote")
            ),
        ],
        edges=[_edge("request", "out", "quote")],
    )

    inputs = find_deploy_input_nodes(graph)
    result = score_graph(graph=graph, input_df=request, input_node_ids=inputs, output_node_id="out")

    assert inputs == ["request"]
    assert result.select("state", "limit").to_dicts() == [{"state": "TX", "limit": 5}]


def test_a_workbench_input_previews_one_null_row_per_table(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    # A join of its two tables, which an admitted preview runs only when it can size both.
    graph.nodes.append(
        _node(
            "joined",
            NodeType.POLARS,
            {"code": "df = policy.join(items, left_on='state', right_on='item_id', how='left')"},
        )
    )
    graph.edges += [_edge("request", "joined", "policy"), _edge("request", "joined", "items")]

    joined = _preview(graph, "joined")

    # The join keeps its left key; every column keeps its declared type.
    assert joined.schema == pl.Schema({"state": pl.String, "limit": pl.Int64, "value": pl.Float64})
    assert joined.to_dicts() == [{"state": None, "limit": None, "value": None}]
    with pytest.raises(WorkbenchInputError, match="has no tables"):
        workbench_table_frames({"tables": []})
    # A table with no columns yet is no port, and a node needs one.
    with pytest.raises(WorkbenchInputError, match="have no columns yet"):
        workbench_table_labels({"tables": [{**_TABLES[0], "columns": []}]})
    assert workbench_table_labels({"tables": [{**_TABLES[0], "columns": []}, _TABLES[1]]}) == (
        "items",
    )


def test_a_workbench_input_table_point_reads_directly(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    resolver = DataPointResolver(graph, source="live", store=NodeSnapshotStore(project))

    resolution = resolver.resolve(DataPoint("request", "items"), NodeSnapshotColumns.of({"value"}))
    with resolver.lease_resolved(resolution) as leased:
        frame = leased.scan.collect()

    assert (resolution.kind, resolution.state, resolution.input_identity) == (
        "api_input_table",
        "current",
        None,
    )
    assert frame.schema == pl.Schema({"value": pl.Float64})
    assert frame.to_dicts() == [{"value": None}]


def test_a_saved_workbench_input_loads_with_its_ports(project: Path) -> None:
    """The document's identities give a Workbench Input its tables' ports, read as the
    editor reads a copy, without refusing a config that may still need repair."""
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)

    document = load_pipeline_editor_document(_write(graph, project, "main"), project_root=project)

    assert document.load_status == "ready"
    request = next(node for node in document.nodes if node.authored_id == "request")
    assert request.source_handle_input_names == {"policy": "policy", "items": "items"}
    assert sorted(
        edge.input_name for edge in document.edges if edge.source_authored_id == "request"
    ) == ["items", "policy"]
    # A table without a column, a name that cannot be a handle, a name already used whatever
    # its case, and an entry that is not a table give no port; nothing is refused.
    tables = [
        {**_TABLES[0], "columns": []},
        _TABLES[1],
        {"name": "Items", "rows": "one", "columns": [_column("x", "int")]},
        {"name": "class", "rows": "one", "columns": [_column("x", "int")]},
        {"name": "half", "rows": "some", "columns": [_column("x", "int")]},
        "policy",
    ]
    assert recoverable_request_input_source_handles(
        NodeType.WORKBENCH_INPUT, {"tables": tables}
    ) == ("items",)
    assert recoverable_request_input_source_handles(NodeType.WORKBENCH_INPUT, {}) == ()

    # A column whose type is not even a string is left out too, and the document still
    # loads with the ports of the tables that read: a config to repair, not a crash.
    broken = graph.model_copy(deep=True)
    broken.nodes[0].data.config["tables"] = [
        {**_TABLES[0], "columns": [{"name": "limit", "type": []}, {"name": "state", "type": {}}]},
        _TABLES[1],
    ]
    loaded = load_pipeline_editor_document(_write(broken, project, "broken"), project_root=project)
    assert loaded.load_status != "source_only"
    request = next(node for node in loaded.nodes if node.authored_id == "request")
    assert request.source_handle_input_names == {"items": "items"}


def test_deploy_derives_a_workbench_inputs_request_schema(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)

    assert infer_input_schema(graph, "request") == {
        "policy": str(pl.Struct({"state": pl.String, "limit": pl.Int64})),
        "items": str(pl.List(pl.Struct({"item_id": pl.String, "value": pl.Float64}))),
    }
    # The dry run reads one quote with nothing filled in: one row of nulls per table.
    assert _read_sample_row(graph, ["request"]).to_dicts() == [
        {
            "policy": {"state": None, "limit": None},
            "items": [{"item_id": None, "value": None}],
        }
    ]
    empty = graph.model_copy(deep=True)
    empty.nodes[0].data.config["tables"] = []
    with pytest.raises(ValueError, match="has no tables"):
        infer_input_schema(empty, "request")
    malformed = graph.model_copy(deep=True)
    malformed.nodes[0].data.config["tables"] = [{**_TABLES[0], "path": "$[:]"}]
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Cannot derive the request schema of Workbench Input 'request': The Workbench "
            "Input's tables are not as the workbench writes them (tables[0].path: Extra inputs "
            "are not permitted)."
        ),
    ):
        infer_input_schema(malformed, "request")


def test_deploy_resolves_a_workbench_input_without_a_sample_file(project: Path) -> None:
    # Deploy reads the request into the table, so the response's columns are the table's.
    table = {
        "name": "quote",
        "rows": "one",
        "columns": [_column("state", "str"), _column("limit", "int")],
    }
    graph = PipelineGraph(
        nodes=[
            _node("request", NodeType.WORKBENCH_INPUT, {"tables": [table]}),
            _node(
                "out", NodeType.OUTPUT, make_output_config(["state", "limit"], source_port="quote")
            ),
        ],
        edges=[_edge("request", "out", "quote")],
    )

    resolved = resolve_config(
        DeployConfig(pipeline_file=_write(graph, project, "main"), model_name="m")
    )

    assert resolved.input_node_ids == ["request"]
    assert resolved.input_schema == {
        "quote": str(pl.Struct({"state": pl.String, "limit": pl.Int64}))
    }
    assert resolved.output_schema == {"state": "String", "limit": "Int64"}

    # Deploy's test quotes are scored one request each, their expected outputs compared in
    # the file's order: a file of several quotes passes, and a drift names its row.
    def case(state: str, limit: int, expected_limit: int) -> dict[str, Any]:
        return {
            "input": {"quote": {"state": state, "limit": limit}},
            "expected": {"state": state, "limit": expected_limit},
        }

    quotes = project / "test_quotes"
    quotes.mkdir()
    (quotes / "quotes.json").write_text(
        json.dumps([case("TX", 5, 5), case("CA", 7, 7)]), encoding="utf-8"
    )
    (quotes / "drift.json").write_text(
        json.dumps([case("TX", 5, 5), case("CA", 7, 8)]), encoding="utf-8"
    )

    results = {result["file"]: result for result in score_test_quotes(resolved, quotes)}

    assert (results["quotes.json"]["status"], results["quotes.json"]["rows"]) == ("ok", 2)
    assert results["drift.json"]["status"] == "error"
    assert "row 1 column 'limit'" in str(results["drift.json"]["error"])


def test_the_cache_report_reads_a_workbench_input_directly(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    graph.nodes.append(_node("explore", NodeType.EXPLORE, {"steps": []}))
    graph.edges.append(_edge("request", "explore", "items"))

    rows = {
        node_id: (point, reason)
        for node_id, point, reason, _digests in node_data_service().points_for_graph(
            graph, "live", NodeSnapshotStore(project)
        )
    }

    for node_id in ("request", "explore"):
        point, reason = rows[node_id]
        assert reason is None and point is not None, node_id
        assert (point.kind, point.state, point.reads_directly) == (
            "api_input_table",
            "current",
            True,
        ), node_id


def test_the_node_data_routes_read_a_workbench_table_directly(project: Path, client) -> None:
    """A node reading a Workbench Input's table has nothing to build, cache or clear."""
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    graph.nodes.append(_node("explore", NodeType.EXPLORE, {"steps": []}))
    graph.edges.append(_edge("request", "explore", "items"))
    body = {"graph": graph.model_dump(mode="json"), "node_id": "explore", "source": "live"}

    point = client.post("/api/node-data/point", json=body)
    run = client.post("/api/node-data/run", json=body)
    cleared = client.post("/api/node-data/clear", json=body)

    assert point.status_code == 200, point.text
    assert (point.json()["state"], point.json()["reads_directly"]) == ("current", True)
    assert run.status_code == 200, run.text
    assert (run.json()["status"], run.json()["message"]) == (
        "completed",
        "A Workbench Input's tables are read directly; there is nothing to cache.",
    )
    # The editor clears nothing through the input cache for a point that reads directly.
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["status"] == "delegated"
    assert cleared.json()["point"]["reads_directly"] is True


# ---------------------------------------------------------------------------
# The workbench's sample quote (specs/workbench)
# ---------------------------------------------------------------------------


def _sampled(project: Path, sample: Any) -> PipelineGraph:
    """The pricing graph, its Workbench Input holding *sample*."""
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    graph.nodes[0].data.config["sample"] = sample
    return graph


def _with_node(graph: PipelineGraph, nid: str, code: str, *ports: str) -> PipelineGraph:
    """*graph* and a Polars node *nid* reading the Workbench Input's *ports*."""
    graph.nodes.append(_node(nid, NodeType.POLARS, {"code": code}))
    graph.edges.extend(_edge("request", nid, port) for port in ports)
    return graph


_DOUBLED = "df = items.with_columns(pl.col('value') * 2)"
_JOINED = "df = policy.join(items, left_on='state', right_on='item_id', how='left')"


def test_a_workbench_input_previews_its_sample(project: Path) -> None:
    # Fields under no table are ignored, as a request's are.
    sample = {**copy.deepcopy(_SAMPLE[0]), "unread": {"x": "y"}}
    graph = _sampled(project, sample)

    # Read as the Quote Input reads the same quote from its sample file, in code too.
    assert _preview(graph, "listed")["name"].to_list() == ["NY", "A", "B", "2.0"]
    ran = _import(_write(graph, project)).pipeline.run(source="live")
    listed = ran.collect() if isinstance(ran, pl.LazyFrame) else ran
    assert listed["name"].to_list() == ["NY", "A", "B", "2.0"]
    # A calculation downstream runs on the sample's values.
    doubled = _preview(
        _with_node(_sampled(project, sample), "doubled", _DOUBLED, "items"), "doubled"
    )
    assert doubled.to_dicts() == [{"item_id": "A", "value": 20.0}, {"item_id": "B", "value": 60.0}]
    # Leasing a table point reads the sample's rows.
    resolver = DataPointResolver(graph, source="live", store=NodeSnapshotStore(project))
    resolution = resolver.resolve(DataPoint("request", "items"), NodeSnapshotColumns.of({"value"}))
    with resolver.lease_resolved(resolution) as leased:
        assert leased.scan.collect().to_dicts() == [{"value": 10.0}, {"value": 30.0}]

    # A column a row does not hold is null, and so is a column the quote does not name; a
    # table the sample does not hold is one row of nulls.
    partial = {"tables": _TABLES, "sample": {"policy": {"limit": 1000, "unread": "x"}}}
    frames = workbench_table_frames(partial)
    assert frames["policy"].collect().to_dicts() == [{"state": None, "limit": 1000}]
    assert frames["items"].collect().to_dicts() == [{"item_id": None, "value": None}]


def test_a_demand_cuts_the_sample_to_the_ports_and_columns_asked_for() -> None:
    config = {"tables": _TABLES, "sample": copy.deepcopy(_SAMPLE[0])}

    asked = workbench_table_frames(
        config, port_columns={"items": frozenset({"value"}), "policy": None}
    )

    assert list(asked) == ["items", "policy"]
    assert asked["items"].collect().to_dicts() == [{"value": 10.0}, {"value": 30.0}]
    assert asked["policy"].collect_schema().names() == ["state", "limit"]
    # A demand of no columns keeps the rows through one carrier column.
    carried = workbench_table_frames(config, port_columns={"items": frozenset()})
    assert carried["items"].collect_schema().names() == ["item_id"]
    with pytest.raises(ValueError, match="requests unknown port 'nope'"):
        workbench_table_frames(config, port_columns={"nope": None})
    with pytest.raises(
        ValueError,
        match=re.escape("port_columns['items'] requests missing declared column(s): ['nope']"),
    ):
        workbench_table_frames(config, port_columns={"items": frozenset({"nope"})})


@pytest.mark.parametrize(
    ("kind", "value", "read"),
    [
        ("str", "NY", "NY"),
        ("int", 7, 7),
        ("float", 7, 7.0),
        ("float", 2.5, 2.5),
        ("bool", False, False),
        ("date", "2026-01-15", date(2026, 1, 15)),
        ("int", None, None),
    ],
)
def test_a_value_is_read_as_its_columns_type(kind: str, value: Any, read: Any) -> None:
    tables = parse_workbench_tables(
        [{"name": "t", "rows": "one", "columns": [_column("v", kind)]}], owner="Workbench Input"
    )

    assert read_quote(tables, {"t": {"v": value}})["t"].to_dicts() == [{"v": read}]


@pytest.mark.parametrize(
    ("kind", "value", "shown"),
    [
        ("int", True, "True"),
        ("int", 5.5, "5.5"),
        ("float", "7", "'7'"),
        ("float", float("inf"), "inf"),
        ("bool", 1, "1"),
        ("str", 5, "5"),
        ("date", "15/01/2026", "'15/01/2026'"),
        ("date", "2026-13-40", "'2026-13-40'"),
        ("int", "x" * 50, "'" + "x" * 38 + "…"),
    ],
)
def test_a_value_that_is_not_of_its_columns_type_is_refused_naming_it(
    kind: str, value: Any, shown: str
) -> None:
    tables = parse_workbench_tables(
        [{"name": "t", "rows": "many", "columns": [_column("v", kind)]}], owner="Workbench Input"
    )

    with pytest.raises(WorkbenchInputError, match=re.escape(f"t[0].v is {shown}, not {kind!r}")):
        read_quote(tables, {"t": [{"v": value}]})


def test_a_workbench_inputs_table_point_follows_its_sample(project: Path) -> None:
    def version(sample: Any) -> str | None:
        graph = _sampled(project, sample)
        resolver = DataPointResolver(graph, source="live", store=NodeSnapshotStore(project))
        demand = NodeSnapshotColumns.of({"value"})
        return resolver.resolve(DataPoint("request", "items"), demand).data_version

    versions = [version(sample) for sample in ({}, _SAMPLE[0], {"policy": {"state": "CA"}})]

    assert len(set(versions)) == 3


_MISFITS = {
    "wrong type": ({"policy": {"limit": "lots"}}, "policy.limit is 'lots', not 'int'"),
    "not an object": ([], "the sample is a list, not an object"),
    "object for a list": ({"items": {"value": 1}}, "items is an object, not a list"),
    "value for an object": ({"policy": 17}, "policy is a value, not an object"),
    "value in a list": ({"items": [{"item_id": "A"}, 5]}, "items[1] is a value, not a row"),
    "null in a list": ({"items": [None]}, "items[0] is null, not a row"),
    "list in a list": ({"items": [[1]]}, "items[0] is a list, not a row"),
}


@pytest.mark.parametrize(("sample", "reason"), list(_MISFITS.values()), ids=list(_MISFITS))
def test_a_misfit_sample_fails_what_reads_its_rows(
    project: Path, sample: dict[str, Any], reason: str
) -> None:
    message = (
        "The workbench's sample does not fit this Workbench Input's tables: "
        f"{reason}. Correct it in the workbench and save it."
    )
    with pytest.raises(WorkbenchInputError, match=re.escape(message)):
        _preview(_sampled(project, sample), "listed")
    # The sample is read whole, so a node reading only items still finds a misfit in policy.
    with pytest.raises(WorkbenchInputError, match=re.escape(message)):
        _preview(_with_node(_sampled(project, sample), "doubled", _DOUBLED, "items"), "doubled")
    # Planning a join sizes both tables from the sample, and says what to correct.
    joined = _with_node(_sampled(project, sample), "joined", _JOINED, "policy", "items")
    with pytest.raises(WorkbenchInputError, match=re.escape(message)):
        _preview(joined, "joined")
    with pytest.raises(WorkbenchInputError, match=re.escape(message)):
        _import(_write(_sampled(project, sample), project)).pipeline.run(source="live")

    # Resolving points reads the tables alone, so the cache report still has them.
    graph = _sampled(project, sample)
    graph.nodes.append(_node("explore", NodeType.EXPLORE, {"steps": []}))
    graph.edges.append(_edge("request", "explore", "items"))
    rows = {
        node_id: point
        for node_id, point, _reason, _digests in node_data_service().points_for_graph(
            graph, "live", NodeSnapshotStore(project)
        )
    }
    for node_id in ("request", "explore"):
        assert rows[node_id] is not None and rows[node_id].state == "current", node_id


def test_a_misfit_sample_answers_the_preview_route_with_422(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview runs in its spawned worker, which reports the error by its class."""
    from fastapi.testclient import TestClient

    from haute._interactive_workers import shutdown_interactive_worker_pool
    from haute.server import app

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    monkeypatch.setenv("HAUTE_INTERACTIVE_WORKER_COUNT", "1")
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    shutdown_interactive_worker_pool()
    graph = _sampled(project, {"items": {"value": 1}})

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/pipeline/preview",
                json={"graph": graph.model_dump(mode="json"), "node_id": "listed"},
            )
    finally:
        shutdown_interactive_worker_pool()

    assert response.status_code == 422, response.text
    assert "items is an object, not a list. Correct it in the workbench and save it." in (
        response.text
    )


@pytest.mark.parametrize(
    "sample", [_SAMPLE[0], {"items": {"value": 1}}], ids=["a sample", "a misfit sample"]
)
def test_a_request_never_reads_the_sample(project: Path, sample: dict[str, Any]) -> None:
    graph = _sampled(project, copy.deepcopy(sample))
    request = {
        "policy": pl.DataFrame({"state": ["TX"], "limit": [5]}),
        "items": pl.DataFrame({"item_id": ["Z"], "value": [7.0]}),
    }

    scored = _import(_write(graph, project)).pipeline.score(request)
    deployed = score_graph(
        graph=graph,
        input_df=_quote(
            {"policy": {"state": "TX", "limit": 5}, "items": [{"item_id": "Z", "value": 7.0}]}
        ),
        input_node_ids=["request"],
        output_node_id="listed",
    )

    assert scored["name"].to_list() == ["TX", "Z", "2.0"]
    assert deployed["name"].to_list() == ["TX", "Z", "2.0"]
    without = _pricing(project, NodeType.WORKBENCH_INPUT)
    assert infer_input_schema(graph, "request") == infer_input_schema(without, "request")
    assert_frame_equal(_read_sample_row(graph, ["request"]), _read_sample_row(without, ["request"]))


def _quote(*quotes: dict[str, Any]) -> pl.DataFrame:
    """A request as a deployed pipeline builds it: its quotes as records."""
    return pl.DataFrame(list(quotes))


def test_a_deployed_pipeline_reads_the_request_into_the_tables(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    quote = {"policy": {"state": "TX", "limit": 5}, "items": [{"item_id": "Z", "value": 7.0}]}

    def deployed(request: pl.DataFrame) -> pl.DataFrame:
        return score_graph(
            graph=graph, input_df=request, input_node_ids=["request"], output_node_id="listed"
        )

    assert deployed(_quote(quote))["name"].to_list() == ["TX", "Z", "2.0"]
    # One quote per request, and each part as its table's shape.
    with pytest.raises(
        WorkbenchInputError, match="reads one quote per request, and this request holds 2"
    ):
        deployed(_quote(quote, quote))
    with pytest.raises(
        WorkbenchInputError,
        match=re.escape(
            "The request does not fit this Workbench Input's tables: items is an object, not "
            "a list."
        ),
    ):
        deployed(_quote({"policy": {"state": "TX", "limit": 5}, "items": {"item_id": "Z"}}))


def test_generated_code_runs_a_workbench_input(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)

    ran = _import(_write(graph, project)).pipeline.run(source="live")

    listed = ran.collect() if isinstance(ran, pl.LazyFrame) else ran
    assert listed["name"].to_list() == [None, None, "2.0"]

    # A submodel's file declares one with the decorator `Submodel` shares with `Pipeline`.
    main = _write(PipelineGraph(nodes=[_occurrence()], edges=[], submodels=_nested()), project)
    definition = (project / "modules" / "pricing.py").read_text(encoding="utf-8")
    assert "@submodel.workbench_input(" in definition
    nested = parse_pipeline_file(main).submodels["pricing"].graph.node_map["workbench"]
    assert nested.data.nodeType is NodeType.WORKBENCH_INPUT
    assert nested.data.config["tables"] == _TABLES[:1]


# ---------------------------------------------------------------------------
# One request input per pipeline
# ---------------------------------------------------------------------------


def _request(nid: str, node_type: NodeType) -> GraphNode:
    config: dict[str, Any] = {"tables": copy.deepcopy(_TABLES[:1])}
    if node_type is not NodeType.WORKBENCH_INPUT:
        config["path"] = "quotes.json"
    return _node(nid, node_type, config)


def _nested() -> dict[str, SubmodelDefinition]:
    """A submodel definition holding a Workbench Input."""
    return {
        "pricing": SubmodelDefinition(
            definitionId="pricing",
            file="modules/pricing.py",
            graph=PipelineGraph(
                nodes=[_request("workbench", NodeType.WORKBENCH_INPUT)],
                edges=[],
                pipeline_name="pricing",
            ),
            inputPorts=[],
            outputPorts=[],
        )
    }


def _occurrence() -> GraphNode:
    return _node("pricing", NodeType.SUBMODEL, {"definitionId": "pricing", "alias": "pricing"})


def _two_request_inputs(case: str) -> PipelineGraph:
    quote = _request("quote", NodeType.API_INPUT)
    response = _node(
        "response", NodeType.OUTPUT, make_output_config(["state"], source_port="policy")
    )
    to_response = _edge("quote", "response", "policy")
    if case == "mixed":
        # The Workbench Input is off the response's path, so pruning would drop it.
        nodes = [quote, _request("workbench", NodeType.WORKBENCH_INPUT), response]
        return PipelineGraph(nodes=nodes, edges=[to_response])
    if case == "two_workbench":
        first = _request("first", NodeType.WORKBENCH_INPUT)
        second = _request("second", NodeType.WORKBENCH_INPUT)
        return PipelineGraph(
            nodes=[first, second, response],
            edges=[_edge("first", "response", "policy")],
        )
    return PipelineGraph(
        nodes=[quote, response, _occurrence()], edges=[to_response], submodels=_nested()
    )


@pytest.mark.parametrize("case", ["mixed", "two_workbench", "nested"])
def test_a_pipeline_has_one_request_input(project: Path, case: str) -> None:
    graph = _two_request_inputs(case)

    with pytest.raises(HTTPException) as refused:
        SavePipelineService(project).validate_graph(graph, source_file="main.py")
    assert refused.value.status_code == 400
    assert refused.value.detail == ONE_REQUEST_INPUT

    main = _write(graph, project, "main")
    with pytest.raises(ValueError, match=re.escape(ONE_REQUEST_INPUT)):
        resolve_config(DeployConfig(pipeline_file=main, model_name="m"))
