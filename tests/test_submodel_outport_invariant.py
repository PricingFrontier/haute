"""Canonical submodel public-output boundary invariants."""

from __future__ import annotations

from haute._flatten import flatten_graph
from haute._submodel_instances import qualified_runtime_node_id
from haute._types import PipelineGraph
from haute.codegen import graph_to_code, graph_to_code_multi


def _canonical_graph() -> PipelineGraph:
    return PipelineGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "src",
                    "data": {
                        "label": "Source",
                        "nodeType": "dataInput",
                        "config": {"path": "d.parquet"},
                    },
                },
                {
                    "id": "pricing-instance",
                    "type": "submodel",
                    "data": {
                        "label": "Pricing",
                        "nodeType": "submodel",
                        "config": {
                            "definitionId": "pricing-definition",
                            "alias": "pricing",
                        },
                    },
                },
                {
                    "id": "down",
                    "data": {"label": "Down", "nodeType": "polars", "config": {}},
                },
            ],
            "edges": [
                {
                    "id": "e_in",
                    "source": "src",
                    "target": "pricing-instance",
                    "targetHandle": "in__input_1",
                },
                {
                    "id": "e_out",
                    "source": "pricing-instance",
                    "target": "down",
                    "sourceHandle": "out__output_1",
                },
            ],
            "submodels": {
                "pricing-definition": {
                    "definitionId": "pricing-definition",
                    "file": "modules/pricing.py",
                    "inputPorts": [
                        {
                            "portId": "input_1",
                            "label": "Source",
                            "targets": [{"nodeId": "child", "handleId": None}],
                        }
                    ],
                    "outputPorts": [
                        {
                            "portId": "output_1",
                            "label": "Child",
                            "source": {"nodeId": "child", "handleId": "frame"},
                        }
                    ],
                    "graph": {
                        "nodes": [
                            {
                                "id": "child",
                                "data": {
                                    "label": "Child",
                                    "nodeType": "polars",
                                    "config": {},
                                },
                            }
                        ],
                        "edges": [],
                    },
                }
            },
        }
    )


def test_flatten_resolves_public_output_to_qualified_definition_endpoint() -> None:
    result = flatten_graph(_canonical_graph())
    runtime_child = qualified_runtime_node_id("pricing-instance", "child")
    rewired = [
        edge for edge in result.edges if edge.source == runtime_child and edge.target == "down"
    ]

    assert len(rewired) == 1
    assert rewired[0].sourceHandle == "frame"
    assert rewired[0].sourcePort is None
    assert result.submodels is None


def test_codegen_uses_public_output_port_without_leaking_child_identity() -> None:
    files = graph_to_code_multi(_canonical_graph(), pipeline_name="main")
    main_code = files["main.py"]

    assert 'pipeline.connect("pricing", "Down", source_port="output_1")' in main_code
    assert "out__output_1" not in main_code
    assert "child" not in main_code
    assert 'definition_id="pricing-definition"' in files["modules/pricing.py"]


def test_regular_frame_named_like_boundary_handle_is_preserved() -> None:
    graph = PipelineGraph.model_validate(
        {
            "nodes": [
                {
                    "id": "a",
                    "data": {
                        "label": "NodeA",
                        "nodeType": "polars",
                        "config": {"code": "df = df"},
                    },
                },
                {
                    "id": "b",
                    "data": {
                        "label": "NodeB",
                        "nodeType": "polars",
                        "config": {"code": "df = df"},
                    },
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "a",
                    "target": "b",
                    "sourceHandle": "out__claims",
                }
            ],
        }
    )

    code = graph_to_code(graph, pipeline_name="main")
    assert 'pipeline.connect("NodeA", "NodeB", source_port="out__claims")' in code
