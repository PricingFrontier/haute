"""Capstone codegen/parser round-trip properties.

The stable artifact invariant is source-focused:

* ``parse(codegen(g))`` preserves the graph semantics that the generated
  Python file can represent.
* ``codegen(parse(codegen(g)))`` is byte-identical to the generated Python
  files from the first pass.

Sidecar JSON bytes are intentionally outside the byte-identical assertion.
The source is the user-edited artifact; sidecars are normalized storage. In
particular, generated ``contract="opaque"`` annotations can make a parsed
graph's next sidecar write include an explicit contract that was absent from a
first-save GUI graph. This file compares source bytes and semantic config
values after normalization, not sidecar formatting.

Known W5 tensions intentionally scoped here:

* dataSource first-save non-idempotence with opaque contracts: this suite uses
  explicit ``contract="opaque"`` in capstone fixtures and asserts source
  idempotence, not sidecar byte idempotence.
* scaffold/docstring observations: generated scaffolding is not user semantic
  code, but pipeline names and node docstrings/descriptions are. The
  comparator asserts pipeline names and descriptions exactly, so module-header
  and function-docstring injection classes are covered by the corpus/property.
* submodel path interpolation: submodel container nodes are explicitly budgeted
  out of this root-decorator property. Adversarial submodel *paths* with
  quotes/backslashes remain a known raw interpolation surface in
  ``pipeline.submodel("{path}")``; fuzzing that path would be a production bug
  report, not a harness fallback.
* Tier-3 ``_parse_decorator_kwargs_regex`` policy: these properties exercise
  generated AST-valid artifacts. Regex fallback policy for manually corrupted
  files is intentionally left to the parser fallback tests.
"""

from __future__ import annotations

import ast
import json
import tempfile
from pathlib import Path
from typing import Any

import hypothesis.strategies as st
from hypothesis import HealthCheck, given, settings

from haute._banding_config import expand_banding_config_from_sidecar
from haute._config_io import collect_node_configs
from haute._graph_utils import _resolve_sink_path, _sanitize_func_name
from haute._rating_step_config import expand_rating_step_config_from_sidecar
from haute._types import GraphEdge, GraphNode, NodeData, NodeType, PipelineGraph
from haute.codegen import graph_to_code_multi
from haute.parser import parse_pipeline_file
from tests.conftest import make_output_config

ROUNDTRIPPABLE_NODE_TYPES: frozenset[NodeType] = frozenset(NodeType) - {
    # These are structural containers in graph_to_code_multi, not standalone
    # decorators that should dispatch through _node_to_code.
    NodeType.SUBMODEL,
    NodeType.SUBMODEL_PORT,
}

ADVERSARIAL_TEXTS: tuple[str, ...] = (
    "",
    "quote ' and double \"",
    'triple """ quote',
    r"C:\tmp\pricing\file.parquet",
    "braces {rating} and {{already}}",
    "line one\n  indented line two\nline three",
    "unicode cafe\u0301 \u6771\u4eac \u0394",
    "paren text (gross) and stray )",
    "-leading-dash",
)


def _node(
    node_id: str,
    label: str,
    node_type: NodeType,
    config: dict[str, Any],
    *,
    description: str = "",
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(
            label=label,
            description=description,
            nodeType=node_type,
            config=config,
        ),
    )


def _edge(
    source: str,
    target: str,
    *,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> GraphEdge:
    suffix = "__".join(
        part
        for part in (
            source,
            source_handle or "",
            target,
            target_handle or "",
        )
        if part
    )
    return GraphEdge(
        id=f"e__{suffix}",
        source=source,
        target=target,
        sourceHandle=source_handle,
        targetHandle=target_handle,
    )


def _quoted_literal(value: str) -> str:
    return repr(value)


def _c5_chain_user_code(value: str) -> str:
    """User code shaped to catch the C5 chain/paren extraction class."""
    literal = _quoted_literal(value)
    return (
        "df = (\n"
        "    df\n"
        f'    .with_columns(pl.lit({literal}).alias("adversarial_note"))\n'
        "    .filter(pl.lit(True))\n"
        ")\n"
        f'df = df.with_columns(pl.lit({literal}).alias("brace_doc_paren_guard"))'
    )


def _simple_user_code(value: str) -> str:
    literal = _quoted_literal(value)
    return f'df = df.with_columns(pl.lit({literal}).alias("post_process_note"))'


def _opaque(config: dict[str, Any]) -> dict[str, Any]:
    return {**config, "contract": "opaque"}


def _capstone_root_graph(
    *,
    pipeline_name: str = "capstone_roundtrip",
    description: str,
    user_text: str,
    handle_text: str,
) -> PipelineGraph:
    """A corpus-scale graph that exercises every root round-trippable type.

    Coverage notes:

    * C1: raw frontend ids differ from sanitized labels for edgeJoin roles.
    * C5: transform and dataSource code boxes use multiline chain assignment.
    * Brace/docstring: descriptions and config strings include braces and
      triple quotes.
    * Paren scanner: decorator strings include ``(`` and ``)`` before contract
      injection runs.
    """
    left = "ui:left-source:7"
    api = "ui/api-input:8"
    join = "ui edge join 9"
    transform = "ui-transform-c5"
    score = "ui-score"
    band = "ui-band"
    rating = "ui-rating"
    scenario = "ui-scenario"
    optimiser = "ui-optimiser"
    apply = "ui-apply"
    modelling = "ui-modelling"
    const = "ui-constant"
    output = "ui-output"
    sink = "ui-sink"
    explore = "ui-explore"
    external = "ui-external"
    switch = "ui-switch"

    nodes = [
        _node(
            left,
            'Left Source "{cafe}"',
            NodeType.DATA_SOURCE,
            _opaque(
                {
                    "path": r"data\raw {quotes}\left (gross).parquet",
                    "sourceType": "flat_file",
                    "schema": {"premium (gross)": "Float64"},
                    "code": _c5_chain_user_code(user_text),
                }
            ),
            description=description,
        ),
        _node(
            api,
            "API Input \u6771\u4eac",
            NodeType.API_INPUT,
            _opaque(
                {
                    "path": "inputs/request (v2).json",
                    "tables": [
                        {
                            "path": "$[:]",
                            "label": "quotes",
                            "emit": True,
                            "columns": [
                                {
                                    "name": "quote_id",
                                    "path": "$[:].quote_id",
                                    "type": "str",
                                    "selected": True,
                                },
                                {
                                    "name": "premium (gross)",
                                    "path": "$[:]['premium (gross)']",
                                    "type": "float",
                                    "selected": True,
                                },
                            ],
                        }
                    ],
                }
            ),
            description="API " + description,
        ),
        _node(
            const,
            "Constant {Brace}",
            NodeType.CONSTANT,
            _opaque(
                {
                    "values": [
                        {"name": "literal_brace", "value": "{value}"},
                        {"name": "unicode_value", "value": "\u0394"},
                    ]
                }
            ),
            description='constant """ doc ' + description,
        ),
        _node(
            join,
            "Edge Join C1",
            NodeType.EDGE_JOIN,
            _opaque(
                {
                    "baseInput": left,
                    "joinInput": api,
                    "how": "left",
                    "leftOn": ["quote_id"],
                    "rightOn": ["quote_id"],
                    "suffix": "_joined",
                    "coalesce": True,
                    "validate": "m:1",
                    "maintainOrder": "left",
                }
            ),
            description="C1 raw id remap " + description,
        ),
        _node(
            transform,
            "Transform C5 (paren)",
            NodeType.POLARS,
            _opaque(
                {
                    "selected_columns": ["premium (gross)", "brace {field}"],
                    "code": _c5_chain_user_code(user_text),
                }
            ),
            description="C5 chain " + description,
        ),
        _node(
            score,
            "Model Score",
            NodeType.MODEL_SCORE,
            _opaque(
                {
                    "sourceType": "run",
                    "run_id": "run-{abc}(1)",
                    "run_name": 'best "run"',
                    "artifact_path": r"models\score {v1}.cbm",
                    "task": "regression",
                    "output_column": "prediction (gross)",
                    "code": _simple_user_code(user_text),
                }
            ),
            description="score " + description,
        ),
        _node(
            band,
            "Banding Node",
            NodeType.BANDING,
            _opaque(
                {
                    "factors": [
                        {
                            "banding": "continuous",
                            "column": "prediction (gross)",
                            "outputColumn": "score_band",
                            "rules": [
                                {
                                    "op1": ">=",
                                    "val1": "0",
                                    "op2": "<",
                                    "val2": "100",
                                    "assignment": "{low}",
                                }
                            ],
                            "default": "other",
                        }
                    ]
                }
            ),
            description="band " + description,
        ),
        _node(
            rating,
            "Rating Step",
            NodeType.RATING_STEP,
            _opaque(
                {
                    "tables": [
                        {
                            "name": "Table {A}",
                            "factors": ["score_band"],
                            "outputColumn": "rate_factor",
                            "defaultValue": "1.0",
                            "entries": [{"score_band": "{low}", "value": "1.25"}],
                        }
                    ],
                    "operation": "multiply",
                    "combinedColumn": "rated premium",
                    "code": _simple_user_code(user_text),
                }
            ),
            description="rating " + description,
        ),
        _node(
            scenario,
            "Scenario Expander",
            NodeType.SCENARIO_EXPANDER,
            _opaque(
                {
                    "quote_id": "quote_id",
                    "column_name": "scenario (value)",
                    "min_value": 0.0,
                    "max_value": 1.0,
                    "steps": 3,
                    "step_column": "step_index",
                    "code": _simple_user_code(user_text),
                }
            ),
            description="scenario " + description,
        ),
        _node(
            optimiser,
            "Optimiser Node",
            NodeType.OPTIMISER,
            _opaque(
                {
                    "mode": "online",
                    "quote_id": "quote_id",
                    "scenario_index": "step_index",
                    "scenario_value": "scenario (value)",
                    "objective": "profit {gross}",
                    "constraints": {"loss_ratio": {"max": 0.65}},
                    "max_iter": 7,
                    "tolerance": 0.001,
                    "chunk_size": 128,
                    "record_history": False,
                }
            ),
            description="optimiser " + description,
        ),
        _node(
            apply,
            "Apply Optimiser",
            NodeType.OPTIMISER_APPLY,
            _opaque(
                {
                    "sourceType": "file",
                    "artifact_path": r"artifacts\optimiser {v1}.json",
                    "version_column": "__optimiser_version__",
                    "optimised_value_column": "selected (price)",
                }
            ),
            description="apply " + description,
        ),
        _node(
            modelling,
            "Modelling Node",
            NodeType.MODELLING,
            _opaque(
                {
                    "name": "glm {frequency}",
                    "target": "claim_count",
                    "algorithm": "glm",
                    "task": "regression",
                    "metrics": ["rmse", "mae"],
                    "feature_columns": ["premium (gross)", "rate_factor"],
                }
            ),
            description="modelling " + description,
        ),
        _node(
            output,
            "Output Node",
            NodeType.OUTPUT,
            _opaque(make_output_config(["premium (gross)", "brace {field}", "quote_id"])),
            description="output " + description,
        ),
        _node(
            sink,
            "Sink Node",
            NodeType.DATA_SINK,
            _opaque({"path": "outputs/final (gross).csv", "format": "csv"}),
            description="sink " + description,
        ),
        _node(
            explore,
            "Explore Node",
            NodeType.EXPLORE,
            _opaque(
                {
                    "overview": {
                        "dataset_snapshot": True,
                        "data_quality": True,
                        "numeric_summary": False,
                        "categorical_summary": True,
                        "schema": True,
                    },
                    "code": _simple_user_code(user_text),
                }
            ),
            description="explore " + description,
        ),
        _node(
            external,
            "External File",
            NodeType.EXTERNAL_FILE,
            _opaque(
                {
                    "path": r"models\external {quote}.pkl",
                    "fileType": "pickle",
                    "code": _simple_user_code(user_text),
                }
            ),
            description="external " + description,
        ),
        _node(
            "ui-data-input",
            "Data Input {Wide}",
            NodeType.DATA_INPUT,
            _opaque(
                {
                    "format": "csv",
                    "mode": "scan",
                    "path": r"data\wide {quotes}\input (raw).csv",
                    "arguments": {
                        "separator": ";",
                        "schema_overrides": {
                            "quote_id": "int64",
                            "nested {col}": {"type": "List", "inner": "str"},
                        },
                    },
                }
            ),
            description="data input " + description,
        ),
        _node(
            "ui-data-output",
            "Data Output {Wide}",
            NodeType.DATA_OUTPUT,
            _opaque(
                {
                    "format": "ndjson",
                    "mode": "sink",
                    "path": "outputs/wide (result).jsonl",
                    "arguments": {},
                }
            ),
            description="data output " + description,
        ),
        _node(
            switch,
            "Live Switch",
            NodeType.LIVE_SWITCH,
            _opaque(
                {
                    "input_scenario_map": {
                        _sanitize_func_name('Left Source "{cafe}"'): "live",
                        "quotes": "test_batch",
                    },
                    "inputs": [
                        _sanitize_func_name('Left Source "{cafe}"'),
                        "quotes",
                    ],
                }
            ),
            description="switch " + description,
        ),
    ]

    edges = [
        _edge(left, join, source_handle=f"left output {handle_text}", target_handle="base"),
        _edge(api, join, source_handle="quotes", target_handle="join"),
        _edge(const, transform, source_handle="constant {port}"),
        _edge(join, transform),
        _edge(transform, score),
        _edge(score, band),
        _edge(band, rating),
        _edge(rating, scenario),
        _edge(scenario, optimiser),
        _edge(optimiser, apply),
        _edge(apply, modelling),
        _edge(transform, output),
        _edge(transform, sink),
        _edge("ui-data-input", "ui-data-output"),
        _edge(transform, explore),
        _edge(transform, external),
        _edge(left, switch, source_handle="live (left)"),
        _edge(api, switch, source_handle="quotes"),
    ]

    return PipelineGraph(
        nodes=nodes,
        edges=edges,
        pipeline_name=pipeline_name,
        pipeline_description=description,
    )


def _write_configs_recursive(graph: PipelineGraph, base_dir: Path) -> None:
    configs = dict(collect_node_configs(graph))
    for meta in (graph.submodels or {}).values():
        if not isinstance(meta, dict) or "graph" not in meta:
            continue
        child_graph = PipelineGraph.model_validate(meta["graph"])
        configs.update(collect_node_configs(child_graph))

    for rel_path, content in configs.items():
        path = base_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _roundtrip(graph: PipelineGraph) -> tuple[dict[str, str], PipelineGraph, dict[str, str]]:
    with tempfile.TemporaryDirectory() as td:
        base_dir = Path(td)
        pipeline_name = graph.pipeline_name or "capstone_roundtrip"
        first = graph_to_code_multi(graph, pipeline_name=pipeline_name, source_file="main.py")

        for rel_path, content in first.items():
            path = base_dir / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        _write_configs_recursive(graph, base_dir)

        parsed = parse_pipeline_file(base_dir / "main.py")
        second = graph_to_code_multi(
            parsed,
            pipeline_name=parsed.pipeline_name or pipeline_name,
            source_file="main.py",
        )
    return first, parsed, second


def _submodel_child_graphs(graph: PipelineGraph) -> dict[str, PipelineGraph]:
    result: dict[str, PipelineGraph] = {}
    for name, meta in (graph.submodels or {}).items():
        if not isinstance(meta, dict) or "graph" not in meta:
            continue
        result[name] = PipelineGraph.model_validate(meta["graph"])
    return result


def _node_id_remap(graph: PipelineGraph) -> dict[str, str]:
    remap = {node.id: _sanitize_func_name(node.data.label) for node in graph.nodes}
    for child in _submodel_child_graphs(graph).values():
        remap.update({node.id: _sanitize_func_name(node.data.label) for node in child.nodes})
    return remap


_NODE_REFERENCE_CONFIG_FIELDS: dict[NodeType, frozenset[str]] = {
    # EdgeJoin role fields store graph node ids in GUI state but generated
    # source can only represent function names. Remapping these two fields is
    # the C1 contract; every other string config value is user data unless it
    # is explicitly added here with a test.
    NodeType.EDGE_JOIN: frozenset({"baseInput", "joinInput"}),
}


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonical_value(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical_value(v) for v in value]
    return value


def _canonical_config(node_type: NodeType, config: dict[str, Any], remap: dict[str, str]) -> Any:
    normalized = dict(config)
    if normalized.get("code") == "":
        normalized.pop("code")
    if node_type == NodeType.DATA_SINK and "path" in normalized:
        normalized["path"] = _resolve_sink_path(
            str(normalized.get("path") or ""),
            str(normalized.get("format") or "parquet"),
        )
    if node_type == NodeType.BANDING:
        normalized = expand_banding_config_from_sidecar(normalized)
    if node_type == NodeType.RATING_STEP:
        normalized = expand_rating_step_config_from_sidecar(normalized)
    for field in _NODE_REFERENCE_CONFIG_FIELDS.get(node_type, frozenset()):
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = remap.get(value, value)
    return _canonical_value(normalized)


def _semantic_nodes(graph: PipelineGraph) -> dict[tuple[str, str], tuple[str, str, Any]]:
    child_graphs = _submodel_child_graphs(graph)
    child_ids = {node.id for child in child_graphs.values() for node in child.nodes}
    placeholder_ids = {f"submodel__{name}" for name in child_graphs}
    remap = _node_id_remap(graph)
    result: dict[tuple[str, str], tuple[str, str, Any]] = {}

    for node in graph.nodes:
        if node.id in child_ids or node.id in placeholder_ids:
            continue
        key = ("", _sanitize_func_name(node.data.label))
        result[key] = (
            node.data.nodeType.value,
            node.data.description,
            _canonical_config(node.data.nodeType, node.data.config, remap),
        )

    for submodel_name, child in child_graphs.items():
        child_remap = _node_id_remap(child)
        for node in child.nodes:
            key = (submodel_name, _sanitize_func_name(node.data.label))
            result[key] = (
                node.data.nodeType.value,
                node.data.description,
                _canonical_config(node.data.nodeType, node.data.config, child_remap),
            )
    return result


def _edge_key(
    edge: GraphEdge,
    remap: dict[str, str],
    *,
    scope: str = "",
) -> tuple[str, str, str | None, str | None, str]:
    return (
        remap.get(edge.source, edge.source),
        remap.get(edge.target, edge.target),
        edge.sourceHandle,
        edge.targetHandle,
        scope,
    )


def _semantic_edges(graph: PipelineGraph) -> set[tuple[str, str, str | None, str | None, str]]:
    child_graphs = _submodel_child_graphs(graph)
    remap = _node_id_remap(graph)
    result: set[tuple[str, str, str | None, str | None, str]] = set()
    placeholder_to_children = {
        f"submodel__{name}": {node.id for node in child.nodes}
        for name, child in child_graphs.items()
    }

    for edge in graph.edges:
        source = edge.source
        target = edge.target
        source_handle = edge.sourceHandle
        target_handle = edge.targetHandle

        if source in placeholder_to_children:
            if source_handle is None or not source_handle.startswith("out__"):
                continue
            source = source_handle.removeprefix("out__")
            source_handle = None
        if target in placeholder_to_children:
            if target_handle is None or not target_handle.startswith("in__"):
                continue
            target = target_handle.removeprefix("in__")
            target_handle = None

        result.add(
            (
                remap.get(source, source),
                remap.get(target, target),
                source_handle,
                target_handle,
                "",
            )
        )

    for submodel_name, child in child_graphs.items():
        child_remap = _node_id_remap(child)
        for edge in child.edges:
            result.add(_edge_key(edge, child_remap, scope=submodel_name))
    return result


def _assert_semantically_equal(original: PipelineGraph, parsed: PipelineGraph) -> None:
    assert parsed.pipeline_name == original.pipeline_name
    assert parsed.pipeline_description == original.pipeline_description
    assert _semantic_nodes(parsed) == _semantic_nodes(original)
    assert _semantic_edges(parsed) == _semantic_edges(original)


def _assert_roundtrip_invariants(graph: PipelineGraph) -> None:
    first, parsed, second = _roundtrip(graph)
    _assert_semantically_equal(graph, parsed)
    assert second == first


def _corpus_graphs() -> list[PipelineGraph]:
    return [
        _capstone_root_graph(
            pipeline_name='capstone """ name {braces} \\ \u6771\u4eac',
            description='pipeline """ doc with {braces}\nC:\\tmp\\x and unicode \u6771\u4eac',
            user_text='value """ {brace} (paren) \\ backslash \u0394',
            handle_text="(v2) {brace}",
        )
    ]


def test_corpus_graphs_cover_all_supported_node_types() -> None:
    graphs = _corpus_graphs()

    covered = {
        node.data.nodeType
        for graph in graphs
        for node in graph.nodes
        if node.data.nodeType not in {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}
    }
    for graph in graphs:
        for child in _submodel_child_graphs(graph).values():
            covered.update(node.data.nodeType for node in child.nodes)

    assert covered == ROUNDTRIPPABLE_NODE_TYPES


def test_corpus_roundtrip_semantics_and_source_bytes() -> None:
    for graph in _corpus_graphs():
        _assert_roundtrip_invariants(graph)


def test_frame_named_api_parameters_are_a_byte_identical_roundtrip_fixpoint() -> None:
    api = _node(
        "api-source",
        "Request Bundle",
        NodeType.API_INPUT,
        _opaque(
            {
                "path": "inputs/request.json",
                "tables": [
                    {
                        "path": "$[:]",
                        "label": "quotes",
                        "emit": True,
                        "columns": [
                            {
                                "name": "quote_id",
                                "path": "$[:].quote_id",
                                "type": "int",
                                "selected": True,
                            }
                        ],
                    },
                    {
                        "path": "$[:].drivers[:]",
                        "label": "drivers",
                        "emit": True,
                        "columns": [
                            {
                                "name": "quote_id",
                                "path": "$[:].quote_id",
                                "type": "int",
                                "selected": True,
                            },
                            {
                                "name": "driver_id",
                                "path": "$[:].drivers[:].driver_id",
                                "type": "int",
                                "selected": True,
                            },
                        ],
                    },
                ],
            }
        ),
    )
    merge = _node(
        "merge",
        "Merge Frames",
        NodeType.POLARS,
        _opaque({"code": "df = quotes.join(drivers, on='quote_id', how='left')"}),
    )
    graph = PipelineGraph(
        nodes=[api, merge],
        edges=[
            _edge("api-source", "merge", source_handle="quotes"),
            _edge("api-source", "merge", source_handle="drivers"),
        ],
        pipeline_name="frame_named_roundtrip",
        pipeline_description="",
    )

    first, parsed, second = _roundtrip(graph)

    _assert_semantically_equal(graph, parsed)
    assert second == first
    generated = ast.parse(first["main.py"])
    merge_fn = next(
        node
        for node in generated.body
        if isinstance(node, ast.FunctionDef) and node.name == "Merge_Frames"
    )
    assert [arg.arg for arg in merge_fn.args.args] == ["quotes", "drivers"]


def test_submodel_container_types_are_explicitly_budgeted() -> None:
    """Submodel containers are multi-file structure, not root decorators.

    A RED probe while building this capstone found the current hierarchical
    submodel parser can add a fallback root edge and drops user-facing
    cross-boundary source handles while rewiring to ``in__`` / ``out__``
    handles. That is a product limitation to fix before submodel containers
    can join this byte-idempotence property.
    """
    assert NodeType.SUBMODEL not in ROUNDTRIPPABLE_NODE_TYPES
    assert NodeType.SUBMODEL_PORT not in ROUNDTRIPPABLE_NODE_TYPES


def test_non_reference_config_literals_are_not_remapped() -> None:
    """Only documented node-reference fields may rewrite raw ids to func names.

    Review regression for W5.8: a comparator that remaps every string through
    ``node.id -> sanitized(label)`` can hide a real bug where user data equal
    to a raw node id is rewritten as though it were a graph reference.
    """
    remap = {"raw-node-id": "Literal_Owner"}
    original = _canonical_config(
        NodeType.CONSTANT,
        {"values": [{"name": "literal", "value": "raw-node-id"}]},
        remap,
    )
    rewritten = _canonical_config(
        NodeType.CONSTANT,
        {"values": [{"name": "literal", "value": "Literal_Owner"}]},
        remap,
    )
    assert original != rewritten


def test_edge_join_reference_fields_are_remapped_for_c1() -> None:
    """C1 role ids are the narrow, intentional exception to exact strings."""
    remap = {"ui:left-source:7": "Left_Source_7", "ui/right-source:8": "Right_Source_8"}
    assert _canonical_config(
        NodeType.EDGE_JOIN,
        {
            "baseInput": "ui:left-source:7",
            "joinInput": "ui/right-source:8",
            "suffix": "ui:left-source:7",
        },
        remap,
    ) == _canonical_config(
        NodeType.EDGE_JOIN,
        {
            "baseInput": "Left_Source_7",
            "joinInput": "Right_Source_8",
            "suffix": "ui:left-source:7",
        },
        remap,
    )


_adversarial_text = st.sampled_from(ADVERSARIAL_TEXTS)
_adversarial_nonempty_text = st.sampled_from(tuple(text for text in ADVERSARIAL_TEXTS if text))


@given(
    pipeline_name=_adversarial_nonempty_text,
    description=_adversarial_text,
    user_text=_adversarial_text,
    handle_text=_adversarial_text,
)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_hypothesis_roundtrip_semantics_and_source_bytes(
    pipeline_name: str,
    description: str,
    user_text: str,
    handle_text: str,
) -> None:
    graph = _capstone_root_graph(
        pipeline_name=pipeline_name,
        description=description,
        user_text=user_text,
        handle_text=handle_text,
    )
    _assert_roundtrip_invariants(graph)


def test_generated_config_sidecars_are_valid_json() -> None:
    """The harness writes real sidecars, so assert corpus configs are valid JSON."""
    graph = _capstone_root_graph(
        description=ADVERSARIAL_TEXTS[5],
        user_text=ADVERSARIAL_TEXTS[2],
        handle_text=ADVERSARIAL_TEXTS[7],
    )
    for content in collect_node_configs(graph).values():
        loaded = json.loads(content)
        assert isinstance(loaded, dict)
