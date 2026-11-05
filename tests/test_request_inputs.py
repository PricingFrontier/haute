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
from unittest.mock import patch

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from pydantic import ValidationError

from haute._api_input_schema import ApiInputSchemaError, columns_from_sidecar, validate_v2_schema
from haute._builders import _build_node_fn
from haute._data_points import DataPoint, DataPointResolver, point_kind
from haute._editor_identities import (
    recoverable_api_input_source_handles,
    recoverable_request_input_source_handles,
)
from haute._execution_admission import create_admitted_execution_context
from haute._graph_utils import is_frame_label
from haute._input_preparation import snapshot_backed_inputs
from haute._node_config_recovery import reconcile_config
from haute._node_snapshots import NodeSnapshotColumns, NodeSnapshotStore
from haute._node_specs import REQUEST_INPUT_KINDS, REQUEST_INPUT_NODE_TYPES, SINGLETON_NODE_NAMES
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._project_context import current_project
from haute._trace_enrichment import detect_row_lineage_type
from haute._types import (
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
    workbench_request_frames,
    workbench_table_frames,
    workbench_table_labels,
)
from haute._workbench_tables import WorkbenchTablesError, parse_workbench_tables
from haute.codegen import graph_to_code_multi
from haute.deploy import _scorer
from haute.deploy._config import DeployConfig, resolve_config
from haute.deploy._container import _generate_app_source
from haute.deploy._pruner import find_deploy_input_nodes
from haute.deploy._schema import _read_sample_row, infer_input_schema
from haute.deploy._scorer import DeployInput, QuoteRequest, score_graph
from haute.deploy._utils import shipped_graph
from haute.deploy._validators import score_test_quotes
from haute.errors import ConfigError, NodeConfigError, RequestRefusedError
from haute.execution import ExecutionProfile, execute_lazy_graph
from haute.parser import parse_pipeline_file
from haute.routes._save_pipeline import SavePipelineService
from haute.routes.node_data import node_data_service
from haute.schemas import EditorIdentityRequestNode
from tests._project import project_app, use_project
from tests._source_files import source_files
from tests._workbench_helpers import form_table, without_form_tables, workbench_project
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
    if path == "_node_type.py":
        node_type = _named(tree.body, ast.ClassDef, "NodeType")
        allowed |= {id(_assigned(node_type.body, "API_INPUT"))} if node_type else set()
    if path == "_node_specs.py":
        # The specification table names each type in its own entry; the request
        # inputs are derived from its ``request_input`` flags.
        allowed |= _everything(_assigned(tree.body, "NODE_SPECS"))
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


#: A line that means the Quote Input alone, deliberately, says so: the one form a check
#: against the Quote Input's type may take outside the forms the file holds.
QUOTE_INPUT_ALONE = "# the Quote Input alone"


def request_input_reports(source: str, path: str) -> list[Report]:
    """Each Quote Input reference in *source* outside the forms *path* may hold."""
    tree = ast.parse(source)
    allowed = _allowed(tree, path)
    lines = source.splitlines()
    return sorted(
        Report(path, getattr(node, "lineno", 0))
        for node in ast.walk(tree)
        if (_is_member(node, "API_INPUT") or _is_api_input_value(node))
        and id(node) not in allowed
        and QUOTE_INPUT_ALONE not in lines[node.lineno - 1]
    )


_ACCEPTED = [
    ("execution.py", "M = {NodeType.API_INPUT: 'path', NodeType.WORKBENCH_INPUT: 'path'}\n"),
    # A check that means the Quote Input alone, deliberately, says so on its line.
    ("_seed_plans.py", "ok = node.data.nodeType is NodeType.API_INPUT  # the Quote Input alone\n"),
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
    ("_node_type.py", "class NodeType(StrEnum):\n    API_INPUT = 'apiInput'\n"),
    (
        "_node_specs.py",
        "NODE_SPECS = MappingProxyType({NodeType.API_INPUT: NodeSpec(NodeType.API_INPUT)})\n",
    ),
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


@pytest.mark.meta
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
    """A Quote Input's table labels, a Workbench Input's ports and the editor's source
    handles are held to `is_frame_label` alike. A Workbench Input's port is its table's name
    and side, ``<table>_input``, so a table a keyword names (class) gives a port that is
    one; its name must still be an identifier, so no name gives none."""
    assert is_frame_label(label) is allowed
    quote_table = {
        "path": "$[:]",
        "label": label,
        "emit": True,
        "columns": [{"name": "x", "path": "$[:].x", "type": "int", "selected": True}],
    }
    port = f"{label}_input"
    workbench_table = {
        "name": label,
        "side": "input",
        "port": port,
        "rows": "one",
        "columns": [{"name": "x", "type": "int"}],
    }

    refused: list[str] = []
    try:
        validate_v2_schema({"tables": [quote_table]})
    except ApiInputSchemaError:
        refused.append("quote input")
    try:
        EditorIdentityRequestNode(
            node_id="n", label="n", node_type=NodeType.WORKBENCH_INPUT, source_handles=[label]
        )
    except ValidationError:
        refused.append("identity")
    try:
        parse_workbench_tables([workbench_table], owner="Workbench Input")
    except WorkbenchTablesError:
        took = False
    else:
        took = True

    assert refused == ([] if allowed else ["quote input", "identity"])
    assert took is (label.isidentifier() and is_frame_label(port))
    # Recovery derives a request input's handles by its type, held to the same rule.
    expected = (label,) if allowed else ()
    assert recoverable_api_input_source_handles({"tables": [quote_table]}) == expected
    assert (
        recoverable_request_input_source_handles(NodeType.API_INPUT, {"tables": [quote_table]})
        == expected
    )
    # A Workbench Input names none: its tables are the form's, given where it runs, and an
    # edge out of it takes its handle as its input name.
    assert recoverable_request_input_source_handles(NodeType.WORKBENCH_INPUT, {}) == ()


# ---------------------------------------------------------------------------
# The request inputs, side by side
# ---------------------------------------------------------------------------


def _column(name: str, kind: str) -> dict[str, Any]:
    return {"name": name, "type": kind}


# A one-row and a many-row table's input sides, as the workbench supplies them, each its
# port ``<table>_input``. The many-row side is given no key here, so its rows read as the
# Quote Input reads the same quote's; a form keys it by its index (FORM_TABLES).
_TABLES: list[dict[str, Any]] = [
    {
        "name": "policy",
        "side": "input",
        "port": "policy_input",
        "rows": "one",
        "columns": [_column("state", "str"), _column("limit", "int")],
    },
    {
        "name": "items",
        "side": "input",
        "port": "items_input",
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
#: `_TABLES` as the project's form defines them, and `_SAMPLE`'s quote as typed into it: what
#: the pipeline file runs on, on its own, what deploy reads and what the routes give a
#: browser's graph (specs/workbench). Each leads with the quote's identity, and items with
#: its index, row_number, which keys its rows.
FORM_TABLES = [
    form_table("policy", ("state", "str"), ("limit", "int")),
    form_table("items", ("item_id", "str"), ("value", "float"), many=True),
]
FORM_SAMPLE = {
    "policy": [{"state": "NY", "limit": "1000"}],
    "items": [{"item_id": "A", "value": "10"}, {"item_id": "B", "value": "30"}],
}


def _ports(node_type: NodeType) -> tuple[str, str]:
    """The ports a request input gives policy and items on: a Workbench Input's are its
    tables' input sides, a Quote Input's its tables' labels."""
    if node_type is NodeType.WORKBENCH_INPUT:
        return ("policy_input", "items_input")
    return ("policy", "items")


def _listed(policy: str, items: str) -> str:
    """Every input in one column, stacked, each table read under its port: no join, so no
    materialisation to admit. `score` hands seeded inputs over as data frames and the
    Constant's as lazy."""
    return (
        f"df = pl.concat([{policy}.lazy().select(pl.col('state').alias('name')), "
        f"{items}.lazy().select(pl.col('item_id').alias('name')), "
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
    original = current_project().root
    use_project(tmp_path)
    try:
        yield tmp_path
    finally:
        use_project(original)


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
    name = SINGLETON_NODE_NAMES[node_type]
    policy, items = _ports(node_type)
    return PipelineGraph(
        nodes=[
            _node(name, node_type, config),
            _node("rates", NodeType.CONSTANT, {"values": [{"name": "loading", "value": "2.0"}]}),
            _node("listed", NodeType.POLARS, {"code": _listed(policy, items)}),
        ],
        edges=[
            _edge(name, "listed", policy),
            _edge(name, "listed", items),
            _edge("rates", "listed"),
        ],
    )


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
    name = SINGLETON_NODE_NAMES[node_type]
    folder = FOLDERS[node_type]
    reads_a_file = node_type is not NodeType.WORKBENCH_INPUT
    if not reads_a_file:
        # Scored on its own, a Workbench Input is held to its project's form.
        workbench_project(project, FORM_TABLES)

    pipeline_file = _write(graph, project)
    code = pipeline_file.read_text(encoding="utf-8")
    assert f'@pipeline.{DECORATORS[node_type]}(config="config/{folder}/{name}.json")' in code
    parsed = parse_pipeline_file(pipeline_file).node_map[name]
    assert parsed.data.nodeType is node_type
    # A Workbench Input's config file holds no tables: the form's are given where it runs. A
    # Quote Input's columns load with the settings inference gives them back (READ-42).
    loaded = columns_from_sidecar({"tables": _QUOTE_TABLES})["tables"]
    assert parsed.data.config.get("tables") == (loaded if reads_a_file else None)
    assert ("path" in parsed.data.config) is reads_a_file

    # In the editor, a Quote Input reads its sample and a Workbench Input yields nulls.
    names = ["NY", "A", "B", "2.0"] if reads_a_file else [None, None, "2.0"]
    assert_frame_equal(_preview(graph, "listed"), pl.DataFrame({"name": names}))
    assert [
        (node_id, kind)
        for node_id, kind, _ in snapshot_backed_inputs([name, "rates", "listed"], graph.node_map)
    ] == ([(name, "api_input")] if reads_a_file else [])
    policy, items = _ports(node_type)
    assert point_kind(graph, DataPoint(name, items)) == "api_input_table"

    # Two sources: only a request input recognised as one is seeded with the live frames,
    # by its ports; the form keys a Workbench Input's items by their row_number.
    numbered = {"row_number": [1]} if not reads_a_file else {}
    scored = _import(pipeline_file).pipeline.score(
        {
            policy: pl.DataFrame({"state": ["TX"], "limit": [5]}),
            items: pl.DataFrame({**numbered, "item_id": ["Z"], "value": [7.0]}),
        }
    )
    assert scored["name"].to_list() == ["TX", "Z", "2.0"]
    assert detect_row_lineage_type(node_type=node_type.value) == "created"


@REQUEST_INPUTS
def test_deploy_scoring_seeds_the_request_input(project: Path, node_type: NodeType) -> None:
    name = SINGLETON_NODE_NAMES[node_type]
    port = "quote"
    if node_type is NodeType.WORKBENCH_INPUT:
        port = "quote_input"
        table = {
            "name": "quote",
            "side": "input",
            "port": port,
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
            _node(name, node_type, config),
            _node("rates", NodeType.CONSTANT, {"values": [{"name": "loading", "value": "2.0"}]}),
            _node(
                "quote_response",
                NodeType.OUTPUT,
                make_output_config(["state", "limit"], source_port=port),
            ),
        ],
        edges=[_edge(name, "quote_response", port)],
    )

    inputs = find_deploy_input_nodes(graph)
    result = score_graph(
        graph=graph, input_df=request, input_node_ids=inputs, output_node_id="quote_response"
    )

    assert inputs == [name]
    assert result.select("state", "limit").to_dicts() == [{"state": "TX", "limit": 5}]


def test_a_workbench_input_previews_one_null_row_per_table(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    # A join of its two tables, which an admitted preview runs only when it can size both.
    graph.nodes.append(
        _node(
            "joined",
            NodeType.POLARS,
            {
                "code": (
                    "df = policy_input.join(items_input, left_on='state', right_on='item_id', "
                    "how='left')"
                )
            },
        )
    )
    graph.edges += [
        _edge("workbench_input", "joined", "policy_input"),
        _edge("workbench_input", "joined", "items_input"),
    ]

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
        "items_input",
    )


def test_a_workbench_input_table_point_reads_directly(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    resolver = DataPointResolver(graph, source="live", store=NodeSnapshotStore(project))

    resolution = resolver.resolve(
        DataPoint("workbench_input", "items_input"), NodeSnapshotColumns.of({"value"})
    )
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
    """A Workbench Input holds no tables, so the document's identity names none of its
    handles; each edge out of it carries its input name, its handle, as the executor derives
    it, and the editor names an edge's input after its handle alike."""
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)

    document = load_pipeline_editor_document(_write(graph, project, "main"), project_root=project)

    assert document.load_status == "ready"
    request = next(node for node in document.nodes if node.authored_id == "workbench_input")
    assert request.config == {}
    assert request.source_handle_input_names == {}
    assert sorted(
        edge.input_name for edge in document.edges if edge.source_authored_id == "workbench_input"
    ) == ["items_input", "policy_input"]


def test_a_saved_workbench_input_holding_copies_opens_for_its_repair(project: Path) -> None:
    """A config file an earlier Haute wrote holds copies of the tables and sample: the strict
    parse refuses it naming the keys, and the editor opens the pipeline with that node
    unavailable, its repair removing them (specs/workbench)."""
    pipeline_file = _write(_pricing(project, NodeType.WORKBENCH_INPUT), project, "main")
    sidecar = project / "config" / "workbench_input" / "workbench_input.json"
    sidecar.write_text(json.dumps({"tables": _TABLES, "sample": _SAMPLE[0]}), encoding="utf-8")
    removed = (
        "workbenchInput config contains the removed tables and sample fields. A Workbench "
        "Input reads its tables and sample from the form folder: delete them."
    )

    with pytest.raises(ConfigError, match=re.escape(removed)):
        parse_pipeline_file(pipeline_file)
    document = load_pipeline_editor_document(pipeline_file, project_root=project)
    request = next(node for node in document.nodes if node.authored_id == "workbench_input")
    assert request.availability == "unavailable"
    repaired = reconcile_config(NodeType.WORKBENCH_INPUT, {"tables": _TABLES, "sample": {}})
    assert repaired.config == {}
    assert sorted(change.path for change in repaired.changes) == ["/sample", "/tables"]
    assert {change.outcome for change in repaired.changes} == {"removed"}


def test_deploy_derives_a_workbench_inputs_request_schema(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)

    assert infer_input_schema(graph, "workbench_input") == {
        "policy": str(pl.Struct({"state": pl.String, "limit": pl.Int64})),
        "items": str(pl.List(pl.Struct({"item_id": pl.String, "value": pl.Float64}))),
    }
    # The dry run reads one quote with nothing filled in: one row of nulls per table.
    assert _read_sample_row(graph, ["workbench_input"]).to_dicts() == [
        {
            "policy": {"state": None, "limit": None},
            "items": [{"item_id": None, "value": None}],
        }
    ]
    empty = graph.model_copy(deep=True)
    empty.nodes[0].data.config["tables"] = []
    with pytest.raises(ValueError, match="has no tables"):
        infer_input_schema(empty, "workbench_input")
    malformed = graph.model_copy(deep=True)
    malformed.nodes[0].data.config["tables"] = [{**_TABLES[0], "path": "$[:]"}]
    with pytest.raises(
        ValueError,
        match=re.escape(
            "Cannot derive the request schema of Workbench Input 'workbench_input': The Workbench "
            "Input's tables are not as the workbench gives them (tables[0].path: Extra inputs "
            "are not permitted)."
        ),
    ):
        infer_input_schema(malformed, "workbench_input")


def test_deploy_resolves_a_workbench_input_without_a_sample_file(project: Path) -> None:
    # Deploy reads the request into the table, so the response's columns are the table's.
    table = {
        "name": "quote",
        "side": "input",
        "port": "quote_input",
        "rows": "one",
        "columns": [_column("state", "str"), _column("limit", "int")],
    }
    graph = PipelineGraph(
        nodes=[
            _node("workbench_input", NodeType.WORKBENCH_INPUT, {"tables": [table]}),
            _node(
                "quote_response",
                NodeType.OUTPUT,
                make_output_config(["state", "limit"], source_port="quote_input"),
            ),
        ],
        edges=[_edge("workbench_input", "quote_response", "quote_input")],
    )

    main = _write(graph, project, "main")
    workbench_project(project, [form_table("quote", ("state", "str"), ("limit", "int"))])
    resolved = resolve_config(DeployConfig(pipeline_file=main, model_name="m"))

    # The form's tables: Haute's quote_details first, which a request names the quote in.
    assert resolved.input_node_ids == ["workbench_input"]
    assert list(resolved.input_schema) == ["quote_details", "quote"]
    assert resolved.input_schema["quote"] == str(pl.Struct({"state": pl.String, "limit": pl.Int64}))
    assert resolved.output_schema == {"state": "String", "limit": "Int64"}

    # Deploy's test quotes are scored one request each, their expected outputs compared in
    # the file's order: a file of several quotes passes, and a drift names its row.
    def case(state: str, limit: int, expected_limit: int) -> dict[str, Any]:
        return {
            "input": {
                "quote_details": {"quote_reference": "Q-1", "quote_option": 1},
                "quote": {"state": state, "limit": limit},
            },
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
    graph.edges.append(_edge("workbench_input", "explore", "items_input"))

    rows = {
        node_id: (point, reason)
        for node_id, point, reason, _digests in node_data_service().points_for_graph(
            graph, "live", NodeSnapshotStore(project)
        )
    }

    for node_id in ("workbench_input", "explore"):
        point, reason = rows[node_id]
        assert reason is None and point is not None, node_id
        assert (point.kind, point.state, point.reads_directly) == (
            "api_input_table",
            "current",
            True,
        ), node_id


def test_the_node_data_routes_read_a_workbench_table_directly(project: Path, client) -> None:
    """A node reading a Workbench Input's table has nothing to build, cache or clear."""
    workbench_project(project, FORM_TABLES, FORM_SAMPLE)
    graph = without_form_tables(_pricing(project, NodeType.WORKBENCH_INPUT))
    graph.nodes.append(_node("explore", NodeType.EXPLORE, {"steps": []}))
    graph.edges.append(_edge("workbench_input", "explore", "items_input"))
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
    graph.edges.extend(_edge("workbench_input", nid, port) for port in ports)
    return graph


_DOUBLED = "df = items_input.with_columns(pl.col('value') * 2)"
_JOINED = "df = policy_input.join(items_input, left_on='state', right_on='item_id', how='left')"


def test_a_workbench_input_previews_its_sample(project: Path) -> None:
    # Fields under no table are ignored, as a request's are.
    sample = {**copy.deepcopy(_SAMPLE[0]), "unread": {"x": "y"}}
    graph = _sampled(project, sample)

    # Read as the Quote Input reads the same quote from its sample file, and by the pipeline
    # file on its own, which reads the sample the project's form gives.
    assert _preview(graph, "listed")["name"].to_list() == ["NY", "A", "B", "2.0"]
    workbench_project(project, FORM_TABLES, FORM_SAMPLE)
    ran = _import(_write(graph, project)).pipeline.run(source="live")
    listed = ran.collect() if isinstance(ran, pl.LazyFrame) else ran
    assert listed["name"].to_list() == ["NY", "A", "B", "2.0"]
    # A calculation downstream runs on the sample's values.
    doubled = _preview(
        _with_node(_sampled(project, sample), "doubled", _DOUBLED, "items_input"), "doubled"
    )
    assert doubled.to_dicts() == [{"item_id": "A", "value": 20.0}, {"item_id": "B", "value": 60.0}]
    # Leasing a table point reads the sample's rows.
    resolver = DataPointResolver(graph, source="live", store=NodeSnapshotStore(project))
    resolution = resolver.resolve(
        DataPoint("workbench_input", "items_input"), NodeSnapshotColumns.of({"value"})
    )
    with resolver.lease_resolved(resolution) as leased:
        assert leased.scan.collect().to_dicts() == [{"value": 10.0}, {"value": 30.0}]

    # A column a row does not hold is null, and so is a column the quote does not name. The
    # sample is read as a request is: a many-row table it does not hold has no rows and a
    # one-row table its one row of nulls, so the sample prices as its quote would deployed.
    partial = {"tables": _TABLES, "sample": {"policy": {"limit": 1000, "unread": "x"}}}
    frames = workbench_table_frames(partial)
    assert frames["policy_input"].collect().to_dicts() == [{"state": None, "limit": 1000}]
    assert frames["items_input"].collect().to_dicts() == []
    # No sample, or nothing typed in it: one row of nulls per table, the null quote.
    for sample in ({}, None):
        blank = workbench_table_frames({"tables": _TABLES, "sample": sample})
        assert blank["policy_input"].collect().to_dicts() == [{"state": None, "limit": None}]
        assert blank["items_input"].collect().to_dicts() == [{"item_id": None, "value": None}]


@pytest.mark.parametrize(
    "quote",
    [
        {"policy": {"state": "TX", "limit": 5}},
        {"policy": {"state": "TX", "limit": 5}, "items": []},
        {"items": [{"item_id": "Z", "value": 7.0}]},
        _SAMPLE[0],
    ],
    ids=["no many-row table", "an empty many-row table", "no one-row table", "a full quote"],
)
def test_a_sample_prices_as_its_quote_would_when_deployed(quote: dict[str, Any]) -> None:
    """Preview prices a quote as the sample; a deployed pipeline reads it as the request."""
    sampled = workbench_table_frames({"tables": _TABLES, "sample": copy.deepcopy(quote)})
    requested = workbench_request_frames({"tables": _TABLES}, [copy.deepcopy(quote)])

    assert list(sampled) == list(requested)
    for name, frame in requested.items():
        assert_frame_equal(sampled[name].collect(), frame)


def test_a_demand_cuts_the_sample_to_the_ports_and_columns_asked_for() -> None:
    config = {"tables": _TABLES, "sample": copy.deepcopy(_SAMPLE[0])}

    asked = workbench_table_frames(
        config, port_columns={"items_input": frozenset({"value"}), "policy_input": None}
    )

    assert list(asked) == ["items_input", "policy_input"]
    assert asked["items_input"].collect().to_dicts() == [{"value": 10.0}, {"value": 30.0}]
    assert asked["policy_input"].collect_schema().names() == ["state", "limit"]
    # A demand of no columns keeps the rows through one carrier column.
    carried = workbench_table_frames(config, port_columns={"items_input": frozenset()})
    assert carried["items_input"].collect_schema().names() == ["item_id"]
    # A port is a side's: the table's own name is none.
    for unknown in ("nope", "items"):
        with pytest.raises(ValueError, match=f"requests unknown port '{unknown}'"):
            workbench_table_frames(config, port_columns={unknown: None})
    with pytest.raises(
        ValueError,
        match=re.escape(
            "port_columns['items_input'] requests missing declared column(s): ['nope']"
        ),
    ):
        workbench_table_frames(config, port_columns={"items_input": frozenset({"nope"})})


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
        [
            {
                "name": "t",
                "side": "input",
                "port": "t_input",
                "rows": "one",
                "columns": [_column("v", kind)],
            }
        ],
        owner="Workbench Input",
    )

    # Read from the quote's table by its name, into its port's frame.
    assert read_quote(tables, {"t": {"v": value}})["t_input"].to_dicts() == [{"v": read}]


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
        [
            {
                "name": "t",
                "side": "input",
                "port": "t_input",
                "rows": "many",
                "columns": [_column("v", kind)],
            }
        ],
        owner="Workbench Input",
    )

    with pytest.raises(WorkbenchInputError, match=re.escape(f"t[0].v is {shown}, not {kind!r}")):
        read_quote(tables, {"t": [{"v": value}]})


def test_a_workbench_inputs_table_point_follows_its_sample(project: Path) -> None:
    def version(sample: Any) -> str | None:
        graph = _sampled(project, sample)
        resolver = DataPointResolver(graph, source="live", store=NodeSnapshotStore(project))
        demand = NodeSnapshotColumns.of({"value"})
        return resolver.resolve(DataPoint("workbench_input", "items_input"), demand).data_version

    versions = [version(sample) for sample in ({}, _SAMPLE[0], {"policy": {"state": "CA"}})]

    assert len(set(versions)) == 3


_MISFITS = {
    "wrong type": ({"policy": {"limit": "lots"}}, "policy.limit is 'lots', not 'int'"),
    "object for a list": ({"items": {"value": 1}}, "items is an object, not a list"),
    "value for an object": ({"policy": 17}, "policy is a value, not an object"),
    "value in a list": ({"items": [{"item_id": "A"}, 5]}, "items[1] is a value, not a row"),
    "null in a list": ({"items": [None]}, "items[0] is null, not a row"),
    "list in a list": ({"items": [[1]]}, "items[0] is a list, not a row"),
}


def test_a_sample_that_is_not_an_object_is_refused_when_the_node_is_built(project: Path) -> None:
    # The form always gives an object (``sample_quote``); anything else is refused by the
    # Workbench Input's config model when the node is built, naming the setting.
    with pytest.raises(
        NodeConfigError, match=r"Node 'workbench_input' \(workbenchInput\) setting 'sample': "
    ):
        _preview(_sampled(project, []), "listed")


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
        _preview(
            _with_node(_sampled(project, sample), "doubled", _DOUBLED, "items_input"), "doubled"
        )
    # Planning a join sizes both tables from the sample, and says what to correct.
    joined = _with_node(_sampled(project, sample), "joined", _JOINED, "policy_input", "items_input")
    with pytest.raises(WorkbenchInputError, match=re.escape(message)):
        _preview(joined, "joined")

    # Resolving points reads the tables alone, so the cache report still has them.
    graph = _sampled(project, sample)
    graph.nodes.append(_node("explore", NodeType.EXPLORE, {"steps": []}))
    graph.edges.append(_edge("workbench_input", "explore", "items_input"))
    rows = {
        node_id: point
        for node_id, point, _reason, _digests in node_data_service().points_for_graph(
            graph, "live", NodeSnapshotStore(project)
        )
    }
    for node_id in ("workbench_input", "explore"):
        assert rows[node_id] is not None and rows[node_id].state == "current", node_id


def test_a_misfit_sample_in_the_form_fails_the_pipeline_file_on_its_own(project: Path) -> None:
    """Text typed into a whole-number column is kept as typed, so the sample the form gives
    does not fit, and the pipeline file running on its own says what to correct."""
    workbench_project(project, FORM_TABLES, {"policy": [{"state": "NY", "limit": "lots"}]})
    message = (
        "The workbench's sample does not fit this Workbench Input's tables: policy.limit is "
        "'lots', not 'int'. Correct it in the workbench and save it."
    )

    pipeline = _import(_write(_pricing(project, NodeType.WORKBENCH_INPUT), project)).pipeline
    with pytest.raises(WorkbenchInputError, match=re.escape(message)):
        pipeline.run(source="live")


def test_a_misfit_sample_answers_the_preview_route_with_422(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview runs in its spawned worker, which reports the error by its class."""
    from fastapi.testclient import TestClient

    from haute._interactive_workers import shutdown_interactive_worker_pool

    app = project_app()

    monkeypatch.setenv("HAUTE_INTERACTIVE_EXECUTION_MODE", "process")
    monkeypatch.setenv("HAUTE_INTERACTIVE_WORKER_COUNT", "1")
    monkeypatch.setenv("HAUTE_WORKER_MEMORY_ENFORCEMENT", "best_effort")
    shutdown_interactive_worker_pool()
    # The route gives the browser's graph the form's sample, misfit as typed.
    workbench_project(project, FORM_TABLES, {"policy": [{"state": "NY", "limit": "lots"}]})
    graph = without_form_tables(_pricing(project, NodeType.WORKBENCH_INPUT))

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                "/api/pipeline/preview",
                json={"graph": graph.model_dump(mode="json"), "node_id": "listed"},
            )
    finally:
        shutdown_interactive_worker_pool()

    assert response.status_code == 422, response.text
    assert "policy.limit is 'lots', not 'int'. Correct it in the workbench and save it." in (
        response.text
    )


@pytest.mark.parametrize(
    "sample", [_SAMPLE[0], {"items": {"value": 1}}], ids=["a sample", "a misfit sample"]
)
def test_a_request_never_reads_the_sample(project: Path, sample: dict[str, Any]) -> None:
    graph = _sampled(project, copy.deepcopy(sample))
    # Scored on its own, a Workbench Input reads its project's form, never its sample.
    workbench_project(project, FORM_TABLES, {"policy": [{"state": "NY", "limit": "lots"}]})
    request = {
        "policy_input": pl.DataFrame({"state": ["TX"], "limit": [5]}),
        "items_input": pl.DataFrame({"row_number": [1], "item_id": ["Z"], "value": [7.0]}),
    }

    scored = _import(_write(graph, project)).pipeline.score(request)
    deployed = score_graph(
        graph=graph,
        input_df=_quote(
            {"policy": {"state": "TX", "limit": 5}, "items": [{"item_id": "Z", "value": 7.0}]}
        ),
        input_node_ids=["workbench_input"],
        output_node_id="listed",
    )

    assert scored["name"].to_list() == ["TX", "Z", "2.0"]
    assert deployed["name"].to_list() == ["TX", "Z", "2.0"]
    without = _pricing(project, NodeType.WORKBENCH_INPUT)
    assert infer_input_schema(graph, "workbench_input") == infer_input_schema(
        without, "workbench_input"
    )
    assert_frame_equal(
        _read_sample_row(graph, ["workbench_input"]), _read_sample_row(without, ["workbench_input"])
    )


def _quote(*quotes: dict[str, Any]) -> pl.DataFrame:
    """A request as a deployed pipeline builds it: its quotes as records."""
    return pl.DataFrame(list(quotes))


def test_a_deployed_pipeline_reads_the_request_into_the_tables(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    quote = {"policy": {"state": "TX", "limit": 5}, "items": [{"item_id": "Z", "value": 7.0}]}

    def deployed(request: DeployInput) -> pl.DataFrame:
        return score_graph(
            graph=graph,
            input_df=request,
            input_node_ids=["workbench_input"],
            output_node_id="listed",
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

    # The records as they were sent, which the container's /quote and deploy's test quotes
    # hand over: a quote need not hold every table, and a value Polars could not build a
    # frame from is refused by the reader, naming the spot.
    assert deployed(QuoteRequest((quote,)))["name"].to_list() == ["TX", "Z", "2.0"]
    only_policy = QuoteRequest(({"policy": {"state": "TX", "limit": 5}},))
    assert deployed(only_policy)["name"].to_list() == ["TX", "2.0"]
    for misfit, says in (
        (
            {"policy": {"state": "TX", "limit": 5}, "items": [{"value": 1}, {"value": "x"}]},
            "items[1].value is 'x', not 'float'",
        ),
        (
            {"policy": {"state": "TX", "limit": 5}, "items": [{"value": 1}, 5]},
            "items[1] is a value, not a row",
        ),
    ):
        with pytest.raises(
            WorkbenchInputError,
            match=re.escape(f"The request does not fit this Workbench Input's tables: {says}."),
        ):
            deployed(QuoteRequest((misfit,)))
    with pytest.raises(
        WorkbenchInputError, match="reads one quote per request, and this request holds 2"
    ):
        deployed(QuoteRequest((quote, quote)))
    # A Quote Input with tables reads the records as they were sent too, through its tables.
    quote_input = score_graph(
        graph=_pricing(project, NodeType.API_INPUT),
        input_df=QuoteRequest((quote,)),
        input_node_ids=["quote_input"],
        output_node_id="listed",
    )
    assert quote_input["name"].to_list() == ["TX", "Z", "2.0"]


def test_a_deployed_request_is_sized_per_table_for_the_memory_estimate(project: Path) -> None:
    """The estimate sees each port's rows, not the one-row request that holds them all."""
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    quote = {
        "policy": {"state": "TX", "limit": 5},
        "items": [{"item_id": item, "value": 1.0} for item in "ABC"],
    }
    sized: dict[str, Any] = {}
    real = _scorer.execute_lazy_graph

    def recording(graph_: PipelineGraph, builder: Any, **kwargs: Any) -> Any:
        sized.update(kwargs["runtime_source_frames_by_node"])
        return real(graph_, builder, **kwargs)

    with patch.object(_scorer, "execute_lazy_graph", recording):
        listed = score_graph(
            graph=graph,
            input_df=QuoteRequest((quote,)),
            input_node_ids=["workbench_input"],
            output_node_id="listed",
        )

    assert listed["name"].to_list() == ["TX", "A", "B", "C", "2.0"]
    assert {port: frame.height for port, frame in sized["workbench_input"].items()} == {
        "policy_input": 1,
        "items_input": 3,
    }


def test_the_containers_quote_route_reads_the_request_as_sent(project: Path) -> None:
    """A served quote answers 200 priced, and a misfit the structured 422, not Polars' 500.

    The graph is shipped as a deploy ships it, the sample typed while building left behind,
    and the request alone is priced."""
    from fastapi.testclient import TestClient

    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    [request] = [node for node in graph.nodes if node.id == "workbench_input"]
    request.data.config["sample"] = {"policy": {"state": "CA", "limit": 1}}
    # Its items keyed by their row_number, as a form's index keys them.
    items = request.data.config["tables"][1]
    items["columns"].insert(0, _column("row_number", "int"))
    items.update(key="row_number", null_key=1)
    container = project / "container"
    container.mkdir()
    manifest = {
        "pruned_graph": shipped_graph(graph),
        "input_node_ids": ["workbench_input"],
        "output_node_id": "listed",
        "request_rows": {"multi_quote": "in_request_order", "refusals": [], "checked_lookups": []},
        "artifacts": {},
        "output_fields": None,
    }
    (container / "deploy_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    app_path = container / "app.py"
    app_path.write_text(_generate_app_source("motor", 8080), encoding="utf-8")
    module_name = f"_haute_workbench_app_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, app_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        with TestClient(module.app) as client:
            priced = client.post(
                "/quote",
                json={
                    "policy": {"state": "TX", "limit": 5},
                    "items": [{"row_number": 1, "item_id": "Z", "value": 7.0}],
                },
            )
            # Rows whose outputs could not be put back on them: one holding no key, and two
            # holding one.
            unkeyed, twice = (
                client.post(
                    "/quote",
                    json={
                        "policy": {"state": "TX", "limit": 5},
                        "items": [
                            {"row_number": 1, "item_id": "Z", "value": 7.0},
                            {"row_number": number, "item_id": "Y", "value": 1.0},
                        ],
                    },
                )
                for number in (None, 1)
            )
            # A text where a number belongs, in a later row: Polars could not build a frame
            # from it, so it is read from the records and answered as the reader says it.
            refused = client.post(
                "/quote",
                json={
                    "policy": {"state": "TX", "limit": 5},
                    "items": [{"value": 1}, {"value": "x"}],
                },
            )
    finally:
        sys.modules.pop(module_name, None)

    assert priced.status_code == 200, priced.text
    assert [row["name"] for row in priced.json()["rows"]] == ["TX", "Z", "2.0"]
    assert refused.status_code == 422, refused.text
    assert refused.json() == {
        "error_code": "workbench_input_invalid",
        "message": "The request does not fit this Workbench Input's tables: items[1].value is "
        "'x', not 'float'.",
    }
    assert (unkeyed.status_code, twice.status_code) == (422, 422), (unkeyed.text, twice.text)
    assert unkeyed.json() == {
        "error_code": "workbench_input_invalid",
        "message": "The request does not fit this Workbench Input's tables: items's row 2 holds "
        "no row_number.",
    }
    assert twice.json() == {
        "error_code": "workbench_input_invalid",
        "message": "The request does not fit this Workbench Input's tables: items's rows 1 and "
        "2 hold row_number 1; each row's row_number is its own.",
    }


def test_generated_code_runs_a_workbench_input(project: Path) -> None:
    graph = _pricing(project, NodeType.WORKBENCH_INPUT)
    workbench_project(project, FORM_TABLES)

    # Its form has no sample: one row of nulls per table, the null quote.
    ran = _import(_write(graph, project)).pipeline.run(source="live")

    listed = ran.collect() if isinstance(ran, pl.LazyFrame) else ran
    assert listed["name"].to_list() == [None, None, "2.0"]

    # A submodel's file declares one with the decorator `Submodel` shares with `Pipeline`.
    main = _write(PipelineGraph(nodes=[_occurrence()], edges=[], submodels=_nested()), project)
    definition = (project / "modules" / "pricing.py").read_text(encoding="utf-8")
    assert "@submodel.workbench_input(" in definition
    nested = parse_pipeline_file(main).submodels["pricing"].graph.node_map["workbench_input"]
    assert nested.data.nodeType is NodeType.WORKBENCH_INPUT
    assert "tables" not in nested.data.config


# ---------------------------------------------------------------------------
# One request input per pipeline
# ---------------------------------------------------------------------------


def _request(nid: str, node_type: NodeType) -> GraphNode:
    """A request input as the editor holds it: a Workbench Input holds no tables."""
    if node_type is NodeType.WORKBENCH_INPUT:
        return _node(nid, node_type, {})
    return _node(
        nid, node_type, {"tables": copy.deepcopy(_QUOTE_TABLES[:1]), "path": "quotes.json"}
    )


def _nested() -> dict[str, SubmodelDefinition]:
    """A submodel definition holding a Workbench Input."""
    return {
        "pricing": SubmodelDefinition(
            definitionId="pricing",
            file="modules/pricing.py",
            graph=PipelineGraph(
                nodes=[_request("workbench_input", NodeType.WORKBENCH_INPUT)],
                edges=[],
                pipeline_name="pricing",
            ),
            inputPorts=[],
            outputPorts=[],
        )
    }


def _occurrence() -> GraphNode:
    return _node("pricing", NodeType.SUBMODEL, {"definition_id": "pricing", "alias": "pricing"})


def _two_request_inputs(case: str) -> PipelineGraph:
    quote = _request("quote_input", NodeType.API_INPUT)
    response = _node(
        "quote_response", NodeType.OUTPUT, make_output_config(["state"], source_port="policy")
    )
    to_response = _edge("quote_input", "quote_response", "policy")
    if case == "mixed":
        # The Workbench Input is off the response's path, so pruning would drop it.
        nodes = [quote, _request("workbench_input", NodeType.WORKBENCH_INPUT), response]
        return PipelineGraph(nodes=nodes, edges=[to_response])
    if case == "two_workbench":
        first = _request("first", NodeType.WORKBENCH_INPUT)
        second = _request("second", NodeType.WORKBENCH_INPUT)
        return PipelineGraph(
            nodes=[first, second, response],
            edges=[_edge("first", "quote_response", "policy")],
        )
    return PipelineGraph(
        nodes=[quote, response, _occurrence()], edges=[to_response], submodels=_nested()
    )


@pytest.mark.parametrize("case", ["mixed", "two_workbench", "nested"])
def test_a_pipeline_has_one_request_input(project: Path, case: str) -> None:
    graph = _two_request_inputs(case)

    with pytest.raises(RequestRefusedError) as refused:
        SavePipelineService(project).validate_graph(graph, source_file="main.py")
    assert refused.value.status_code == 400
    assert refused.value.detail == ONE_REQUEST_INPUT

    if case == "two_workbench":
        # Each is named by its type (READ-46), so a file cannot hold two: generating
        # one refuses a Workbench Input named otherwise before a deploy counts them.
        with pytest.raises(ConfigError, match="always named 'workbench_input'"):
            _write(graph, project, "main")
        return
    main = _write(graph, project, "main")
    with pytest.raises(ValueError, match=re.escape(ONE_REQUEST_INPUT)):
        resolve_config(DeployConfig(pipeline_file=main, model_name="m"))
