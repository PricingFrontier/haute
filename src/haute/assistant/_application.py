"""Typed application services behind the in-app assistant tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from haute._pipeline_recovery import load_pipeline_editor_document
from haute._types import PipelineGraph
from haute.assistant._ops import (
    AssistantOperationError,
    GraphEditPlan,
    PlanStore,
    ProjectSnapshot,
    ProjectSourceEvidence,
    SemanticDiff,
    build_project_snapshot,
    finalize_graph_edit_plan,
    prepare_graph_edit,
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
# Publishes the current on-disk editor document for *source_file* to live
# sync clients and returns the published document fingerprint.
DocumentUpdatePublisher = Callable[[str], str]
GraphParser = Callable[[Path], PipelineGraph]
ProjectSources = Callable[[str], Sequence[Path | ProjectSourceEvidence]]
GraphValidator = Callable[[PipelineGraph], Sequence[str]]


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


@dataclass(frozen=True, slots=True)
class VerifiedPlan:
    """One fully validated graph result and its sealed plan authority."""

    result_graph: PipelineGraph
    plan: GraphEditPlan


class CommittedVerificationError(AssistantOperationError):
    """A save completed, but its strongest declared verification did not."""

    def __init__(self, message: str, result: Mapping[str, object]) -> None:
        super().__init__("verification_failed", message)
        self.result = dict(result)


def _diff_seed_nodes(graph: PipelineGraph, diff: SemanticDiff) -> frozenset[str]:
    """Return the surviving nodes this plan is directly answerable for.

    Only the edge's target is seeded. Adding or removing an edge changes what
    arrives at the target and therefore everything downstream of it; the
    source's own output schema is unchanged and its other children are
    untouched. Seeding the source dragged every unrelated branch of a shared
    input into validation, so an edit was blocked — and blamed — by a node it
    never touched.
    """

    present = {node.id for node in graph.nodes}
    seeds = set(diff.nodes_added) | set(diff.nodes_updated)
    seeds.update(new for _old, new in diff.nodes_renamed)
    for _source, target, _source_handle, _target_handle in (
        *diff.edges_added,
        *diff.edges_removed,
    ):
        seeds.add(target)
    seeds.intersection_update(present)
    if diff.preamble_changed:
        # A preamble replacement can change any node's behaviour, so the plan
        # is answerable for the whole graph.
        seeds = set(present)
    return frozenset(seeds)


def _schema_validation_targets(
    graph: PipelineGraph,
    diff: SemanticDiff,
) -> tuple[str, ...]:
    """Return affected terminal nodes whose lazy schemas prove executability."""

    present = {node.id for node in graph.nodes}
    seeds = set(_diff_seed_nodes(graph, diff))
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


@dataclass(frozen=True, slots=True)
class _PreparedGraph:
    """One graph's flatten-and-preamble preparation, reused across targets.

    Preparation is per graph, not per target: a plan validates every terminal
    of the changed nodes' downstream cone, and both the baseline and planned
    graphs may be prepared. Doing it inside the per-target call re-flattened
    the whole graph once per terminal for no change in result.
    """

    graph: PipelineGraph
    flattened: PipelineGraph
    preamble_ns: dict[str, Any] | None

    @classmethod
    def build(cls, graph: PipelineGraph) -> _PreparedGraph:
        preamble_ns = _compile_preamble(
            graph.preamble or "",
            pipeline_dir=_pipeline_dir(graph),
        )
        return cls(
            graph=graph,
            flattened=flatten_graph(graph),
            preamble_ns=preamble_ns or None,
        )


def _resolve_target_evidence(prepared: _PreparedGraph, target: str) -> Mapping[str, object]:
    """Resolve one terminal's schema through the production lazy engine.

    `schema_only=True` states the invariant this path already holds: it reads
    `collect_schema()` and never collects a frame or invokes a sink, so the
    engine's group-by materialisation-admission gate — which bounds peak memory
    during materialisation — does not apply to it.
    """

    graph = prepared.graph
    lazy_outputs, *_ = execute_lazy_graph(
        prepared.flattened,
        _build_node_fn,
        target_node_id=target,
        preserve_node_ids={target},
        preamble_ns=prepared.preamble_ns,
        source=graph.active_source,
        enforce_contracts=True,
        schema_only=True,
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
    return {
        "kind": "node_schema_resolved",
        "node": target,
        "shape": shape,
        "column_count": column_count,
        "schema_sha256": schema_digest,
        **extra,
    }


def _schema_evidence(
    graph: PipelineGraph,
    targets: Sequence[str],
    *,
    baseline: PipelineGraph | None = None,
    changed: frozenset[str] = frozenset(),
) -> tuple[tuple[Mapping[str, object], ...], tuple[str, ...]]:
    """Resolve target schemas without rows, separating pre-existing breakage.

    Validation reaches beyond the nodes a plan touches: a changed node's whole
    downstream cone is resolved, because that is what proves the edit
    executable. Collateral in that cone which *already* failed on the saved
    pipeline is not evidence against this plan — the analyst is being blocked
    by a defect the edit did not cause and does not touch. Such a target is
    excluded from the plan's schema evidence and reported as a
    `pre_existing_schema_failure:<node>` validation warning, so the plan drops
    to the tier its evidence actually supports rather than claiming a
    verification it did not perform. That warning is part of the hashed plan
    authority, so it carries the node identity only — never the engine's
    message, which is not deterministic across runs.

    `changed` is the plan's own seed set and is never excused. A node this plan
    added or updated is the plan's responsibility, and an authored-but-empty
    node fails on the saved pipeline by construction — excusing it would
    silently accept exactly the broken code the analyst asked for.

    `baseline=None` is the strict mode used for post-save verification, where
    every target is one the plan already resolved: a failure there is a real
    verification failure and can never be excused.
    """

    if not targets:
        return (), ()
    baseline_nodes = {node.id for node in baseline.nodes} if baseline is not None else set()
    prepared = _PreparedGraph.build(graph)
    prepared_baseline: _PreparedGraph | None = None
    evidence: list[Mapping[str, object]] = []
    warnings: list[str] = []
    for target in targets:
        try:
            evidence.append(_resolve_target_evidence(prepared, target))
            continue
        except Exception as exc:
            failure = exc
        if baseline is not None and target not in changed and target in baseline_nodes:
            try:
                if prepared_baseline is None:
                    # Prepared lazily: most plans never reach this path at all.
                    prepared_baseline = _PreparedGraph.build(baseline)
                _resolve_target_evidence(prepared_baseline, target)
            except Exception:
                # Deterministic and value-free by construction: this string is
                # hashed into the plan authority, and `apply` must reproduce it
                # exactly. An engine message carries estimated row counts and
                # scan byte sizes, which would make the plan hash depend on
                # data-file metadata the revision manifest does not pin.
                # `get_node_schema` on the named node reports the actual
                # failure, and the tool log records it server-side.
                warnings.append(f"pre_existing_schema_failure:{target}")
                continue
        raise AssistantOperationError(
            "schema_unresolvable",
            f"Schema validation failed for node {target!r}: {failure}",
        ) from failure
    return tuple(evidence), tuple(warnings)


def build_verified_plan(
    snapshot: ProjectSnapshot,
    operations: Sequence[Mapping[str, Any]],
    postconditions: Sequence[Mapping[str, Any]] = (),
    *,
    validate_graph: GraphValidator,
) -> VerifiedPlan:
    """Build one plan through the shared edit and save-verification pipeline."""

    prepared = prepare_graph_edit(snapshot, operations, postconditions)
    warnings = validate_graph(prepared.result_graph)
    targets = _schema_validation_targets(prepared.result_graph, prepared.diff)
    evidence, schema_warnings = _schema_evidence(
        prepared.result_graph,
        targets,
        baseline=snapshot.graph,
        changed=_diff_seed_nodes(prepared.result_graph, prepared.diff),
    )
    plan = finalize_graph_edit_plan(
        prepared,
        validation_warnings=(*warnings, *schema_warnings),
        verification_tier="schema" if evidence else "structural",
        verification_evidence=evidence,
    )
    return VerifiedPlan(result_graph=prepared.result_graph, plan=plan)


class PipelineApplicationService:
    """Canonical inspect, plan, apply, and verify service."""

    def __init__(
        self,
        *,
        project_root: Path,
        pipeline_root: Path,
        mutations_readiness: MutationReadiness,
        publish_document_update: DocumentUpdatePublisher,
        plan_store: PlanStore | None = None,
        parse_graph: GraphParser = parse_pipeline_to_graph,
        project_sources: ProjectSources | None = None,
    ) -> None:
        self._project_root = project_root.resolve()
        self._pipeline_root = pipeline_root.resolve()
        if not self._pipeline_root.is_relative_to(self._project_root):
            raise ValueError("pipeline_root must resolve inside project_root")
        self._mutations_readiness = mutations_readiness
        self._publish_document_update = publish_document_update
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
        verified = build_verified_plan(
            snapshot,
            operations,
            postconditions,
            validate_graph=lambda candidate: self._save_service().validate_graph(
                candidate,
                source_file=source_file,
            ),
        )
        self.plan_store.put(verified.plan)
        return verified.plan

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
        verified = build_verified_plan(
            snapshot,
            raw_operations,
            raw_postconditions,
            validate_graph=lambda candidate: self._save_service().validate_graph(
                candidate,
                source_file=source_file,
            ),
        )
        recomputed = verified.plan
        if recomputed.plan_hash != plan.plan_hash or recomputed != plan:
            raise AssistantOperationError(
                "invalid_plan",
                "The stored plan no longer matches its canonical payload",
            )
        return before, verified.result_graph, recomputed

    def _commit(
        self,
        source_file: str,
        after: PipelineGraph,
    ) -> Any:
        # Plan freshness was proven against the assistant snapshot under
        # ``save_lock``; the save precondition wants the editor protocol's
        # document revision, so read it now, still under that lock.
        source = self._source_path(source_file)
        base_revision = (
            load_pipeline_editor_document(source, project_root=self._project_root).source_revision
            if source.is_file()
            else None
        )
        return self._save_service().save_graph_transactionally(
            graph=after,
            name=after.pipeline_name or "",
            description=after.pipeline_description or "",
            preamble=after.preamble,
            source_file=source_file,
            base_revision=base_revision,
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
            schema_evidence, _ = _schema_evidence(reparsed, schema_targets)
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
                fingerprint = self._publish_document_update(source_file)
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
                # Publish the committed on-disk document when possible so the
                # canvas does not remain stale, and return a truthful
                # committed-but-unverified result to the tool boundary.
                fallback_fingerprint: str | None = None
                publish_error: str | None = None
                try:
                    fallback_fingerprint = self._publish_document_update(source_file)
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
    "VerifiedPlan",
    "build_verified_plan",
]
