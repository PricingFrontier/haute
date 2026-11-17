"""A cached snapshot records the node it was written for, and the cached-data list names
it by that record (specs/server-api/low-level.md, "Node addresses"; specs/caching).

The pipeline holds an owner occurrence ``pricing`` and its copy ``pricing_2``. Inside
each, two Data Inputs feed a join, which a band reads; previewing the copy's band captures
the copy's join (labelled `joined`), and building the band's data publishes the band.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from haute._node_snapshots import NodeSnapshotOwner, NodeSnapshotStore
from haute._submodel_instances import qualified_runtime_node_id
from haute._types import (
    GraphEdge,
    GraphNode,
    NodeData,
    NodeType,
    PipelineGraph,
    SubmodelDefinition,
    SubmodelEndpoint,
    SubmodelOutputPort,
)
from haute.schemas import CacheNodesResponse
from tests._preview_targets import node_target
from tests.test_node_address_contracts import _addresses_every_key
from tests.test_preview_snapshot_seeding import _code, _parquet, api, project

__all__ = ["api", "project"]


def _node(
    node_id: str, node_type: NodeType, config: dict[str, Any], label: str | None = None
) -> GraphNode:
    return GraphNode(
        id=node_id,
        type="submodel" if node_type == NodeType.SUBMODEL else "pipelineNode",
        data=NodeData(label=label or node_id, nodeType=node_type, config=config),
    )


def _occurrence(instance_id: str, *, instance_of: str | None = None) -> GraphNode:
    config: dict[str, Any] = {"definition_id": "scoring", "alias": instance_id}
    if instance_of is not None:
        config["instance_of"] = instance_of
    return _node(instance_id, NodeType.SUBMODEL, config)


def _graph(project: Path, *, with_copy: bool = True) -> PipelineGraph:
    definition = SubmodelDefinition(
        definition_id="scoring",
        file="modules/scoring.py",
        graph=PipelineGraph(
            nodes=[
                _node("policies", NodeType.DATA_INPUT, _parquet(project / "policies.parquet")),
                _node("claims", NodeType.DATA_INPUT, _parquet(project / "claims.parquet")),
                _node(
                    "join",
                    NodeType.POLARS,
                    _code("df = policies.join(claims, on='id', how='left')"),
                    label="joined",
                ),
                _node(
                    "band",
                    NodeType.POLARS,
                    _code("df = joined.with_columns((pl.col('a') * 2).alias('band'))"),
                    label="Band",
                ),
            ],
            edges=[
                GraphEdge(id="p", source="policies", target="join"),
                GraphEdge(id="c", source="claims", target="join"),
                GraphEdge(id="j", source="join", target="band"),
            ],
        ),
        input_ports=[],
        output_ports=[SubmodelOutputPort(name="banded", source=SubmodelEndpoint(node_id="band"))],
    )
    nodes = [_occurrence("pricing")]
    if with_copy:
        nodes.append(_occurrence("pricing_2", instance_of="pricing"))
    return PipelineGraph(
        nodes=nodes,
        edges=[],
        preamble="import polars as pl",
        source_file=str(project / "main.py"),
        submodels={"scoring": definition},
    )


def _owner(project: Path, occurrence: str, node_id: str) -> NodeSnapshotOwner | None:
    key = qualified_runtime_node_id(occurrence, node_id)
    owners = [o for o in NodeSnapshotStore(project).inventory().owners if o.node_id == key]
    assert owners, f"nothing is cached for {key}"
    return owners[0].owner


def _raw(graph: PipelineGraph) -> dict[str, Any]:
    return graph.model_dump(mode="json", by_alias=True)


def test_a_capture_records_the_copys_child_by_address_and_authored_label(
    project: Path, api: Any
) -> None:
    graph = _graph(project)
    preview = api.post(
        "/api/pipeline/preview",
        json={"graph": _raw(graph), "target": node_target("band", occurrence="pricing_2")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "ok", preview.text

    assert _owner(project, "pricing_2", "join") == NodeSnapshotOwner(
        occurrence="pricing_2", occurrence_label="pricing_2", node_id="join", label="joined"
    )


def test_an_explicit_build_records_the_copys_child_by_address_and_authored_label(
    project: Path, api: Any
) -> None:
    graph = _graph(project)
    built = api.post(
        "/api/node-data/run",
        json={
            "graph": _raw(graph),
            "target": {"occurrence": "pricing_2", "node_id": "band"},
            "refresh": True,
        },
    )
    assert built.status_code == 200, built.text
    job_id = built.json()["job_id"]
    if job_id is not None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = api.get(f"/api/node-data/status/{job_id}").json()
            if status["status"] not in {"pending", "running", "queued"}:
                break
            time.sleep(0.05)
        assert status["status"] == "completed", status

    assert _owner(project, "pricing_2", "band") == NodeSnapshotOwner(
        occurrence="pricing_2", occurrence_label="pricing_2", node_id="band", label="Band"
    )


@pytest.mark.parametrize("deleted", [False, True], ids=["copy kept", "copy deleted"])
def test_the_cached_data_list_names_nodes_by_address_and_recorded_owner(
    project: Path, api: Any, deleted: bool
) -> None:
    preview = api.post(
        "/api/pipeline/preview",
        json={
            "graph": _raw(_graph(project)),
            "target": node_target("band", occurrence="pricing_2"),
        },
    )
    assert preview.status_code == 200, preview.text

    # The copy and its occurrence may be gone since: its data is listed by what it recorded.
    graph = _graph(project, with_copy=not deleted)
    listed = api.post("/api/cache/nodes", json={"graph": _raw(graph), "source": "live"})
    assert listed.status_code == 200, listed.text
    answer = listed.json()
    _addresses_every_key(CacheNodesResponse, graph, answer)

    copy_join = qualified_runtime_node_id("pricing_2", "join")
    if not deleted:
        row = next(row for row in answer["nodes"] if row["node_id"] == copy_join)
        assert row["size_bytes"] > 0
        assert answer["node_addresses"][copy_join] == {
            "occurrence": "pricing_2",
            "node_id": "join",
            "label": "joined",
        }
        return
    other = [entry for entry in answer["other"] if entry["bucket"] == "node_output"]
    assert other == [
        {
            "bucket": "node_output",
            "label": "joined",
            "pipeline": "main.py",
            "owner": {
                "occurrence": "pricing_2",
                "occurrence_label": "pricing_2",
                "node_id": "join",
                "label": "joined",
            },
            "source": "live",
            "generations": other[0]["generations"],
            "row_count": other[0]["row_count"],
            "size_bytes": other[0]["size_bytes"],
            "newest_created_at": other[0]["newest_created_at"],
            "build_seconds": other[0]["build_seconds"],
            "identity_digests": other[0]["identity_digests"],
        }
    ]


@pytest.mark.parametrize(
    "record",
    [None, {"occurrence": "pricing_2", "node_id": "", "label": "join"}],
    ids=["no-owner", "an-unreadable-owner"],
)
def test_data_published_before_owners_were_recorded_is_listed_by_its_slot_key(
    project: Path, api: Any, record: dict[str, Any] | None
) -> None:
    preview = api.post(
        "/api/pipeline/preview",
        json={
            "graph": _raw(_graph(project)),
            "target": node_target("band", occurrence="pricing_2"),
        },
    )
    assert preview.status_code == 200, preview.text
    # The generation as it was written before owners were recorded (no node_output.owner),
    # or with an owner record that cannot be read, which names no node either.
    stripped = 0
    for meta in project.rglob("meta.json"):
        raw = json.loads(meta.read_text(encoding="utf-8"))
        node_output = raw.get("node_output")
        if isinstance(node_output, dict) and node_output.pop("owner", None) is not None:
            if record is not None:
                node_output["owner"] = record
            meta.write_text(json.dumps(raw), encoding="utf-8")
            stripped += 1
    assert stripped

    copy_join = qualified_runtime_node_id("pricing_2", "join")
    assert _owner(project, "pricing_2", "join") is None
    listed = api.post(
        "/api/cache/nodes",
        json={"graph": _raw(_graph(project, with_copy=False)), "source": "live"},
    )
    assert listed.status_code == 200, listed.text
    [entry] = [e for e in listed.json()["other"] if e["bucket"] == "node_output"]
    assert (entry["label"], entry["owner"], entry["pipeline"]) == (copy_join, None, "main.py")
