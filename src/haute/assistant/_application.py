"""Typed application services behind the in-app assistant tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from haute._types import PipelineGraph
from haute.assistant._ops import (
    AssistantOperationError,
    GraphEditPlan,
    PlanStore,
    ProjectSnapshot,
    ProjectSourceEvidence,
    SemanticDiff,
    apply_ops,
    build_graph_edit_plan,
    build_project_snapshot,
    parse_ops,
    semantic_diff,
    verify_postconditions,
)
from haute.execution import execute_lazy_graph
from haute.executor import (
    _build_node_fn,
    _compile_preamble,
    _pipeline_dir,
)
from haute.graph_utils import flatten_graph
from haute.routes._helpers import parse_pipeline_to_graph, save_lock
from haute.routes._save_pipeline import SavePipelineService

_MAX_SCHEMA_TARGETS = 100

MutationReadiness = Callable[[Path], tuple[bool, str | None]]
GraphPublisher = Callable[[str, PipelineGraph], str]
GraphParser = Callable[[Path], PipelineGraph]
ProjectSources = Callable[[str], Sequence[Path | ProjectSourceEvidence]]


@dataclass(frozen=True, slots=True)
class ApplicationResult:
    """The attributable result of one committed, verified graph plan."""

    plan_hash: str
    capability_hash: str
    base_revision: str
    result_revision: str
    expected_diff: SemanticDiff
    actual_diff: SemanticDiff
    verification_tier: str
    verification_evidence: tuple[Mapping[str, object], ...]
    graph_fingerprint: str
    warnings: tuple[str, ...]
    git_sha: str | None
    applied_operations: int

    def as_dict(self) -> dict[str, object]:
        return {
            "plan_hash": self.plan_hash,
            "capability_hash": self.capability_hash,
            "base_revision": self.base_revision,
            "result_revision": self.result_revision,
            "expected_diff": self.expected_diff.as_dict(),
            "actual_diff": self.actual_diff.as_dict(),
            "verification_tier": self.verification_tier,
            "verification_evidence": [dict(item) for item in self.verification_evidence],
            "graph_fingerprint": self.graph_fingerprint,
            "warnings": list(self.warnings),
            "git_sha": self.git_sha,
            "applied_operations": self.applied_operations,
        }


class CommittedVerificationError(AssistantOperationError):
    """A save completed, but its strongest declared verification did not."""

    def __init__(self, message: str, result: Mapping[str, object]) -> None:
        super().__init__("verification_failed", message)
        self.result = dict(result)


def _schema_validation_targets(
    graph: PipelineGraph,
    diff: SemanticDiff,
) -> tuple[str, ...]:
    """Return affected terminal nodes whose lazy schemas prove executability."""

    present = {node.id for node in graph.nodes}
    seeds = set(diff.nodes_added) | set(diff.nodes_updated)
    seeds.update(new for _old, new in diff.nodes_renamed)
    for source, target, _source_handle, _target_handle in (
        *diff.edges_added,
        *diff.edges_removed,
    ):
        seeds.update((source, target))
    seeds.intersection_update(present)
    if diff.preamble_changed:
        seeds = set(present)
    if not seeds:
        return ()

    downstream: dict[str, set[str]] = {node_id: set() for node_id in present}
    for edge in graph.edges:
        if edge.source in present and edge.target in present:
            downstream[edge.source].add(edge.target)

    affected = set(seeds)
    pending = list(seeds)
    while pending:
        current = pending.pop()
        for child in downstream[current]:
            if child not in affected:
                affected.add(child)
                pending.append(child)

    targets = tuple(sorted(node_id for node_id in affected if not downstream[node_id]))
    if not targets:
        targets = tuple(sorted(affected))
    if len(targets) > _MAX_SCHEMA_TARGETS:
        raise AssistantOperationError(
            "schema_validation_too_broad",
            f"Schema validation requires {len(targets)} targets; "
            f"the maximum is {_MAX_SCHEMA_TARGETS}",
        )
    return targets


def _frame_schema(frame: Any) -> list[dict[str, str]]:
    return [{"name": name, "dtype": str(dtype)} for name, dtype in frame.collect_schema().items()]


def _schema_evidence(
    graph: PipelineGraph,
    targets: Sequence[str],
) -> tuple[Mapping[str, object], ...]:
    """Resolve target schemas through the production lazy engine without rows."""

    if not targets:
        return ()
    flattened = flatten_graph(graph)
    preamble_ns = _compile_preamble(
        graph.preamble or "",
        pipeline_dir=_pipeline_dir(graph),
    )
    evidence: list[Mapping[str, object]] = []
    for target in targets:
        try:
            lazy_outputs, *_ = execute_lazy_graph(
                flattened,
                _build_node_fn,
                target_node_id=target,
                preserve_node_ids={target},
                preamble_ns=preamble_ns or None,
                source=graph.active_source,
                enforce_contracts=True,
            )
            output = lazy_outputs[target]
            extra: dict[str, object]
            if isinstance(output, dict):
                ports = {port: _frame_schema(frame) for port, frame in sorted(output.items())}
                schema_payload: dict[str, object] = {"ports": ports}
                shape = "ports"
                column_count = sum(len(columns) for columns in ports.values())
                extra = {"port_count": len(ports)}
            else:
                columns = _frame_schema(output)
                schema_payload = {"columns": columns}
                shape = "frame"
                column_count = len(columns)
                extra = {}
            schema_digest = sha256(
                json.dumps(
                    schema_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            evidence.append(
                {
                    "kind": "node_schema_resolved",
                    "node": target,
                    "shape": shape,
                    "column_count": column_count,
                    "schema_sha256": schema_digest,
                    **extra,
                }
            )
        except Exception as exc:
            raise AssistantOperationError(
                "schema_unresolvable",
                f"Schema validation failed for node {target!r}: {exc}",
            ) from exc
    return tuple(evidence)


class PipelineApplicationService:
    """Canonical inspect, plan, apply, and verify service."""

    def __init__(
        self,
        *,
        project_root: Path,
        pipeline_root: Path,
        mutations_readiness: MutationReadiness,
        publish_graph_update: GraphPublisher,
        plan_store: PlanStore | None = None,
        parse_graph: GraphParser = parse_pipeline_to_graph,
        project_sources: ProjectSources | None = None,
    ) -> None:
        self._project_root = project_root.resolve()
        self._pipeline_root = pipeline_root.resolve()
        if not self._pipeline_root.is_relative_to(self._project_root):
            raise ValueError("pipeline_root must resolve inside project_root")
        self._mutations_readiness = mutations_readiness
        self._publish_graph_update = publish_graph_update
        self._parse_graph = parse_graph
        self._project_sources = project_sources or (lambda _source_file: ())
        self.plan_store = plan_store if plan_store is not None else PlanStore()

    def _source_path(self, source_file: str) -> Path:
        source = (self._project_root / source_file).resolve()
        if not source.is_relative_to(self._project_root):
            raise AssistantOperationError(
                "project_source_forbidden",
                "Pipeline source is outside the project root",
            )
        return source

    def _save_service(self) -> SavePipelineService:
        return SavePipelineService(
            project_root=self._project_root,
            pipeline_root=self._pipeline_root,
        )

    def inspect(self, source_file: str) -> tuple[PipelineGraph, str]:
        """Return the saved graph and the revision it describes."""

        source = self._source_path(source_file)
        graph = self._parse_graph(source)
        snapshot = build_project_snapshot(
            self._project_root,
            source,
            graph,
            self._project_sources(source_file),
        )
        return graph, snapshot.revision

    def _snapshot_for_plan(
        self,
        source_file: str,
        graph: PipelineGraph,
        plan: GraphEditPlan,
    ) -> ProjectSnapshot:
        source = self._source_path(source_file)
        always_included = {
            f"content:{source.relative_to(self._project_root).as_posix()}",
            "content:haute.toml",
        }
        planned_sources: list[ProjectSourceEvidence] = []
        for identity, digest in plan.source_manifest:
            if identity in always_included:
                continue
            kind, separator, relative = identity.partition(":")
            if not separator or kind not in {"content", "schema"}:
                raise AssistantOperationError(
                    "invalid_plan", "The plan contains an invalid revision source"
                )
            planned_sources.append(
                ProjectSourceEvidence(
                    path=self._project_root / relative,
                    digest=digest,
                    kind=kind,  # type: ignore[arg-type]
                )
            )
        return build_project_snapshot(
            self._project_root,
            source,
            graph,
            tuple(planned_sources),
        )

    def dry_run(
        self,
        source_file: str,
        operations: Sequence[Mapping[str, Any]],
        *,
        postconditions: Sequence[Mapping[str, Any]] = (),
    ) -> GraphEditPlan:
        """Validate and retain an exact no-write plan against saved state."""

        source = self._source_path(source_file)
        graph = self._parse_graph(source)
        snapshot = build_project_snapshot(
            self._project_root,
            source,
            graph,
            self._project_sources(source_file),
        )
        result_graph = apply_ops(graph, parse_ops(operations))
        warnings = self._save_service().validate_graph(
            result_graph,
            source_file=source_file,
        )
        provisional = build_graph_edit_plan(
            snapshot,
            operations,
            postconditions,
            validation_warnings=warnings,
        )
        targets = _schema_validation_targets(result_graph, provisional.diff)
        evidence = _schema_evidence(result_graph, targets)
        plan = build_graph_edit_plan(
            snapshot,
            operations,
            postconditions,
            validation_warnings=warnings,
            verification_tier="schema" if evidence else "structural",
            verification_evidence=evidence,
        )
        self.plan_store.put(plan)
        return plan

    def _prepare_apply(
        self,
        source_file: str,
        plan: GraphEditPlan,
    ) -> tuple[PipelineGraph, PipelineGraph, GraphEditPlan]:
        source = self._source_path(source_file)
        before = self._parse_graph(source)
        snapshot = self._snapshot_for_plan(source_file, before, plan)
        if snapshot.revision != plan.base_revision:
            raise AssistantOperationError(
                "stale_revision",
                "The saved project changed after this plan was validated",
            )
        wire = plan.as_dict()
        raw_operations = wire["normalized_operations"]
        raw_postconditions = wire["postconditions"]
        assert isinstance(raw_operations, list)
        assert isinstance(raw_postconditions, list)
        parsed_operations = parse_ops(raw_operations)
        after = apply_ops(before, parsed_operations)
        warnings = self._save_service().validate_graph(
            after,
            source_file=source_file,
        )
        provisional = build_graph_edit_plan(
            snapshot,
            raw_operations,
            postconditions=raw_postconditions,
            validation_warnings=warnings,
        )
        targets = _schema_validation_targets(after, provisional.diff)
        schema_evidence = _schema_evidence(after, targets)
        recomputed = build_graph_edit_plan(
            snapshot,
            raw_operations,
            postconditions=raw_postconditions,
            validation_warnings=warnings,
            verification_tier="schema" if schema_evidence else "structural",
            verification_evidence=schema_evidence,
        )
        if recomputed.plan_hash != plan.plan_hash or recomputed != plan:
            raise AssistantOperationError(
                "invalid_plan",
                "The stored plan no longer matches its canonical payload",
            )
        return before, after, recomputed

    def _commit(
        self,
        source_file: str,
        after: PipelineGraph,
    ) -> Any:
        return self._save_service().save_graph_transactionally(
            graph=after,
            name=after.pipeline_name or "",
            description=after.pipeline_description or "",
            preamble=after.preamble,
            source_file=source_file,
        )

    def _verify_commit(
        self,
        source_file: str,
        before: PipelineGraph,
        plan: GraphEditPlan,
    ) -> tuple[PipelineGraph, str, SemanticDiff, tuple[Mapping[str, object], ...]]:
        reparsed = self._parse_graph(self._source_path(source_file))
        raw_operations = plan.as_dict()["normalized_operations"]
        assert isinstance(raw_operations, list)
        actual_diff = semantic_diff(before, reparsed, raw_operations)
        if actual_diff != plan.diff:
            raise AssistantOperationError(
                "verification_failed",
                "The saved semantic diff does not match the validated plan",
            )
        structural_evidence = verify_postconditions(reparsed, plan.postconditions)
        schema_targets = tuple(
            str(item["node"])
            for item in plan.verification_evidence
            if item.get("kind") == "node_schema_resolved"
        )
        try:
            schema_evidence = _schema_evidence(reparsed, schema_targets)
        except AssistantOperationError as exc:
            raise AssistantOperationError(
                "verification_failed",
                f"Committed graph schema verification failed: {exc}",
            ) from exc
        if schema_evidence != plan.verification_evidence:
            raise AssistantOperationError(
                "verification_failed",
                "The committed graph schema evidence does not match the validated plan",
            )
        result_snapshot = self._snapshot_for_plan(source_file, reparsed, plan)
        return (
            reparsed,
            result_snapshot.revision,
            actual_diff,
            (*structural_evidence, *schema_evidence),
        )

    async def apply(
        self,
        source_file: str,
        plan_hash: str,
    ) -> ApplicationResult:
        """Apply one exact plan once, then verify structure and bound schemas."""

        async with save_lock:
            enabled, reason = self._mutations_readiness(self._project_root)
            if not enabled:
                raise AssistantOperationError(
                    "authority_denied",
                    reason or "Assistant mutations are not enabled for this project",
                )
            plan = self.plan_store.begin_apply(plan_hash)
            try:
                before, after, recomputed = await asyncio.to_thread(
                    self._prepare_apply,
                    source_file,
                    plan,
                )
            except BaseException:
                self.plan_store.abort_apply(plan_hash)
                raise

            try:
                response = await asyncio.to_thread(self._commit, source_file, after)
            except BaseException:
                self.plan_store.abort_apply(plan_hash)
                raise

            try:
                reparsed, result_revision, actual_diff, evidence = await asyncio.to_thread(
                    self._verify_commit,
                    source_file,
                    before,
                    recomputed,
                )
                fingerprint = self._publish_graph_update(source_file, reparsed)
                result = ApplicationResult(
                    plan_hash=plan.plan_hash,
                    capability_hash=plan.capability_hash,
                    base_revision=plan.base_revision,
                    result_revision=result_revision,
                    expected_diff=plan.diff,
                    actual_diff=actual_diff,
                    verification_tier=plan.verification_tier,
                    verification_evidence=evidence,
                    graph_fingerprint=fingerprint,
                    warnings=tuple(response.warnings or ()),
                    git_sha=response.git_sha,
                    applied_operations=len(plan.normalized_operations),
                )
                self.plan_store.complete_apply(plan_hash, result.as_dict())
                return result
            except BaseException as exc:
                # The transaction returned successfully: this plan is used
                # even when reparse, structural proof, or publication fails.
                # Publish the validated target graph when possible so the
                # canvas does not remain stale, and return a truthful
                # committed-but-unverified result to the tool boundary.
                fallback_fingerprint: str | None = None
                publish_error: str | None = None
                try:
                    fallback_fingerprint = self._publish_graph_update(source_file, after)
                except Exception as publish_exc:  # noqa: BLE001 - preserve committed state
                    publish_error = type(publish_exc).__name__
                failure = {
                    "plan_hash": plan.plan_hash,
                    "capability_hash": plan.capability_hash,
                    "base_revision": plan.base_revision,
                    "expected_diff": plan.diff.as_dict(),
                    "actual_diff": None,
                    "verification_tier": plan.verification_tier,
                    "verification_status": "failed",
                    "verification_error_code": getattr(exc, "code", type(exc).__name__),
                    "graph_fingerprint": fallback_fingerprint,
                    "graph_publication_error": publish_error,
                    "warnings": list(response.warnings or ()),
                    "git_sha": response.git_sha,
                    "applied_operations": len(plan.normalized_operations),
                }
                self.plan_store.complete_apply(plan_hash, failure)
                raise CommittedVerificationError(
                    "The plan was committed, but structural verification failed; "
                    "review or undo the captured save before continuing.",
                    failure,
                ) from exc


__all__ = [
    "ApplicationResult",
    "CommittedVerificationError",
    "PipelineApplicationService",
]
