"""A message names a node by its label, and a submodel child by its label and its
occurrence's ("Banding 1 in pricing"), never by the key the flat graph runs it under
(specs/server-api/low-level.md, "Node addresses", item 7)."""

from __future__ import annotations

from typing import Any

import pytest

from haute._flatten import flatten_graph
from haute._node_addresses import node_display_name, node_reference
from haute._submodel_instances import qualified_runtime_node_id
from haute._types import NodeType, PipelineGraph
from haute.errors import RequestRefusedError
from haute.routes._helpers import find_typed_node
from tests._preview_targets import node_target
from tests._project import project_app
from tests.test_node_addresses import _pipeline
from tests.test_submodel_instances import _reuse_graph


@pytest.fixture
def client() -> Any:
    from fastapi.testclient import TestClient

    return TestClient(project_app())


@pytest.fixture
def flat() -> PipelineGraph:
    return flatten_graph(_pipeline())


def test_a_node_is_named_by_its_label_and_a_child_with_its_occurrence(
    flat: PipelineGraph,
) -> None:
    copy_rate = qualified_runtime_node_id("pricing_2", "rate")

    assert node_display_name(flat, "upstream") == "Upstream"
    # A copy's child runs under a flat name of its own; it is named as authored.
    assert flat.node_map[copy_rate].data.label != "Rate"
    assert node_display_name(flat, copy_rate) == "Rate in pricing_2"
    assert node_reference(flat, copy_rate) == {
        "key": copy_rate,
        "occurrence": "pricing_2",
        "node_id": "rate",
        "label": "Rate",
    }
    # A key the graph does not hold is named as it is, so the message still reads.
    assert node_display_name(flat, "gone") == "gone"


def test_a_refusal_of_the_wrong_node_type_names_the_child_by_label(flat: PipelineGraph) -> None:
    with pytest.raises(RequestRefusedError) as refused:
        find_typed_node(
            flat, qualified_runtime_node_id("pricing_2", "rate"), NodeType.MODELLING, "modelling"
        )

    assert refused.value.status_code == 400
    assert refused.value.detail == "'Rate in pricing_2' is not a modelling node (got polars)"


def _broken_copy() -> PipelineGraph:
    """The reuse pipeline with its definition's first child broken."""
    graph = _reuse_graph()
    assert graph.submodels is not None
    scale = graph.submodels["definition_scoring"].graph.node_map["scale"]
    scale.data.config["code"] = "df = policy.with_columns(pl.col('missing'))"
    return graph


def test_an_upstream_failure_names_the_failed_child_by_label_and_occurrence(client: Any) -> None:
    preview = client.post(
        "/api/pipeline/preview",
        json={
            "graph": _broken_copy().model_dump(mode="json", by_alias=True),
            "target": node_target("finish", occurrence="rating_ie"),
        },
    ).json()

    assert preview["status"] == "error"
    assert preview["error"].startswith("Upstream node(s) failed: scale in rating_ie: ")
    assert "submodel_runtime" not in preview["error"]
    assert "rating_ie__scale" not in preview["error"]


def test_a_trace_of_a_failing_child_names_it_and_carries_its_reference(client: Any) -> None:
    graph = _broken_copy()
    target = node_target("scale", occurrence="rating_ie")
    traced = client.post(
        "/api/pipeline/trace",
        json={
            "graph": graph.model_dump(mode="json", by_alias=True),
            "target": target,
            "row_index": 0,
            "seed_plan": [],
        },
    )

    assert traced.status_code == 422, traced.text
    detail = traced.json()["detail"]
    key = qualified_runtime_node_id("rating_ie", "scale")
    assert detail["error_code"] == "trace_node_failed"
    assert detail["message"].startswith("'scale in rating_ie' failed: ")
    assert detail["node"] == {
        "key": key,
        "occurrence": "rating_ie",
        "node_id": "scale",
        "label": "scale",
    }
