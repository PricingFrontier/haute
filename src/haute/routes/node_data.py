"""Node-data endpoints: point status, explicit cache builds, status, cancel, clear."""

from __future__ import annotations

from typing import TypeVar

from fastapi import APIRouter

from haute.graph_utils import flatten_graph
from haute.routes._job_store import get_job_store
from haute.routes._node_data_service import NodeDataService
from haute.routes.pipeline import _ensure_source_file, _validate_runtime_input_paths
from haute.schemas import (
    NodeDataClearResponse,
    NodeDataPointResponse,
    NodeDataProfileResponse,
    NodeDataRequest,
    NodeDataRunRequest,
    NodeDataRunResponse,
    NodeDataStatusResponse,
)

router = APIRouter(prefix="/api/node-data", tags=["node-data"])

_store = get_job_store("node_data")
_node_data_service = NodeDataService(_store)

RequestT = TypeVar("RequestT", bound=NodeDataRequest)


def node_data_service() -> NodeDataService:
    """The shared service, so another route resolves points against its jobs.

    A second service would keep its own view of which builds are running, so a
    node being built would read as merely stale there.
    """
    return _node_data_service


def _prepared(body: RequestT) -> RequestT:
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    _validate_runtime_input_paths(graph)
    return body.model_copy(update={"graph": graph})


@router.post("/point", response_model=NodeDataPointResponse)
def node_data_point(body: NodeDataRequest) -> NodeDataPointResponse:
    """Report the data point a consumer node reads and its state for that consumer."""
    return _node_data_service.point(_prepared(body))


@router.post("/run", response_model=NodeDataRunResponse)
def run_node_data(body: NodeDataRunRequest) -> NodeDataRunResponse:
    """Start, join, or delegate caching of a consumer node's whole dataset."""
    return _node_data_service.run(_prepared(body))


@router.post("/profile", response_model=NodeDataProfileResponse)
def node_data_profile(body: NodeDataRequest) -> NodeDataProfileResponse:
    """Return or start the data profile of a consumer node's current data version."""
    return _node_data_service.profile(_prepared(body))


@router.get("/status/{job_id}", response_model=NodeDataStatusResponse)
def node_data_status(job_id: str) -> NodeDataStatusResponse:
    """Poll a node-data cache build."""
    return _node_data_service.status(job_id)


@router.post("/cancel/{job_id}", response_model=NodeDataStatusResponse)
def cancel_node_data(job_id: str) -> NodeDataStatusResponse:
    """Cancel a running node-data cache build."""
    return _node_data_service.cancel(job_id)


@router.post("/clear", response_model=NodeDataClearResponse)
def clear_node_data(body: NodeDataRequest) -> NodeDataClearResponse:
    """Clear every cached signature of a consumer node's node-output data point."""
    return _node_data_service.clear(_prepared(body))
