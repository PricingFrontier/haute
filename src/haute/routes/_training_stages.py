"""Bind project-scoped staged builds to the canonical training job lifecycle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haute._logging import get_logger
from haute._project_context import current_project
from haute.errors import ConflictError, HauteValidationError
from haute.modelling._stage_objects import StoredRevision
from haute.modelling._stage_store import PreparedStage, StageStore
from haute.modelling._staged_config import STAGE_NAMES, StagedConfig
from haute.modelling._staged_runtime import fit_config_digest
from haute.modelling._train_config import INTERACTIVE_MODEL_KEYS, build_training_job_kwargs
from haute.schemas import StageActionRequest, StageJobRequest, TrainRequest, TrainResponse

logger = get_logger(component="server.modelling.stages")


def next_preview_stage(raw: object, applied: str) -> str | None:
    settings = StagedConfig.from_plain_data(raw).to_plain_data()
    enabled = settings.get("enabled")
    if enabled is None:
        banded = settings["smoothing"]["parent"] == "banded"
        enabled = {
            "banded": banded,
            "smoothed": True,
            "pruned": (settings["banding"]["parent"] if banded else settings["smoothing"]["parent"])
            == "pruned",
        }
    chain = ["full", *(stage for stage in STAGE_NAMES[1:] if enabled[stage])]
    if applied not in chain or chain.index(applied) + 1 == len(chain):
        return None
    return chain[chain.index(applied) + 1]


def _upstream_digest(body: TrainRequest) -> str:
    from haute._cache import graph_pipeline_dir, preamble_execution_fingerprint
    from haute._input_providers import source_signature

    graph = body.graph
    ancestors = {body.node_id}
    while True:
        expanded = ancestors | {edge.source for edge in graph.edges if edge.target in ancestors}
        if expanded == ancestors:
            break
        ancestors = expanded
    value = {
        "preamble": preamble_execution_fingerprint(
            graph.preamble, pipeline_dir=graph_pipeline_dir(graph)
        ),
        "local_sources": {
            node.id: source_signature(node.data.config, base_dir=graph_pipeline_dir(graph))
            for node in graph.nodes
            if node.id in ancestors and node.data.nodeType == "dataInput"
        },
        "source": body.source,
        "global_constants": [item.model_dump(mode="json") for item in graph.global_constants],
        "preserved_blocks": graph.preserved_blocks,
        "nodes": sorted(
            (node.id, node.type, node.data.nodeType, node.data.config)
            for node in graph.nodes
            if node.id in ancestors and node.id != body.node_id
        ),
        "edges": sorted(
            (edge.source, edge.target, edge.sourceHandle or "", edge.targetHandle or "")
            for edge in graph.edges
            if edge.target in ancestors
        ),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class StageJobScope:
    store: StageStore
    key: str
    operation: str
    parent_revision: str | None
    graph_digest: str
    followup_request: TrainRequest | None = None

    @classmethod
    def from_request(cls, body: TrainRequest) -> StageJobScope:
        if body.model is None:
            raise HauteValidationError("Staged training requires an interactive model")
        store = StageStore(current_project().root)
        return cls(
            store,
            store.key(str(body.graph.source_file or ""), body.node_id, body.model),
            body.stage.operation if body.stage is not None else "full",
            body.stage.parent_revision if body.stage is not None else None,
            _upstream_digest(body),
            body.model_copy(deep=True)
            if body.stage and body.stage.operation == "full" and body.stage.prepare_next
            else None,
        )

    def validate_parent(self) -> None:
        snapshot = self.store.recover(self.key, compact=True)
        if self.operation == "full":
            return
        current = snapshot["current"]
        if current is None or current["revision"] != self.parent_revision:
            raise ConflictError("The stage parent changed; refresh before continuing")
        if current["result"].get("graph_digest") != self.graph_digest:
            raise ConflictError("The upstream pipeline changed; build the GBM again")

    def validate_configuration(self, config: Mapping[str, Any]) -> None:
        current = self.store.snapshot(self.key, compact=True)["current"]
        if current is None:
            raise ConflictError("Build the GBM before using a saved stage")
        if current["result"].get("graph_digest") != self.graph_digest:
            raise ConflictError("The upstream pipeline changed; build the GBM again")
        kwargs = build_training_job_kwargs(config, data="stage-checkpoint", default_name="model")
        kwargs["purpose"] = "exploration"
        if fit_config_digest(kwargs) != current["config_digest"]:
            raise ConflictError("The GBM configuration changed; build the GBM again")

    def validate_checkpoint(self, record: dict[str, Any], config: Mapping[str, Any]) -> None:
        if record["result"].get("graph_digest") != self.graph_digest:
            raise ConflictError("The upstream pipeline changed; this checkpoint cannot be restored")
        saved = record["result"].get("config")
        if not isinstance(saved, dict):
            raise HauteValidationError("This checkpoint has no saved model settings")
        restored = {
            key: value for key, value in config.items() if key not in INTERACTIVE_MODEL_KEYS
        }
        restored.update({key: saved[key] for key in INTERACTIVE_MODEL_KEYS if key in saved})
        kwargs = build_training_job_kwargs(restored, data="stage-checkpoint", default_name="model")
        kwargs["purpose"] = "exploration"
        if fit_config_digest(kwargs) != record["config_digest"]:
            raise ConflictError(
                "Shared training settings changed; restore the original setup and split "
                "before this checkpoint"
            )

    def begin(self, job_id: str, *, queued_preview: bool = False) -> None:
        self.store.begin(
            self.key,
            job_id=job_id,
            operation=self.operation,
            expected_revision=self.parent_revision,
            queued_preview=queued_preview,
        )

    def parent_reference(self) -> dict[str, Any]:
        """The worker's in-place reference to this job's parent revision (no copy is made)."""
        if self.parent_revision is None:
            raise HauteValidationError("Post-fit work requires a saved parent")
        return self.store.parent_reference(self.key, self.parent_revision)

    def lease_parent(self) -> AbstractContextManager[StoredRevision]:
        """Pin this job's parent revision, for validating the delta built on it."""
        if self.parent_revision is None:
            raise HauteValidationError("Post-fit work requires a saved parent")
        return self.store.lease(self.key, self.parent_revision)

    def prepare(
        self,
        path: Path,
        response: TrainResponse,
        *,
        provenance: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> PreparedStage:
        if response.staged is None:
            raise HauteValidationError("Stage job returned no stage diagnostics")
        if self.operation == "finalise":
            from haute.modelling._stage_objects import StageArchive

            final = StageArchive.read(path).manifest.metadata["final_job_kwargs"]
            config = {
                **config,
                "name": final["name"],
                "params": final["params"],
                "stages": final["stages"],
                "tuning": None,
                "refit_on_development": True,
            }
        return self.store.prepare(
            self.key,
            path,
            result={
                "response": response.model_dump(mode="json"),
                "graph_digest": self.graph_digest,
                "provenance": dict(provenance),
                "config": dict(config),
            },
            config_digest=response.staged.fit_config_digest,
            parent_revision=self.parent_revision,
        )

    def publish(self, job_id: str, prepared: PreparedStage) -> None:
        self.store.publish(
            self.key,
            job_id=job_id,
            prepared=prepared,
            preview=self.operation in {"pruned", "banded", "smoothed"},
            next_preview=(
                next_preview_stage(prepared.result["response"]["staged"]["configuration"], "full")
                if self.followup_request is not None
                else None
            ),
        )

    def start_queued_preview(
        self,
        body: TrainRequest,
        start: Callable[[TrainRequest], TrainResponse],
        *,
        expected_revision: str,
    ) -> None:
        queued = self.store.snapshot(self.key, compact=True)["next_preview"]
        if (
            not queued
            or queued["status"] != "queued"
            or queued["parent_revision"] != expected_revision
        ):
            return
        try:
            request = TrainRequest(
                graph=body.graph.model_copy(deep=True),
                node_id=body.node_id,
                source=body.source,
                model=body.model,
                stage=StageJobRequest(
                    operation=queued["operation"], parent_revision=queued["parent_revision"]
                ),
            )
            start(request)
        except Exception as exc:
            # The preceding stage is already committed. Report continuation failure
            # independently instead of turning a successful fit/apply into an error.
            logger.warning(
                "stage_preview_start_failed", operation=queued["operation"], error=str(exc)
            )
            self.store.preview_start_failed(
                self.key,
                revision=queued["parent_revision"],
                message=f"Could not prepare the next preview: {exc}. Use Preview to retry.",
            )

    def apply_preview(
        self, body: StageActionRequest, start: Callable[[TrainRequest], TrainResponse]
    ) -> dict[str, Any]:
        config = next(node.data.config for node in body.graph.nodes if node.id == body.node_id)
        self.validate_configuration(config)
        snapshot = self.store.snapshot(self.key)
        candidate = snapshot["preview"]
        if candidate is None or candidate["revision"] != body.preview_revision:
            raise ConflictError("The stage preview changed; refresh before Apply")
        staged = candidate["result"]["response"].get("staged", {})
        if staged.get("prepared_path") is not None:
            from haute.routes._training_paths import apply_path
            from haute.schemas import StagePathApplyRequest

            path = next(
                value
                for value in staged["paths"].values()
                if value["id"] == staged["prepared_path"]
            )
            return apply_path(
                self,
                StagePathApplyRequest(
                    **body.model_dump(exclude={"preview_revision"}),
                    artifact_revision=body.preview_revision,
                    path_id=path["id"],
                    point_id=path["selected_point"],
                ),
                start,
            )
        operation = (
            next_preview_stage(
                config.get("stages"), candidate["result"]["response"]["staged"]["selected_stage"]
            )
            if body.prepare_next
            else None
        )
        self.store.apply(
            self.key,
            expected_revision=body.current_revision,
            preview_revision=body.preview_revision,
            next_preview=operation,
        )
        self.store.cleanup(self.key)
        if operation is not None:
            self.start_queued_preview(body, start, expected_revision=body.preview_revision)
        return self.store.snapshot(self.key)

    def finish(self, job_id: str, job: Mapping[str, Any]) -> None:
        if job.get("status") != "completed":
            self.store.finish(
                self.key,
                job_id=job_id,
                status="cancelled" if job.get("status") == "cancelled" else "failed",
                message=str(job.get("message") or "Stage job interrupted"),
            )
        self.store.cleanup(self.key)
