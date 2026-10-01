"""Typed application services behind the in-app assistant tools."""

from __future__ import annotations

import ast
import asyncio
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from haute._ast_helpers import _extract_function_bodies, _is_pipeline_authored_decorator
from haute._builders import load_external_file_object
from haute._config_io import collect_node_configs, config_path_for_node, node_emits_sidecar
from haute._git import commit_parent
from haute._graph_builders import _extract_decorated_node_skeletons, _resolve_node_skeleton
from haute._graph_utils import _sanitize_func_name
from haute._input_providers import (
    DeclaredTableSchema,
    InferredInputSchema,
    RecordedSchemaTiers,
    recording_schema_tiers,
)
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._polars_steps import is_stepped_config, render_polars_steps, stepped_surface_for
from haute._types import GraphNode, NodeType, PipelineGraph
from haute._user_exec import user_code_line
from haute.assistant._catalog import new_logic_steps
from haute.assistant._change_record import (
    change_headline,
    change_record,
    evidence_summary,
    graph_changes,
)
from haute.assistant._ops import (
    AssistantOperationError,
    GraphEditPlan,
    LocatedPlanError,
    PlanReceipt,
    PlanStore,
    ProjectSnapshot,
    ProjectSourceEvidence,
    SemanticDiff,
    build_project_snapshot,
    finalize_graph_edit_plan,
    locate_plan_error,
    prepare_graph_edit,
    semantic_diff,
    validate_declared_postconditions,
    verify_postconditions,
)
from haute.codegen import graph_to_code_multi
from haute.errors import (
    ConfigError,
    HauteError,
    HauteValidationError,
    ParseError,
    PreambleError,
)
from haute.execution import execute_lazy_graph
from haute.executor import (
    _build_node_fn,
    _compile_preamble,
    _pipeline_dir,
)
from haute.graph_utils import flatten_graph
from haute.modelling._train_config import (
    MISSING_TARGET_MESSAGE,
    TrainingConfigError,
    parse_evaluation_config,
    training_objective_issue,
)
from haute.routes._helpers import commit_pipeline_graph, parse_pipeline_to_graph, save_lock
from haute.routes._save_pipeline import SavePipelineService
from haute.routes._training_preparation import build_training_feature_selection
from haute.schemas import AssistantChangeRecord, AssistantGraphChanges

_MAX_SCHEMA_TARGETS = 100

MutationReadiness = Callable[[Path], tuple[bool, str | None]]
# Publishes the current on-disk editor document for *source_file* to live
# sync clients, tagged with the change that saved it (None when a committed save
# has no change record), and returns the published document fingerprint.
DocumentUpdatePublisher = Callable[[str, AssistantChangeRecord | None], str]
GraphParser = Callable[[Path], PipelineGraph]
ProjectSources = Callable[[str], Sequence[Path | ProjectSourceEvidence]]
GraphValidator = Callable[[PipelineGraph], Sequence[str]]


@dataclass(frozen=True, slots=True)
class DryRunResult:
    """One validated, stored plan and the compact view the dry-run tool returns."""

    plan: GraphEditPlan
    changes: AssistantGraphChanges

    def as_dict(self) -> dict[str, object]:
        """What the model needs to apply the plan: no echoed operations, digests or revisions."""

        return {
            "plan_hash": self.plan.plan_hash,
            "operations": len(self.plan.normalized_operations),
            "verification_tier": self.plan.verification_tier,
            "evidence": evidence_summary(self.plan.verification_evidence),
            "warnings": list(self.plan.validation_warnings),
            "changes": self.changes.model_dump(mode="json", exclude_defaults=True),
        }


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
    change: AssistantChangeRecord

    def as_dict(self) -> dict[str, object]:
        """The compact apply result: the change record is the diff, stated once, and
        its id is the plan hash."""

        return {
            "applied_operations": self.applied_operations,
            "verification_tier": self.verification_tier,
            "evidence": evidence_summary(self.verification_evidence),
            "change": self.change.model_dump(mode="json", exclude_defaults=True),
        }


@dataclass(frozen=True, slots=True)
class UndoResult:
    """The forward save that undid one change: its commit and document revision."""

    git_sha: str | None
    revision: str


@dataclass(frozen=True, slots=True)
class VerifiedPlan:
    """One fully validated graph result and its sealed plan authority."""

    result_graph: PipelineGraph
    plan: GraphEditPlan


@dataclass(frozen=True, slots=True)
class FailedStep:
    """The authored step a failure's node-code line falls in (1-based number)."""

    node: str
    number: int
    step_id: str


class SchemaUnresolvableError(AssistantOperationError):
    """A target's schema did not resolve while validating a plan.

    Resolving a schema runs node code over the project's inputs, so the
    failure's own text can quote row values: a Polars cast error names the
    cell it could not parse, and node code can put collected values in its
    exception. The message therefore names only the target; the tool
    boundary renders ``failure`` under the project's egress policy. ``graph``
    is the graph that ran, whose schema metadata (and, only when executable
    source is permitted, its authored code) may name a column in that
    failure; ``submitted`` is the operations payload the model sent for this
    plan, whose text the provider already holds.
    """

    def __init__(
        self,
        node: str,
        failure: Exception,
        *,
        step: FailedStep | None,
        graph: PipelineGraph,
        submitted: Sequence[Mapping[str, Any]],
    ) -> None:
        where: dict[str, object] = {"node": node}
        if step is not None:
            where = {"node": step.node, "field": "steps", "step": step.step_id}
        super().__init__(
            "schema_unresolvable", f"Schema validation failed for node {node!r}.", where=where
        )
        self.node = node
        self.failure = failure
        self.step = step
        self.graph = graph
        self.submitted = tuple(submitted)


class PreambleFailedError(AssistantOperationError):
    """The pipeline preamble failed while a plan was being validated.

    The preamble is authored code that can read project data at import, so
    its ``PreambleError`` text can quote row values. The message is fixed; the
    tool boundary renders ``failure`` under the project's egress policy.
    """

    def __init__(self, failure: PreambleError, *, graph: PipelineGraph) -> None:
        super().__init__(
            "preamble_failed", "The pipeline preamble failed while validating the plan."
        )
        self.failure = failure
        self.graph = graph


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
    changes = diff.complete
    seeds = set(changes.written_nodes)
    seeds.update(new for _old, new in changes.nodes_renamed)
    for _source, target, _source_handle, _target_handle in (
        *changes.edges_added,
        *changes.edges_removed,
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
        try:
            preamble_ns = _compile_preamble(
                graph.preamble or "",
                pipeline_dir=_pipeline_dir(graph),
            )
        except PreambleError as exc:
            raise PreambleFailedError(exc, graph=graph) from exc
        return cls(
            graph=graph,
            flattened=flatten_graph(graph),
            preamble_ns=preamble_ns or None,
        )


def _resolve_lazy_output(prepared: _PreparedGraph, target: str) -> tuple[Any, RecordedSchemaTiers]:
    """Build one node's lazy output (a frame, or a frame per port) without rows.

    `schema_only=True` states the invariant this path already holds: callers
    read `collect_schema()` and never collect a frame or invoke a sink, so the
    engine's group-by materialisation-admission gate — which bounds peak memory
    during materialisation — does not apply to it. The resolution records the
    inferred and declared schema tiers, so a local file input with no snapshot
    yet resolves from its file and an API Input table from its declared
    contract; the inputs that did are returned with the output.
    """

    with recording_schema_tiers() as recorded:
        lazy_outputs, *_ = execute_lazy_graph(
            prepared.flattened,
            _build_node_fn,
            target_node_id=target,
            preserve_node_ids={target},
            preamble_ns=prepared.preamble_ns,
            source=prepared.graph.active_source,
            enforce_contracts=True,
            schema_only=True,
        )
    return lazy_outputs[target], recorded


def _inferred_input_evidence(node: str, inferred: InferredInputSchema) -> Mapping[str, object]:
    return {
        "kind": "input_schema_inferred",
        "node": node,
        "tier": "inferred",
        "format": inferred.format,
        "inference_rows": inferred.inference_rows,
    }


def _declared_input_evidence(
    node: str, table: str, declared: DeclaredTableSchema
) -> Mapping[str, object]:
    return {
        "kind": "input_schema_declared",
        "node": node,
        "table": table,
        "tier": "declared",
        "column_count": declared.column_count,
    }


def _resolve_target_evidence(
    prepared: _PreparedGraph, target: str
) -> tuple[Mapping[str, object], RecordedSchemaTiers]:
    """Resolve one terminal's schema through the production lazy engine.

    Returns the terminal's evidence and the inputs in its lineage whose schema
    was inferred from their file or taken from their declared contract.
    """

    output, recorded = _resolve_lazy_output(prepared, target)
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
    }, recorded


def _failed_step(
    graph: PipelineGraph,
    changed: frozenset[str],
    failure: Exception,
) -> FailedStep | None:
    """Name the step a node-code failure came from, when only one node can own it.

    The engine does not say which node raised, only the line of node code. When
    exactly one node this plan changed runs authored code and that node is
    stepped, the line is that node's, and the model authored its steps rather
    than the rendered program, so the step is what it can act on.
    """

    line = user_code_line(failure)
    if line is None:
        return None
    authored = [
        node
        for node in graph.nodes
        if node.id in changed
        and isinstance(code := node.data.config.get("code"), str)
        and code.strip()
    ]
    if len(authored) != 1:
        return None
    node = authored[0]
    if not is_stepped_config(node.data.nodeType, node.data.config):
        return None
    steps = node.data.config["steps"]
    rendered = render_polars_steps(steps, start=stepped_surface_for(node.data.nodeType).start)
    for index, (first, last) in enumerate(rendered.step_lines):
        if first <= line <= last:
            return FailedStep(node=node.id, number=index + 1, step_id=str(steps[index]["id"]))
    return None


def _schema_evidence(
    graph: PipelineGraph,
    targets: Sequence[str],
    *,
    baseline: PipelineGraph | None = None,
    changed: frozenset[str] = frozenset(),
    submitted: Sequence[Mapping[str, Any]] = (),
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

    The terminal records are followed by one `input_schema_inferred` record per
    Data Input in a resolved terminal's lineage whose schema came from its file
    because it has no snapshot yet, sorted by node id, then by one
    `input_schema_declared` record per API Input table whose schema came from
    its declared contract for the same reason, sorted by node id and table.

    `submitted` is the plan's operations payload, carried on a
    `SchemaUnresolvableError` so the tool boundary may name a column the
    model's own text names.
    """

    if not targets:
        return (), ()
    baseline_nodes = {node.id for node in baseline.nodes} if baseline is not None else set()
    prepared = _PreparedGraph.build(graph)
    prepared_baseline: _PreparedGraph | None = None
    evidence: list[Mapping[str, object]] = []
    inferred_inputs: dict[str, InferredInputSchema] = {}
    declared_tables: dict[tuple[str, str], DeclaredTableSchema] = {}
    warnings: list[str] = []
    for target in targets:
        try:
            target_evidence, recorded = _resolve_target_evidence(prepared, target)
            evidence.append(target_evidence)
            inferred_inputs.update(recorded.inferred)
            declared_tables.update(recorded.declared)
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
        raise SchemaUnresolvableError(
            target,
            failure,
            step=_failed_step(graph, changed, failure),
            graph=graph,
            submitted=submitted,
        ) from failure
    evidence.extend(
        _inferred_input_evidence(node, inferred_inputs[node]) for node in sorted(inferred_inputs)
    )
    evidence.extend(
        _declared_input_evidence(node, table, declared_tables[(node, table)])
        for node, table in sorted(declared_tables)
    )
    return tuple(evidence), tuple(warnings)


def _prove_steps_survive_save(
    graph: PipelineGraph,
    node_ids: Collection[str],
    *,
    source_file: str,
) -> None:
    """Reparse each stepped node in *node_ids* from the source a save would write.

    The planned source is generated in memory by the save path's codegen; each
    node's generated function is resolved by the parser's own node resolution
    against its sidecar exactly as ``collect_node_configs`` writes it (staged in
    a temporary directory, since the parser reads sidecars from disk). Steps the
    parser would discard, because the extracted body no longer matches their
    rendering, fail the plan before apply rather than turn the node code-only.
    """

    nodes = {node.id: node for node in graph.nodes}
    stepped = sorted(
        node_id
        for node_id in node_ids
        if (node := nodes.get(node_id)) is not None
        and is_stepped_config(node.data.nodeType, node.data.config)
        and not node.data.config.get("instanceOf")
    )
    if not stepped:
        return
    source = graph_to_code_multi(
        graph,
        pipeline_name=graph.pipeline_name or "",
        description=graph.pipeline_description or "",
        preamble=graph.preamble or "",
        source_file=source_file,
        preserved_blocks=graph.preserved_blocks or None,
    )[source_file]
    tree = ast.parse(source)
    skeletons = {
        skeleton.authored_id: skeleton
        for skeleton in _extract_decorated_node_skeletons(
            tree,
            _is_pipeline_authored_decorator,
            _extract_function_bodies(source, tree=tree),
            source=source,
        )
    }
    sidecars = collect_node_configs(graph)
    with TemporaryDirectory(prefix="haute-steps-") as directory:
        base_dir = Path(directory)
        for node_id in stepped:
            node = nodes[node_id]
            func_name = _sanitize_func_name(node.data.label)
            if node_emits_sidecar(node):
                relative = config_path_for_node(node.data.nodeType, func_name).as_posix()
                target = base_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(sidecars[relative].encode("utf-8"))
            try:
                reparsed = _resolve_node_skeleton(skeletons[func_name], base_dir)["config"]
            except (ConfigError, ParseError) as exc:
                raise AssistantOperationError(
                    "op_not_applied",
                    f"Node {node_id!r} would not reparse after save: {exc}",
                    where={"node": node_id, "field": "steps"},
                    fix=_steps_fix(node),
                ) from exc
            if "_steps_discarded" in reparsed or reparsed.get("steps") != node.data.config["steps"]:
                reason = reparsed.get("_steps_discarded") or "Its steps change on reparse."
                raise AssistantOperationError(
                    "op_not_applied",
                    f"Node {node_id!r} would lose its steps on save: {reason}",
                    where={"node": node_id, "field": "steps"},
                    fix=_steps_fix(node),
                )


def _steps_fix(node: GraphNode) -> str:
    """The correction for a step list that would not survive its save."""

    form = json.dumps(new_logic_steps(node.data.nodeType, "..."))
    return f"Write the node's new logic as {form}."


def _not_ready(node_id: str, message: str) -> AssistantOperationError:
    return AssistantOperationError(
        "node_not_ready",
        f"Node {node_id!r} is not ready: {message}",
        where={"node": node_id},
        fix=f"Configure node {node_id!r} so it is ready: {message}",
    )


def _prove_load_file_loads(node_id: str, config: Mapping[str, Any]) -> None:
    """Load a Load File's object as its node would when it runs.

    A deserialiser's own message can quote the file's content, so a failure
    other than a missing file or a Haute refusal is reported by type only.
    """

    path = config.get("path")
    file_type = config.get("fileType")
    if not isinstance(path, str) or not path.strip():
        raise _not_ready(node_id, "Load File has no path. Set the file it loads.")
    try:
        load_external_file_object(config)
    except FileNotFoundError:
        raise _not_ready(node_id, f"Load File path {path!r} does not exist.") from None
    except (HauteError, HauteValidationError) as exc:
        raise _not_ready(node_id, str(exc)) from exc
    except Exception as exc:
        raise _not_ready(
            node_id,
            f"Load File path {path!r} does not load as {file_type!r} ({type(exc).__name__}).",
        ) from exc


def _prove_nodes_ready(
    graph: PipelineGraph,
    node_ids: Collection[str],
    *,
    submitted: Sequence[Mapping[str, Any]],
) -> None:
    """Refuse a Modelling or Load File node in *node_ids* that is not ready to use.

    Save validation lets an analyst keep an unfinished node and refuses only
    malformed values; a node the assistant writes must also be usable. A
    Modelling node needs a target and a complete objective, and its configured
    columns must be in the input schema, checked by the function training
    preparation runs on the materialised schema. A Load File must load its file
    as its declared type: with empty steps it passes its input through and
    never loads the file, so schema resolution does not prove it.
    """

    nodes = {node.id: node for node in graph.nodes}
    ready_types = {NodeType.MODELLING, NodeType.EXTERNAL_FILE}
    written = sorted(
        node_id
        for node_id in node_ids
        if (node := nodes.get(node_id)) is not None
        and node.data.nodeType in ready_types
        and not node.data.config.get("instanceOf")
    )
    prepared: _PreparedGraph | None = None
    for node_id in written:
        node = nodes[node_id]
        config = node.data.config
        if node.data.nodeType == NodeType.EXTERNAL_FILE:
            _prove_load_file_loads(node_id, config)
            continue
        target = config.get("target")
        issue = (
            MISSING_TARGET_MESSAGE
            if not isinstance(target, str) or not target
            else training_objective_issue(config)
        )
        if issue is None:
            # Training refuses a config without a valid evaluation object, so a
            # node the assistant writes must carry one too.
            try:
                parse_evaluation_config(config.get("evaluation"))
            except TrainingConfigError as exc:
                issue = str(exc)
        if issue is not None:
            raise _not_ready(node_id, issue)
        if prepared is None:
            prepared = _PreparedGraph.build(graph)
        try:
            frame, _recorded = _resolve_lazy_output(prepared, node_id)
            schema = {name: str(dtype) for name, dtype in frame.collect_schema().items()}
        except Exception as exc:
            raise SchemaUnresolvableError(
                node_id, exc, step=None, graph=graph, submitted=submitted
            ) from exc
        try:
            build_training_feature_selection(config, schema)
        except HauteValidationError as exc:
            raise _not_ready(node_id, str(exc)) from exc


def build_verified_plan(
    snapshot: ProjectSnapshot,
    operations: Sequence[Mapping[str, Any]],
    postconditions: Sequence[Mapping[str, Any]] = (),
    *,
    validate_graph: GraphValidator,
    source_file: str,
) -> VerifiedPlan:
    """Build one plan through the shared edit and save-verification pipeline."""

    prepared = prepare_graph_edit(snapshot, operations, postconditions)
    try:
        warnings = validate_graph(prepared.result_graph)
        _prove_steps_survive_save(
            prepared.result_graph,
            prepared.diff.complete.written_nodes,
            source_file=source_file,
        )
        targets = _schema_validation_targets(prepared.result_graph, prepared.diff)
        evidence, schema_warnings = _schema_evidence(
            prepared.result_graph,
            targets,
            baseline=snapshot.graph,
            changed=_diff_seed_nodes(prepared.result_graph, prepared.diff),
            submitted=operations,
        )
        _prove_nodes_ready(
            prepared.result_graph, prepared.diff.complete.written_nodes, submitted=operations
        )
    except LocatedPlanError as exc:
        locate_plan_error(exc, prepared.writers, prepared.result_graph)
        raise
    plan = finalize_graph_edit_plan(
        prepared,
        validation_warnings=(*warnings, *schema_warnings),
        verification_tier="schema" if evidence else "structural",
        verification_evidence=evidence,
        # Resolving any target ran node code over the project's inputs, even
        # when every target was excused as a pre-existing failure.
        egress="schema-resolution" if targets else "none",
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
        summary: str,
        assumptions: Sequence[str] = (),
    ) -> DryRunResult:
        """Validate and retain an exact no-write plan against saved state.

        *summary* and *assumptions* are the plan's receipt, stored beside it
        for the change card the apply builds.
        """

        receipt = PlanReceipt(summary, tuple(assumptions))
        validate_declared_postconditions(postconditions)
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
            source_file=source_file,
        )
        self.plan_store.put(verified.plan, receipt)
        return DryRunResult(
            plan=verified.plan,
            changes=graph_changes(graph, verified.result_graph, verified.plan.diff),
        )

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
            source_file=source_file,
        )
        recomputed = verified.plan
        if recomputed.plan_hash != plan.plan_hash or recomputed != plan:
            raise AssistantOperationError(
                "invalid_plan",
                "The stored plan no longer matches its canonical payload",
            )
        return before, verified.result_graph, recomputed

    def _document_revision(self, source_file: str) -> str | None:
        source = self._source_path(source_file)
        return (
            load_pipeline_editor_document(source, project_root=self._project_root).source_revision
            if source.is_file()
            else None
        )

    def _save(
        self,
        source_file: str,
        graph: PipelineGraph,
        *,
        base_revision: str | None,
        commit_message: str,
    ) -> Any:
        return self._save_service().save_graph_transactionally(
            graph=graph,
            name=graph.pipeline_name or "",
            description=graph.pipeline_description or "",
            preamble=graph.preamble,
            source_file=source_file,
            base_revision=base_revision,
            commit_message=commit_message,
        )

    def _commit(
        self,
        source_file: str,
        after: PipelineGraph,
        receipt: PlanReceipt,
    ) -> Any:
        # Plan freshness was proven against the assistant snapshot under
        # ``save_lock``; the save precondition wants the editor protocol's
        # document revision, so read it now, still under that lock.
        return self._save(
            source_file,
            after,
            base_revision=self._document_revision(source_file),
            commit_message=change_headline(receipt.summary),
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
            receipt = self.plan_store.receipt(plan_hash)
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
                response = await asyncio.to_thread(self._commit, source_file, after, receipt)
            except BaseException:
                self.plan_store.abort_apply(plan_hash)
                raise

            # The committed save's record as the plan describes it: what a save
            # whose verification fails reports, so the analyst can undo it.
            planned: AssistantChangeRecord | None = None
            try:
                parent_sha = (
                    None
                    if response.git_sha is None
                    else await asyncio.to_thread(
                        commit_parent, response.git_sha, self._project_root
                    )
                )

                def saved_record(changes: AssistantGraphChanges) -> AssistantChangeRecord:
                    return change_record(
                        plan.plan_hash,
                        receipt,
                        changes,
                        warnings=response.warnings or (),
                        git_sha=response.git_sha,
                        parent_sha=parent_sha,
                        revision=response.source_revision,
                    )

                planned = saved_record(graph_changes(before, after, recomputed.diff))
                reparsed, result_revision, actual_diff, evidence = await asyncio.to_thread(
                    self._verify_commit,
                    source_file,
                    before,
                    recomputed,
                )
                change = saved_record(graph_changes(before, reparsed, actual_diff))
                fingerprint = self._publish_document_update(source_file, change)
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
                    change=change,
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
                    fallback_fingerprint = self._publish_document_update(source_file, planned)
                except Exception as publish_exc:  # noqa: BLE001 - preserve committed state
                    publish_error = type(publish_exc).__name__
                failure: dict[str, object] = {
                    "plan_hash": plan.plan_hash,
                    "verification_tier": plan.verification_tier,
                    "verification_status": "failed",
                    "verification_error_code": getattr(exc, "code", type(exc).__name__),
                    "graph_fingerprint": fallback_fingerprint,
                    "graph_publication_error": publish_error,
                    "warnings": list(response.warnings or ()),
                    "git_sha": response.git_sha,
                    "applied_operations": len(plan.normalized_operations),
                }
                if planned is not None:
                    failure["change"] = planned.model_dump(mode="json", exclude_defaults=True)
                self.plan_store.complete_apply(plan_hash, failure)
                raise CommittedVerificationError(
                    "The plan was committed, but structural verification failed; "
                    "review or undo the captured save before continuing.",
                    failure,
                ) from exc

    def _undo(self, source_file: str, change: AssistantChangeRecord, parent_sha: str) -> Any:
        # The explicit comparison names why an undo is refused; the save's own
        # base-revision precondition repeats it inside the transaction.
        if self._document_revision(source_file) != change.revision:
            raise AssistantOperationError(
                "undo_superseded",
                "The pipeline was saved again after this change, so it can no longer be "
                "undone here. Use the Git panel to return to an earlier version.",
            )
        return self._save(
            source_file,
            commit_pipeline_graph(parent_sha, source_file),
            base_revision=change.revision,
            commit_message=f"Undo: {change_headline(change.summary)}",
        )

    async def undo(self, source_file: str, change: AssistantChangeRecord) -> UndoResult:
        """Save the graph at *change*'s parent commit as a forward save.

        Allowed only while the pipeline is at the revision *change* produced, so
        only the latest change to the file is undone, never a later save.
        """

        parent_sha = change.parent_sha
        if parent_sha is None:
            raise AssistantOperationError(
                "undo_unavailable",
                "This change was not saved to Git, so there is no earlier version to return to.",
            )
        async with save_lock:
            response = await asyncio.to_thread(self._undo, source_file, change, parent_sha)
            self._publish_document_update(source_file, change)
        return UndoResult(git_sha=response.git_sha, revision=response.source_revision)


__all__ = [
    "ApplicationResult",
    "CommittedVerificationError",
    "DryRunResult",
    "PipelineApplicationService",
    "PreambleFailedError",
    "UndoResult",
    "VerifiedPlan",
    "build_verified_plan",
]
