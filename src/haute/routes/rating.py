"""Rating endpoints: whole-dataset levels for the factors a Rating Step rates on."""

from __future__ import annotations

from fastapi import APIRouter, Request

from haute.graph_utils import flatten_graph
from haute.routes._rating_levels import RatingLevelsService
from haute.routes._synchronous_analysis import run_until_disconnected
from haute.routes.node_data import _node_data_service
from haute.routes.pipeline import _ensure_source_file, _validate_runtime_input_paths
from haute.schemas import RatingLevelsRequest, RatingLevelsResponse

router = APIRouter(prefix="/api/rating", tags=["rating"])

_rating_levels_service = RatingLevelsService(_node_data_service)


@router.post("/levels", response_model=RatingLevelsResponse)
async def rating_levels(body: RatingLevelsRequest, request: Request) -> RatingLevelsResponse:
    """Return the whole-dataset levels of the raw factor columns asked about.

    The editor supersedes its own request when the factors change, so the
    abandoned one is cancelled rather than left scanning the whole dataset for
    an answer nobody will read.
    """
    graph = flatten_graph(body.graph)
    _ensure_source_file(graph)
    _validate_runtime_input_paths(graph)
    prepared = body.model_copy(update={"graph": graph})
    return await run_until_disconnected(
        request, lambda token: _rating_levels_service.levels(prepared, cancellation_token=token)
    )
