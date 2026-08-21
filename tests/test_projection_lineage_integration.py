"""Integration regressions for column-lineage driven projection planning."""

from __future__ import annotations

import pytest

from haute._contracts import Contract
from haute._execution_context import ExecutionProfile
from haute._types import GraphEdge, GraphNode, NodeData, NodeType
from haute.projection import (
    AllExcept,
    BoundedDiagnosticCollection,
    DiagnosticDetailState,
    ProjectionEdgeKey,
    ProjectionPlan,
    ProjectionRequest,
    _analyse_polars_node_lineage,
    _declared_api_input_port_columns,
    _EdgeIdentityMapping,
    _edges_by_endpoint,
    _exact_registered_contract_output,
    _lineage_input_bindings,
    _projection_edges,
    api_input_port_columns_by_node,
    compute_prepared_plan,
    explain,
    plan,
    prepare_graph,
    with_api_input_port_projection_boundaries,
    with_runtime_inferred_streaming_edges,
)
from tests._projection_helpers import edge_keys_for_pair
from tests.conftest import make_graph, make_output_config


def _api_node() -> dict:
    return {
        "id": "api",
        "data": {
            "label": "api",
            "nodeType": "apiInput",
            "config": {
                "tables": [
                    {
                        "label": "left",
                        "emit": True,
                        "columns": [
                            {"name": "id", "selected": True},
                            {"name": "left_value", "selected": True},
                            {"name": "left_unused", "selected": True},
                        ],
                    },
                    {
                        "label": "right",
                        "emit": True,
                        "columns": [
                            {"name": "id", "selected": True},
                            {"name": "right_value", "selected": True},
                            {"name": "right_unused", "selected": True},
                        ],
                    },
                    {
                        "label": "rows",
                        "emit": True,
                        "columns": [
                            {"name": "a", "selected": True},
                            {"name": "sort_key", "selected": True},
                            {"name": "unused", "selected": True},
                        ],
                    },
                ]
            },
        },
    }


def _polars_node(node_id: str, code: str) -> dict:
    return {
        "id": node_id,
        "data": {"label": node_id, "nodeType": "polars", "config": {"code": code}},
    }


def test_api_ports_sharing_a_source_node_keep_independent_join_edge_demands() -> None:
    graph = make_graph(
        {
            "nodes": [
                _api_node(),
                _polars_node(
                    "joined", "df = left.join(right, on='id').select(['left_value', 'right_value'])"
                ),
            ],
            "edges": [
                {"id": "e_left", "source": "api", "target": "joined", "sourceHandle": "left"},
                {"id": "e_right", "source": "api", "target": "joined", "sourceHandle": "right"},
            ],
        }
    )
    prepared = prepare_graph(graph, "joined")
    projection = plan(
        ProjectionRequest(
            graph=graph, target_node_id="joined", profile=ExecutionProfile.PREVIEW_EAGER
        )
    )
    left_edge, right_edge = graph.edges

    assert projection.demand_for_edge(left_edge) == frozenset({"id", "left_value"})
    assert projection.demand_for_edge(right_edge) == frozenset({"id", "right_value"})
    # Parallel ports between one node pair keep two distinct complete keys;
    # a lossy pair can never address either demand.
    assert len(edge_keys_for_pair(projection.edge_demands, "api", "joined")) == 2
    assert len(projection.diagnostics.to_dict()["edge_reasons"]) == 2
    assert api_input_port_columns_by_node(
        prepared.node_map, prepared.relevant_edges, projection
    ) == {
        "api": {"left": frozenset({"id", "left_value"}), "right": frozenset({"id", "right_value"})}
    }


def test_unseeded_terminal_select_narrows_its_api_port() -> None:
    graph = make_graph(
        {
            "nodes": [_api_node(), _polars_node("selected", "df = rows.select(['a'])")],
            "edges": [
                {"id": "e_rows", "source": "api", "target": "selected", "sourceHandle": "rows"}
            ],
        }
    )
    prepared = prepare_graph(graph, "selected")
    projection = plan(
        ProjectionRequest(
            graph=graph, target_node_id="selected", profile=ExecutionProfile.PREVIEW_EAGER
        )
    )

    assert api_input_port_columns_by_node(
        prepared.node_map, prepared.relevant_edges, projection
    ) == {"api": {"rows": frozenset({"a"})}}


def test_terminal_modelling_identity_uses_the_known_input_schema() -> None:
    graph = make_graph(
        {
            "nodes": [
                _api_node(),
                {
                    "id": "training",
                    "data": {
                        "label": "training",
                        "nodeType": "modelling",
                        "config": {"contract": {"inputs": [], "outputs": []}},
                    },
                },
            ],
            "edges": [
                {
                    "id": "e_rows",
                    "source": "api",
                    "target": "training",
                    "sourceHandle": "rows",
                }
            ],
        }
    )
    prepared = prepare_graph(graph, "training")
    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="training",
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )
    expected = frozenset({"a", "sort_key", "unused"})

    assert projection.needed_by_node["training"] == expected
    assert projection.demand_for_edge(graph.edges[0]) == expected
    assert projection.opaque_boundaries == frozenset()
    assert api_input_port_columns_by_node(
        prepared.node_map,
        prepared.relevant_edges,
        projection,
    ) == {"api": {"rows": expected}}


def test_declared_contract_cannot_manufacture_an_exact_user_code_schema() -> None:
    graph = make_graph(
        {
            "nodes": [
                _api_node(),
                {
                    "id": "opaque",
                    "data": {
                        "label": "opaque",
                        "nodeType": "polars",
                        "config": {
                            "code": "if enabled:\n    df = rows.select(['a'])",
                            "contract": {"inputs": [], "outputs": []},
                        },
                    },
                },
            ],
            "edges": [
                {
                    "id": "e_rows",
                    "source": "api",
                    "target": "opaque",
                    "sourceHandle": "rows",
                }
            ],
        }
    )
    projection = plan(
        ProjectionRequest(
            graph=graph,
            target_node_id="opaque",
            profile=ExecutionProfile.PREVIEW_EAGER,
        )
    )

    assert projection.needed_by_node["opaque"] is None
    assert "opaque" in projection.opaque_boundaries


def test_sort_then_select_keeps_sort_key_in_api_port_demand() -> None:
    graph = make_graph(
        {
            "nodes": [
                _api_node(),
                _polars_node("selected", "df = rows.sort('sort_key').select(['a'])"),
            ],
            "edges": [
                {"id": "e_rows", "source": "api", "target": "selected", "sourceHandle": "rows"}
            ],
        }
    )
    prepared = prepare_graph(graph, "selected")
    projection = plan(
        ProjectionRequest(
            graph=graph, target_node_id="selected", profile=ExecutionProfile.PREVIEW_EAGER
        )
    )

    assert api_input_port_columns_by_node(
        prepared.node_map, prepared.relevant_edges, projection
    ) == {"api": {"rows": frozenset({"a", "sort_key"})}}


def test_exact_api_schema_makes_literal_rename_projectable() -> None:
    graph = make_graph(
        {
            "nodes": [
                _api_node(),
                _polars_node(
                    "renamed",
                    "df = rows.rename({'a': 'value'}).select(['value'])",
                ),
            ],
            "edges": [
                {"id": "e_rows", "source": "api", "target": "renamed", "sourceHandle": "rows"}
            ],
        }
    )
    prepared = prepare_graph(graph, "renamed")
    projection = plan(
        ProjectionRequest(
            graph=graph, target_node_id="renamed", profile=ExecutionProfile.PREVIEW_EAGER
        )
    )

    assert api_input_port_columns_by_node(
        prepared.node_map, prepared.relevant_edges, projection
    ) == {"api": {"rows": frozenset({"a"})}}


def test_parent_id_contract_cannot_conflate_parallel_api_port_edges() -> None:
    graph = make_graph(
        {
            "nodes": [
                _api_node(),
                {
                    "id": "contracted",
                    "data": {
                        "label": "contracted",
                        "nodeType": "polars",
                        "config": {
                            "code": "df = left.join(right, on='id')",
                            "contract": {
                                "inputs": ["id", "left_value"],
                                "outputs": [],
                            },
                        },
                    },
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["left_value"]),
                    },
                },
            ],
            "edges": [
                {"id": "e_left", "source": "api", "target": "contracted", "sourceHandle": "left"},
                {"id": "e_right", "source": "api", "target": "contracted", "sourceHandle": "right"},
                {"id": "e_out", "source": "contracted", "target": "out"},
            ],
        }
    )
    prepared = prepare_graph(graph, "out")
    projection = plan(
        ProjectionRequest(graph=graph, target_node_id="out", profile=ExecutionProfile.LAZY_SINK)
    )

    assert api_input_port_columns_by_node(
        prepared.node_map, prepared.relevant_edges, projection
    ) == {"api": {"left": None, "right": None}}
    assert projection.reason_for_edge(graph.edges[0]).rule == "parallel_edge_contract_ambiguous"
    assert projection.reason_for_edge(graph.edges[1]).rule == "parallel_edge_contract_ambiguous"


def test_runtime_inference_does_not_hide_an_opaque_fan_out_sibling() -> None:
    graph = make_graph(
        {
            "nodes": [
                {"id": "source", "data": {"label": "source", "nodeType": "dataInput"}},
                _polars_node("first", "df = source.select(['a'])"),
                _polars_node("opaque", "if enabled:\n    df = source.select(['b'])"),
            ],
            "edges": [
                {"id": "e_first", "source": "source", "target": "first"},
                {"id": "e_opaque", "source": "source", "target": "opaque"},
            ],
        }
    )
    projection = ProjectionPlan(
        needed_by_node={"source": None},
        edge_demands={},
        opaque_boundaries=frozenset({"source"}),
    )
    first_key = ProjectionEdgeKey.from_edge(graph.edges[0])

    refined = with_runtime_inferred_streaming_edges(
        projection,
        demands_by_edge={first_key: {"a"}},
        resolved_parent_ids={"source"},
        relevant_edges=graph.edges,
    )

    assert refined.edge_demands[first_key] == frozenset({"a"})
    assert refined.needed_by_node["source"] is None
    assert "source" in refined.opaque_boundaries


@pytest.mark.parametrize("retain_one_by", ["", 1])
def test_bounded_diagnostics_reject_invalid_grouping_field(retain_one_by: object) -> None:
    with pytest.raises(ValueError, match="retain_one_by"):
        BoundedDiagnosticCollection.from_items(
            [],
            cap=1,
            sort_key="reasons",
            retain_one_by=retain_one_by,  # type: ignore[arg-type]
        )


def test_bounded_diagnostics_require_group_field_and_capacity() -> None:
    with pytest.raises(ValueError, match="missing grouping field"):
        BoundedDiagnosticCollection.from_items(
            [
                {"node_id": "first", "reason_code": "a"},
                {"reason_code": "b"},
            ],
            cap=1,
            sort_key="reasons",
            retain_one_by="node_id",
        )

    with pytest.raises(ValueError, match="smaller than the number"):
        BoundedDiagnosticCollection.from_items(
            [
                {"node_id": "first", "reason_code": "a"},
                {"node_id": "second", "reason_code": "b"},
            ],
            cap=1,
            sort_key="reasons",
            retain_one_by="node_id",
        )


def test_bounded_diagnostics_retain_every_group_then_fill_capacity() -> None:
    collection = BoundedDiagnosticCollection.from_items(
        [
            {"node_id": "first", "reason_code": "a"},
            {"node_id": "first", "reason_code": "b"},
            {"node_id": "second", "reason_code": "c"},
            {"node_id": "second", "reason_code": "d"},
        ],
        cap=3,
        sort_key="reasons",
        retain_one_by="node_id",
    )

    assert collection.state is DiagnosticDetailState.TRUNCATED
    assert collection.total_count == 4
    assert len(collection.items) == 3
    assert {item["node_id"] for item in collection.items} == {"first", "second"}


def test_edge_identity_mapping_accepts_only_complete_edge_keys() -> None:
    with pytest.raises(TypeError, match="complete edge keys"):
        _EdgeIdentityMapping({1: "value"})
    with pytest.raises(TypeError, match="complete edge keys"):
        _EdgeIdentityMapping({("source", "target"): "value"})

    key = ProjectionEdgeKey(edge_id="e_source_target", source="source", target="target")
    mapping = _EdgeIdentityMapping({key: "value"})
    assert mapping[key] == "value"
    with pytest.raises(KeyError):
        mapping[("source", "target")]  # type: ignore[index]
    with pytest.raises(KeyError):
        mapping["invalid"]  # type: ignore[index]


def _typed_node(
    node_id: str,
    node_type: NodeType,
    *,
    label: str | None = None,
    config: dict | None = None,
) -> GraphNode:
    return GraphNode(
        id=node_id,
        data=NodeData(
            label=label or node_id,
            nodeType=node_type,
            config=config or {},
        ),
    )


@pytest.mark.parametrize(
    "node",
    [
        _typed_node("not_api", NodeType.DATA_INPUT, config={"tables": []}),
        _typed_node("bad_table", NodeType.API_INPUT, config={"tables": [1]}),
        _typed_node(
            "bad_columns",
            NodeType.API_INPUT,
            config={"tables": [{"label": "rows", "emit": True, "columns": "bad"}]},
        ),
        _typed_node(
            "bad_column",
            NodeType.API_INPUT,
            config={"tables": [{"label": "rows", "emit": True, "columns": [1]}]},
        ),
        _typed_node(
            "not_selected",
            NodeType.API_INPUT,
            config={
                "tables": [
                    {
                        "label": "rows",
                        "emit": True,
                        "columns": [{"name": "a", "selected": False}],
                    }
                ]
            },
        ),
        _typed_node(
            "bad_name",
            NodeType.API_INPUT,
            config={
                "tables": [
                    {
                        "label": "rows",
                        "emit": True,
                        "columns": [{"name": "", "selected": True}],
                    }
                ]
            },
        ),
        _typed_node(
            "not_emitting",
            NodeType.API_INPUT,
            config={
                "tables": [
                    {
                        "label": "rows",
                        "emit": False,
                        "columns": [{"name": "a", "selected": True}],
                    }
                ]
            },
        ),
        _typed_node(
            "duplicate_label",
            NodeType.API_INPUT,
            config={
                "tables": [
                    {
                        "label": "rows",
                        "emit": True,
                        "columns": [{"name": "a", "selected": True}],
                    },
                    {
                        "label": "rows",
                        "emit": True,
                        "columns": [{"name": "b", "selected": True}],
                    },
                ]
            },
        ),
    ],
)
def test_declared_api_port_schema_fails_closed_for_noncanonical_config(
    node: GraphNode,
) -> None:
    assert _declared_api_input_port_columns(node) is None


def test_unavailable_api_schema_disables_every_edge_from_that_source() -> None:
    source = _typed_node(
        "api",
        NodeType.API_INPUT,
        config={"tables": [{"label": "rows", "emit": True, "columns": "bad"}]},
    )
    first = _typed_node("first", NodeType.LIVE_SWITCH)
    second = _typed_node("second", NodeType.LIVE_SWITCH)
    edges = [
        GraphEdge(id="e_first", source="api", target="first", sourceHandle="rows"),
        GraphEdge(id="e_second", source="api", target="second", sourceHandle="rows"),
    ]

    assert (
        api_input_port_columns_by_node(
            {node.id: node for node in (source, first, second)},
            edges,
            ProjectionPlan(needed_by_node={}, edge_demands={}),
        )
        == {}
    )


def test_one_invalid_api_edge_disables_later_valid_edges_from_the_source() -> None:
    source = _typed_node(
        "api",
        NodeType.API_INPUT,
        config={
            "tables": [
                {
                    "label": "rows",
                    "emit": True,
                    "columns": [{"name": "a", "selected": True}],
                }
            ]
        },
    )
    first = _typed_node("first", NodeType.LIVE_SWITCH)
    second = _typed_node("second", NodeType.LIVE_SWITCH)
    edges = [
        GraphEdge(id="e_invalid", source="api", target="first"),
        GraphEdge(id="e_valid", source="api", target="second", sourceHandle="rows"),
    ]

    assert (
        api_input_port_columns_by_node(
            {node.id: node for node in (source, first, second)},
            edges,
            ProjectionPlan(needed_by_node={}, edge_demands={}),
        )
        == {}
    )


def test_api_projection_boundaries_are_added_only_for_unproven_ports() -> None:
    source = _typed_node(
        "api",
        NodeType.API_INPUT,
        config={
            "tables": [
                {
                    "label": "rows",
                    "emit": True,
                    "columns": [{"name": "a", "selected": True}],
                }
            ]
        },
    )
    target = _typed_node("target", NodeType.LIVE_SWITCH)
    edge = GraphEdge(id="e_rows", source="api", target="target", sourceHandle="rows")
    key = ProjectionEdgeKey.from_edge(edge)
    proven = ProjectionPlan(
        needed_by_node={"api": frozenset({"a"})},
        edge_demands={key: frozenset({"a"})},
    )
    nodes = {"api": source, "target": target}

    assert with_api_input_port_projection_boundaries(proven, nodes, [edge]) is proven

    unproven = ProjectionPlan(
        needed_by_node={"api": frozenset({"a"})},
        edge_demands={},
    )
    bounded = with_api_input_port_projection_boundaries(unproven, nodes, [edge])
    assert bounded.needed_by_node["api"] is None
    assert "api" in bounded.opaque_boundaries
    assert bounded.diagnostics.node_reasons["api"].rule == "unprojected_streaming_boundary"


def test_legacy_projection_edges_are_filtered_and_uniquely_ordinalled() -> None:
    edges = _projection_edges(
        ["source", "target"],
        {"source": ["missing", "target", "target"]},
        None,
    )

    assert [edge.id for edge in edges] == ["e_source_target", "e_source_target_1"]
    incoming, outgoing = _edges_by_endpoint(
        ["target"],
        [GraphEdge(id="outside", source="outside", target="target")],
    )
    assert incoming == {"target": []}
    assert outgoing == {"target": []}


def _lineage_fixture(
    *,
    input_mapping: object | None = None,
) -> tuple[GraphNode, list[GraphEdge], dict[str, GraphNode]]:
    left = _typed_node("left", NodeType.DATA_INPUT, label="left")
    right = _typed_node("right", NodeType.DATA_INPUT, label="right")
    config: dict = {"code": "df = left.select('a')"}
    if input_mapping is not None:
        config["inputMapping"] = input_mapping
    target = _typed_node("target", NodeType.POLARS, config=config)
    edges = [
        GraphEdge(id="e_left", source="left", target="target"),
        GraphEdge(id="e_right", source="right", target="target"),
    ]
    return target, edges, {node.id: node for node in (left, right, target)}


def test_lineage_bindings_fail_closed_for_unnameable_or_duplicate_edges() -> None:
    target, _edges, node_map = _lineage_fixture()
    missing_source = GraphEdge(id="missing", source="missing", target="target")
    assert _lineage_input_bindings(target, [missing_source], node_map, {}) is None

    api = _typed_node("api", NodeType.API_INPUT, label="api")
    api_edge = GraphEdge(id="api_edge", source="api", target="target")
    assert (
        _lineage_input_bindings(
            target,
            [api_edge],
            {**node_map, "api": api},
            {},
        )
        is None
    )

    duplicate_left = _typed_node("duplicate_left", NodeType.DATA_INPUT, label="same")
    duplicate_right = _typed_node("duplicate_right", NodeType.DATA_INPUT, label="same")
    duplicate_edges = [
        GraphEdge(id="one", source="duplicate_left", target="target"),
        GraphEdge(id="two", source="duplicate_right", target="target"),
    ]
    assert (
        _lineage_input_bindings(
            target,
            duplicate_edges,
            {
                "target": target,
                "duplicate_left": duplicate_left,
                "duplicate_right": duplicate_right,
            },
            {},
        )
        is None
    )


@pytest.mark.parametrize(
    "input_mapping",
    [
        ["bad"],
        {"": "left"},
        {"alias": 1},
        {"alias": "missing"},
        {"left": "right"},
    ],
)
def test_lineage_bindings_reject_invalid_input_mappings(input_mapping: object) -> None:
    target, edges, node_map = _lineage_fixture(input_mapping=input_mapping)
    assert _lineage_input_bindings(target, edges, node_map, {}) is None


def test_lineage_bindings_add_a_valid_logical_input_alias() -> None:
    target, edges, node_map = _lineage_fixture(input_mapping={"alias": "left"})
    bindings = _lineage_input_bindings(target, edges, node_map, {})

    assert bindings is not None
    assert {binding.name for binding in bindings} == {"left", "right", "alias"}
    alias = next(binding for binding in bindings if binding.name == "alias")
    assert alias.key == ProjectionEdgeKey.from_edge(edges[0])


def test_polars_lineage_skips_blank_code_and_nodes_without_inputs() -> None:
    blank = _typed_node("blank", NodeType.POLARS, config={"code": " "})
    assert (
        _analyse_polars_node_lineage(
            blank,
            [],
            {"blank": blank},
            {},
            None,
            Contract.opaque(),
        )
        is None
    )

    no_inputs = _typed_node(
        "no_inputs",
        NodeType.POLARS,
        config={"code": "df = pl.DataFrame({'a': [1]})"},
    )
    assert (
        _analyse_polars_node_lineage(
            no_inputs,
            [],
            {"no_inputs": no_inputs},
            {},
            None,
            Contract.opaque(),
        )
        is None
    )


def test_registered_contract_output_requires_every_referenced_input() -> None:
    assert (
        _exact_registered_contract_output(
            Contract(
                inputs=frozenset({"missing"}),
                outputs=frozenset({"derived"}),
            ),
            frozenset({"available"}),
        )
        is None
    )


def test_schema_all_except_seed_unions_with_concrete_downstream_demand() -> None:
    graph = make_graph(
        {
            "nodes": [
                _api_node(),
                {
                    "id": "training",
                    "data": {"label": "training", "nodeType": "modelling", "config": {}},
                },
                {
                    "id": "out",
                    "data": {
                        "label": "out",
                        "nodeType": "output",
                        "config": make_output_config(["sort_key"]),
                    },
                },
            ],
            "edges": [
                {
                    "id": "e_rows",
                    "source": "api",
                    "target": "training",
                    "sourceHandle": "rows",
                },
                {"id": "e_out", "source": "training", "target": "out"},
            ],
        }
    )
    prepared = prepare_graph(graph, "out")
    projection = compute_prepared_plan(
        prepared.order,
        {node_id: [] for node_id in prepared.order}
        | {
            "api": ["training"],
            "training": ["out"],
        },
        prepared.node_map,
        required_columns_by_node={
            "training": AllExcept(
                required_columns=frozenset({"a"}),
                excluded_columns=frozenset({"unused"}),
            )
        },
        relevant_edges=prepared.relevant_edges,
    )

    assert projection.needed_by_node["training"] == frozenset({"a", "sort_key"})
    assert projection.demand_for_edge(graph.edges[0]) == frozenset({"a", "sort_key"})


def test_terminal_schema_all_except_seed_resolves_against_exact_input() -> None:
    graph = make_graph(
        {
            "nodes": [
                _api_node(),
                {
                    "id": "training",
                    "data": {"label": "training", "nodeType": "modelling", "config": {}},
                },
            ],
            "edges": [
                {
                    "id": "e_rows",
                    "source": "api",
                    "target": "training",
                    "sourceHandle": "rows",
                }
            ],
        }
    )
    prepared = prepare_graph(graph, "training")
    projection = compute_prepared_plan(
        prepared.order,
        {"api": ["training"], "training": []},
        prepared.node_map,
        required_columns_by_node={
            "training": AllExcept(
                required_columns=frozenset({"a"}),
                excluded_columns=frozenset({"unused"}),
            )
        },
        relevant_edges=prepared.relevant_edges,
    )

    assert projection.needed_by_node["training"] == frozenset({"a", "sort_key"})


def test_schema_all_except_seed_without_exact_schema_stays_full_width() -> None:
    training = _typed_node("training", NodeType.MODELLING)
    projection = compute_prepared_plan(
        ["training"],
        {"training": []},
        {"training": training},
        required_columns_by_node={
            "training": AllExcept(
                required_columns=frozenset({"a"}),
                excluded_columns=frozenset({"unused"}),
            )
        },
    )

    assert projection.needed_by_node["training"] is None


def test_schema_all_except_seed_does_not_narrow_an_opaque_child() -> None:
    graph = make_graph(
        {
            "nodes": [
                _api_node(),
                {
                    "id": "training",
                    "data": {"label": "training", "nodeType": "modelling", "config": {}},
                },
                {
                    "id": "opaque",
                    "data": {
                        "label": "opaque",
                        "nodeType": "polars",
                        "config": {"code": "if enabled:\n    df = training.select(['a'])"},
                    },
                },
            ],
            "edges": [
                {
                    "id": "e_rows",
                    "source": "api",
                    "target": "training",
                    "sourceHandle": "rows",
                },
                {"id": "e_opaque", "source": "training", "target": "opaque"},
            ],
        }
    )
    prepared = prepare_graph(graph, "opaque")
    projection = compute_prepared_plan(
        prepared.order,
        {"api": ["training"], "training": ["opaque"], "opaque": []},
        prepared.node_map,
        required_columns_by_node={
            "training": AllExcept(
                required_columns=frozenset({"a"}),
                excluded_columns=frozenset({"unused"}),
            )
        },
        relevant_edges=prepared.relevant_edges,
    )

    complete_schema = frozenset({"a", "sort_key", "unused"})
    assert projection.needed_by_node["training"] == complete_schema
    assert projection.demand_for_edge(graph.edges[0]) == complete_schema


def test_runtime_inference_without_new_demands_is_an_identity() -> None:
    projection = ProjectionPlan(
        needed_by_node={"source": None},
        edge_demands={},
        opaque_boundaries=frozenset({"source"}),
    )

    assert (
        with_runtime_inferred_streaming_edges(
            projection,
            demands_by_edge={},
        )
        is projection
    )


def test_projection_explain_handles_empty_node_and_edge_collections() -> None:
    edge_key = ProjectionEdgeKey(edge_id="e_source_target", source="source", target="target")
    edge_only = ProjectionPlan(
        needed_by_node={},
        edge_demands={edge_key: frozenset({"a"})},
    )
    assert explain(edge_only) == ("source -> target: edge_demand: edge demand [a]",)

    node_only = ProjectionPlan(
        needed_by_node={"source": frozenset({"a"})},
        edge_demands={},
    )
    assert explain(node_only) == ("source: projection_demand: projection demand [a]",)
