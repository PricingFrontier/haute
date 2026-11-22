"""Read-only candidate inspection and revision-checked exact path application."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from haute.errors import ConflictError
from haute.modelling._stage_paths import (
    path_point_revision,
    read_path_artifact,
    read_path_result,
    resolve_path,
)
from haute.modelling._stage_views import compact_response
from haute.modelling._staged_config import StagedConfig
from haute.modelling._staged_runtime import open_stage_bundle, pack_members
from haute.routes._training_stages import StageJobScope, next_preview_stage
from haute.routes._training_worker import _training_response_payload
from haute.schemas import (
    StagePathApplyRequest,
    StagePathInspectionPayload,
    StagePathReadRequest,
    TrainRequest,
    TrainResponse,
)


def _owner(scope: StageJobScope, body: StagePathReadRequest) -> dict[str, Any]:
    snapshot = scope.store.snapshot(scope.key, compact=True)
    current = snapshot["current"]
    if current is None or current["revision"] != body.current_revision:
        raise ConflictError("The applied model changed; refresh before inspecting this path")
    for record in (current, snapshot["preview"]):
        if record and record["revision"] == body.artifact_revision:
            if record is not current and record["parent_revision"] != body.current_revision:
                break
            return dict(record)
    raise ConflictError("The path artifact changed; refresh before continuing")


def _response(result: Any, body: StagePathReadRequest) -> dict[str, Any]:
    evaluation = copy.deepcopy(result.evaluation)
    # These are embedded diagnostics, not downloadable/exportable inference artifacts.
    # Keep their references inside their actual archive member, never a deleted worker path.
    member = f"path-{body.path_id}-{body.point_id}.result.json"
    for field in ("plan_path", "results_path", "report_path"):
        evaluation[field] = f"{member}#evaluation/{field}"
    return _training_response_payload(
        result, job_id="", model_path="", evaluation=evaluation, tuning=result.tuning
    )


def inspect_path(scope: StageJobScope, body: StagePathReadRequest) -> StagePathInspectionPayload:
    _owner(scope, body)
    with scope.store.lease(scope.key, body.artifact_revision) as artifact:
        result = read_path_artifact(artifact, body.path_id, body.point_id)
        response = _response(result, body)
    _owner(scope, body)
    return StagePathInspectionPayload(
        current_revision=body.current_revision,
        artifact_revision=body.artifact_revision,
        path_id=body.path_id,
        point_id=body.point_id,
        response=TrainResponse.model_validate(
            compact_response(response) if body.diagnostics == "compact" else response
        ),
    )


def apply_path(
    scope: StageJobScope,
    body: StagePathApplyRequest,
    start: Callable[[TrainRequest], TrainResponse],
) -> dict[str, Any]:
    owner = _owner(scope, body)
    config = next(node.data.config for node in body.graph.nodes if node.id == body.node_id)
    scope.validate_configuration(config)
    draft = StagedConfig.from_plain_data(config.get("stages"))
    with scope.store.lease(scope.key, body.artifact_revision) as artifact:
        with open_stage_bundle(artifact) as bundle:
            path = resolve_path(bundle, body.path_id)
            proposed = draft.to_plain_data()
            proposed["enabled"] = {
                **dict.fromkeys(("pruned", "banded", "smoothed"), True),
                **proposed.get("enabled", {}),
                path["stage"]: True,
            }
            if (
                StagedConfig.from_plain_data(proposed).operation(path["stage"])["parent"]
                != path["parent"]
            ):
                raise ConflictError("The enabled predecessor changed; prepare this path again")
            selected = read_path_result(bundle, body.path_id, body.point_id)
            applied_config = selected.staged["configuration"]
            merged = draft.to_plain_data()
            section = {"pruned": "pruning", "banded": "banding", "smoothed": "smoothing"}[
                path["stage"]
            ]
            merged[section] = copy.deepcopy(applied_config[section])
            merged["enabled"] = {
                **dict.fromkeys(("pruned", "banded", "smoothed"), True),
                **merged.get("enabled", {}),
                path["stage"]: body.point_id != "unchanged",
            }
            for key in ("applied_recipe", "applied_path"):
                merged[key] = copy.deepcopy(applied_config[key])
            if path["stage"] == "pruned":
                merged["path"] = copy.deepcopy(applied_config["path"])
            merged = StagedConfig.from_plain_data(merged).to_plain_data()
            # The applied revision is the artifact revision's own objects plus the
            # point's diagnostics: published in place, with no archive written.
            files, metadata, result = path_point_revision(
                bundle, body.path_id, body.point_id, configuration=merged
            )
            members, metadata = pack_members(files, metadata)
            response = _response(result, body)
    operation = (
        next_preview_stage(merged, result.staged["selected_stage"]) if body.prepare_next else None
    )
    prepared = scope.store.prepare_members(
        scope.key,
        members,
        metadata=metadata,
        result={
            **owner["result"],
            "response": response,
            "config": {**config, "stages": merged},
        },
        config_digest=owner["config_digest"],
        parent_revision=body.artifact_revision,
    )
    # `apply_path` owns *prepared* from here: it discards the staging either way.
    value = scope.store.apply_path(
        scope.key,
        expected_revision=body.current_revision,
        artifact_revision=body.artifact_revision,
        prepared=prepared,
        next_preview=operation,
    )
    scope.store.cleanup(scope.key)
    if operation is not None:
        # Continue using the newly committed recipe and switches, preserving unrelated drafts.
        request = body.model_copy(deep=True)
        node = next(node for node in request.graph.nodes if node.id == request.node_id)
        node.data.config["stages"] = merged
        scope.start_queued_preview(request, start, expected_revision=value["current"]["revision"])
        return scope.store.snapshot(scope.key)
    return value
