"""The executable-name rule (``haute._executable_names``) and every entry point applying it.

One table-driven test covers the rule; each entry point (save, codegen, the
strict parse, a standalone import, execution, the assistant, the document
capabilities) has a test proving a violation reaches the user through it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi import HTTPException

from haute._executable_names import (
    RESERVED_NAMES,
    executable_name_violations,
    format_name_violations,
)
from haute._types import GraphEdge, GraphNode, NodeData, PipelineGraph, SubmodelDefinition
from haute._user_exec import _exec_user_code
from haute.assistant._ops import OpValidationError, apply_ops, parse_ops
from haute.codegen import graph_to_code
from haute.errors import ExecutionError, ParseError
from haute.parser import parse_pipeline_source
from haute.routes._save_pipeline import SavePipelineService

# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def _node(node_id: str, label: str | None = None, node_type: str = "polars", **config: Any):
    return GraphNode(
        id=node_id,
        data=NodeData(label=label or node_id, nodeType=node_type, config=config),
    )


def _edge(source: str, target: str, source_handle: str | None = None) -> GraphEdge:
    return GraphEdge(
        id=f"{source}->{target}", source=source, target=target, sourceHandle=source_handle
    )


def _quote_input(node_id: str, *frames: str) -> GraphNode:
    tables = [
        {
            "path": "$[:]",
            "label": frame,
            "emit": True,
            "row_id_column": None,
            "columns": [
                {
                    "name": "quote_id",
                    "path": "$[:].quote_id",
                    "type": "str",
                    "status": "Confirmed",
                    "selected": True,
                    "levels": None,
                }
            ],
        }
        for frame in frames
    ]
    return _node(node_id, node_type="apiInput", path="quote.json", tables=tables)


def _graph(*nodes: GraphNode, edges: list[GraphEdge] | None = None, **kwargs: Any):
    return PipelineGraph(nodes=list(nodes), edges=edges or [], **kwargs)


def _with_submodel(
    *children: GraphNode,
    root: tuple[GraphNode, ...] = (),
    alias: str = "rates",
    input_ports: list[dict[str, Any]] | None = None,
) -> PipelineGraph:
    occurrence = _node(alias, node_type="submodel", definitionId="rates", alias=alias)
    definition = SubmodelDefinition(
        definitionId="rates",
        file="modules/rates.py",
        graph=PipelineGraph(nodes=list(children), edges=[], pipeline_name="rates"),
        inputPorts=input_ports or [],
        outputPorts=[],
    )
    return _graph(*root, occurrence, submodels={"rates": definition})


def _labels(*labels: str) -> PipelineGraph:
    return _graph(*(_node(f"n{index}", label) for index, label in enumerate(labels)))


def _consumes(source: GraphNode, consumer: GraphNode, handle: str | None = None):
    return _graph(source, consumer, edges=[_edge(source.id, consumer.id, handle)])


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

_RULE_CASES = [
    pytest.param(_labels("pl"), [("reserved", "pl")], id="node-pl"),
    pytest.param(_labels("pipeline"), [("reserved", "pipeline")], id="node-pipeline"),
    pytest.param(_labels("submodel"), [("reserved", "submodel")], id="node-submodel"),
    pytest.param(_labels("haute"), [("reserved", "haute")], id="node-haute"),
    pytest.param(
        _labels("global_constants"), [("reserved", "global_constants")], id="node-constants"
    ),
    pytest.param(_labels("max"), [("builtin", "max")], id="builtin-function"),
    pytest.param(_labels("filter"), [("builtin", "filter")], id="builtin-filter"),
    pytest.param(_labels("ValueError"), [("builtin", "ValueError")], id="builtin-exception"),
    pytest.param(_labels("Ellipsis"), [("builtin", "Ellipsis")], id="builtin-constant"),
    pytest.param(_labels("Max premium", "Filter", "PL"), [], id="near-misses-pass"),
    pytest.param(_labels("Claims", "claims"), [("duplicate", "Claims")], id="case-only"),
    pytest.param(_labels("my-node", "my_node"), [("duplicate", "my_node")], id="sanitized"),
    pytest.param(
        _with_submodel(_node("child", "Foo"), root=(_node("root", "foo"),)),
        [("duplicate", "foo")],
        id="root-vs-submodel-child",
    ),
    pytest.param(
        _with_submodel(_node("child", "rates")),
        [("duplicate", "rates")],
        id="alias-vs-own-child",
    ),
    pytest.param(
        _consumes(_quote_input("quotes", "pl"), _node("rated"), "pl"),
        [("reserved_input", "pl")],
        id="quote-input-frame",
    ),
    pytest.param(
        _consumes(_quote_input("quotes", "global_constants"), _node("rated"), "global_constants"),
        [("reserved_input", "global_constants")],
        id="quote-input-frame-constants",
    ),
    pytest.param(
        _consumes(_node("claims"), _node("rated", inputMapping={"pl": "claims"})),
        [("reserved_input", "pl")],
        id="input-mapping-alias",
    ),
    pytest.param(
        _consumes(
            _node("claims"), _node("copy", instanceOf="rated", inputMapping={"pl": "claims"})
        ),
        [("reserved_input", "pl")],
        id="instance-input-mapping",
    ),
    pytest.param(
        _with_submodel(
            _node("child"), input_ports=[{"name": "pl", "targets": [{"nodeId": "child"}]}]
        ),
        [("reserved_input", "pl")],
        id="submodel-port",
    ),
    # ``df`` keeps its own rule: refused only where node code reads it.
    pytest.param(_consumes(_node("df"), _node("rated")), [], id="input-df-has-its-own-rule"),
]


@pytest.mark.parametrize(("graph", "expected"), _RULE_CASES)
def test_the_rule(graph: PipelineGraph, expected: list[tuple[str, str]]) -> None:
    violations = executable_name_violations(graph)

    assert [(violation.kind, violation.name) for violation in violations] == expected


def test_a_case_collision_names_every_node_and_its_module() -> None:
    graph = _with_submodel(_node("child", "Claims"), root=(_node("root", "claims"),))

    (violation,) = executable_name_violations(graph)

    assert violation.node_ids == ("root", "child")
    assert violation.message() == (
        "Nodes 'claims' (the pipeline) and 'Claims' (submodel 'rates') take one name, "
        "`claims`; node names must differ by more than case across the pipeline and its "
        "submodels."
    )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("graph", "message"),
    [
        pytest.param(_labels("pl"), "Node 'pl' (the pipeline) takes the name `pl`", id="pl"),
        pytest.param(_labels("max"), "`max`, a Python built-in", id="max"),
        pytest.param(_labels("ValueError"), "`ValueError`, a Python built-in", id="ValueError"),
        pytest.param(_labels("Claims", "claims"), "take one name, `Claims`", id="case"),
        pytest.param(
            _consumes(_quote_input("quotes", "pl"), _node("rated"), "pl"),
            "Node 'rated' (the pipeline) receives an input named `pl` (frame 'pl' of 'quotes')",
            id="quote-input-table",
        ),
        pytest.param(
            _consumes(_node("claims"), _node("rated", inputMapping={"pl": "claims"})),
            "receives an input named `pl` (inputMapping alias for 'claims')",
            id="input-mapping",
        ),
        pytest.param(
            _with_submodel(
                _node("child"),
                input_ports=[{"name": "global_constants", "targets": [{"nodeId": "child"}]}],
            ),
            "receives an input named `global_constants` (submodel input port",
            id="submodel-port",
        ),
    ],
)
def test_save_refuses_a_violation_naming_the_node(
    tmp_path: Path, graph: PipelineGraph, message: str
) -> None:
    with pytest.raises(HTTPException) as excinfo:
        SavePipelineService(tmp_path).validate_graph(graph, source_file="main.py")

    assert excinfo.value.status_code == 400
    assert message in str(excinfo.value.detail)


def test_save_accepts_a_name_that_only_resembles_a_builtin(tmp_path: Path) -> None:
    SavePipelineService(tmp_path).validate_graph(
        _labels("Max premium", "Filter"), source_file="main.py"
    )


def test_codegen_refuses_a_reserved_node_name() -> None:
    with pytest.raises(ParseError, match="Node 'pipeline' \\(the pipeline\\) takes the name"):
        graph_to_code(_labels("pipeline"), pipeline_name="main")


_PARSED_PREAMBLE = 'import haute\nimport polars as pl\n\npipeline = haute.Pipeline("main")\n\n\n'


@pytest.mark.parametrize(
    ("functions", "message"),
    [
        pytest.param(["pl"], "takes the name `pl`, which the generated module binds", id="pl"),
        pytest.param(["max"], "takes the name `max`, a Python built-in", id="max"),
        pytest.param(["ValueError"], "`ValueError`, a Python built-in", id="ValueError"),
        pytest.param(["Claims", "claims"], "take one name, `Claims`", id="case"),
    ],
)
def test_a_strict_parse_refuses_a_violation(functions: list[str], message: str) -> None:
    source = _PARSED_PREAMBLE + "".join(
        f"@pipeline.polars\ndef {name}() -> pl.LazyFrame:\n"
        "    return pl.LazyFrame({'x': [1]})\n\n\n"
        for name in functions
    )

    with pytest.raises(ParseError, match=message):
        parse_pipeline_source(source)


def _import(source: str) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__name__": "pipeline_under_test"}
    exec(compile(source, "main.py", "exec"), namespace)
    return namespace


@pytest.mark.parametrize(
    ("name", "message"),
    [
        pytest.param("pl", "takes the name `pl`", id="pl"),
        pytest.param("pipeline", "takes the name `pipeline`", id="pipeline"),
        pytest.param("max", "`max`, a Python built-in", id="max"),
        pytest.param("ValueError", "`ValueError`, a Python built-in", id="ValueError"),
    ],
)
def test_a_standalone_import_refuses_a_reserved_or_builtin_node(name: str, message: str) -> None:
    source = (
        _PARSED_PREAMBLE + f"@pipeline.polars\ndef {name}() -> pl.LazyFrame:\n"
        "    return pl.LazyFrame({'x': [1]})\n"
    )

    with pytest.raises(ValueError, match=message):
        _import(source)


def test_a_standalone_import_refuses_a_node_named_like_a_preamble_helper() -> None:
    source = (
        _PARSED_PREAMBLE + "def rate_lookup(x):\n    return x\n\n\n"
        "@pipeline.polars\ndef rate_lookup() -> pl.LazyFrame:\n"
        "    return pl.LazyFrame({'x': [1]})\n"
    )

    with pytest.raises(ValueError, match="'rate_lookup' is already bound in this module"):
        _import(source)


def test_registering_an_already_defined_function_directly_is_accepted() -> None:
    namespace = _import(
        _PARSED_PREAMBLE + "def rows() -> pl.LazyFrame:\n"
        "    return pl.LazyFrame({'x': [1]})\n\n\n"
        "pipeline.polars(rows)\n"
    )

    assert [node.name for node in namespace["pipeline"].nodes] == ["rows"]


def test_a_standalone_import_refuses_names_differing_only_in_case() -> None:
    source = _PARSED_PREAMBLE + "".join(
        f"@pipeline.polars\ndef {name}() -> pl.LazyFrame:\n    return pl.LazyFrame()\n\n\n"
        for name in ("Claims", "claims")
    )

    with pytest.raises(ValueError, match="'claims' differs from the node 'Claims' only in case"):
        _import(source)


@pytest.mark.parametrize("name", ["pl", "global_constants"])
def test_execution_refuses_a_reserved_input(name: str) -> None:
    frame = pl.LazyFrame({"x": [1]})

    with pytest.raises(ExecutionError, match=f"An input is named `{name}`"):
        _exec_user_code("df = rows", [name], (frame,))


def test_execution_refuses_an_input_mapping_alias_that_is_reserved() -> None:
    frame = pl.LazyFrame({"x": [1]})

    with pytest.raises(ExecutionError, match="An input is named `pl`"):
        _exec_user_code(
            "df = claims", ["claims"], (frame,), orig_source_names=["pl"], input_mapping=None
        )


@pytest.mark.parametrize(
    ("op", "message"),
    [
        pytest.param(
            {"op": "rename_node", "node": "first", "new_name": "pl"}, "takes the name `pl`"
        ),
        pytest.param({"op": "rename_node", "node": "first", "new_name": "SECOND"}, "take one name"),
        pytest.param(
            {"op": "add_node", "name": "max", "node_type": "polars", "config": {}},
            "`max`, a Python built-in",
        ),
    ],
)
def test_the_assistant_refuses_a_name_the_rule_refuses(op: dict[str, Any], message: str) -> None:
    graph = _graph(_node("first"), _node("second"))

    with pytest.raises(OpValidationError, match=message):
        apply_ops(graph, parse_ops([op]))


def test_the_document_serves_the_reserved_input_names_as_reserved_frame_labels() -> None:
    from haute._pipeline_recovery import _capabilities

    capabilities = _capabilities("ready", source_selection_trusted=True)

    assert set(RESERVED_NAMES) <= set(capabilities.reserved_api_input_frame_labels)
    assert "class" in capabilities.reserved_api_input_frame_labels


def test_one_message_lists_every_violation() -> None:
    message = format_name_violations(executable_name_violations(_labels("pl", "max")))

    assert message.count("\n  - ") == 2
