"""Persistent, revision-bound proposals and recoverable multi-artifact commits.

The route owns the shared project mutation lock. Source is only generated in an
artifact-only temporary directory; editable draft JSON is never executable input.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal, cast

from pydantic import ValidationError

from haute._node_config_recovery import node_config_schema
from haute._pipeline_recovery import load_pipeline_editor_document
from haute._pipeline_repair import (
    PipelineRepairError,
    RepairArtifactEdit,
    _bounded_diff,
    _iter_recovery_nodes,
    _recovery_structure,
)
from haute._recovery_schemas import (
    RecoveryConfigContracts,
    RecoveryDraft,
    RecoveryDraftApply,
    RecoveryDraftApplyResponse,
    RecoveryDraftCreate,
    RecoveryDraftList,
    RecoveryDraftNode,
    RecoveryDraftPatch,
    RecoveryDraftPreview,
    RecoveryIssue,
)
from haute._recovery_sources import (
    _normalise_ports,
    _submodel_literals,
    apply_node_to_copy,
    authored_config_path,
    find_target,
    make_draft_node,
    static_code_issues,
    validate_node,
)
from haute._recovery_storage import (
    MAX_ARTIFACTS,
    MAX_DRAFTS,
    conflict,
    decode,
    digest,
    encode,
    load_record,
    read_artifact,
    record_ids,
    safe_path,
    save_record,
    snapshot,
)
from haute._types import NodeType
from haute.errors import HauteError
from haute.schemas import (
    PipelineEditorDocument,
    PipelineRepairChange,
    RecoveryGraphSnapshot,
    RecoveryPipelineEdge,
    RecoveryUnresolvedConnection,
)

_FINAL = {"applied", "restored", "discarded"}
_Plan = tuple[
    list[RepairArtifactEdit],
    dict[str, str],
    list[RecoveryIssue],
    Literal["ready", "degraded"] | None,
]
_Connection = tuple[str, str | None, str | None, str | None, str | None]


def contracts() -> RecoveryConfigContracts:
    schemas = {kind.value: node_config_schema(kind) for kind in NodeType}
    # Include installed source, defaults and adapters: a draft cannot outlive a code upgrade.
    package = Path(__file__).parent
    implementation = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        implementation.update(path.relative_to(package).as_posix().encode())
        implementation.update(path.read_bytes())
    implementation.update((package / "node_defaults.json").read_bytes())
    return RecoveryConfigContracts(
        fingerprint=digest([schemas, implementation.hexdigest()]),
        schemas=schemas,
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _draft(record: dict[str, Any]) -> RecoveryDraft:
    try:
        return RecoveryDraft.model_validate(record["draft"])
    except (ValidationError, KeyError) as exc:
        raise conflict("Recovery draft is corrupt; the stored evidence has been retained.") from exc


def _store(root: Path, record: dict[str, Any], draft: RecoveryDraft, *, bump: bool = True) -> None:
    if bump:
        draft.draft_revision = uuid.uuid4().hex
        draft.updated_at = _now()
    record["draft"] = draft.model_dump(mode="json")
    save_record(root, record)


def _state(draft: RecoveryDraft) -> None:
    errors = [issue for node in draft.nodes for issue in node.issues if issue.severity == "error"]
    errors.extend(issue for issue in draft.issues if issue.severity == "error")
    if any(not node.editable for node in draft.nodes):
        draft.state = "manual_action"
    elif errors:
        draft.state = "needs_configuration"
    elif not draft.reviewed:
        draft.state = "review_required"
    else:
        draft.state = "ready_to_apply"


def _require_revision(draft: RecoveryDraft, revision: str) -> None:
    if draft.draft_revision != revision:
        raise conflict("The recovery draft changed; reload the draft before saving or applying.")


def _fresh(root: Path, record: dict[str, Any], draft: RecoveryDraft) -> bool:
    roots, current = snapshot(root, draft.source_file)
    for name in record.get("extras", []):
        current[name] = encode(read_artifact(root, name))
    return (
        roots == record["roots"]
        and current == record["artifacts"]
        and draft.contract_fingerprint == contracts().fingerprint
    )


def _require_fresh(root: Path, record: dict[str, Any], draft: RecoveryDraft) -> None:
    if not _fresh(root, record, draft):
        raise conflict(
            "Source files or installed node definitions changed. "
            "The draft is retained; rebuild and compare it before applying.",
            "recovery_stale",
        )


def _load(root: Path, draft_id: str) -> tuple[dict[str, Any], RecoveryDraft]:
    record = load_record(root, draft_id)
    if record.get("journal"):
        _recover_journal(root, record)
    return record, _draft(record)


def get_draft(root: Path, draft_id: str) -> RecoveryDraft:
    root = root.resolve()
    record, draft = _load(root, draft_id)
    if draft.state not in _FINAL and draft.state != "applying" and not _fresh(root, record, draft):
        draft.state = "stale"
    return draft


def list_drafts(root: Path, source_file: str) -> RecoveryDraftList:
    root = root.resolve()
    safe_path(root, source_file)
    drafts = [get_draft(root, identity) for identity in record_ids(root)]
    return RecoveryDraftList(
        drafts=sorted(
            (draft for draft in drafts if draft.source_file == source_file),
            key=lambda draft: draft.updated_at,
            reverse=True,
        )
    )


def _owners(root: Path, roots: list[str], nodes: list[RecoveryDraftNode]) -> None:
    documents = [
        load_pipeline_editor_document(safe_path(root, path), project_root=root) for path in roots
    ]
    for node in nodes:
        selection = next(
            (
                (document, candidate)
                for document in documents
                for candidate in _iter_recovery_nodes(document)
                if candidate.source_file == node.source_file
                and candidate.authored_id == node.authored_id
            ),
            None,
        )
        selected_path = authored_config_path(root, *selection) if selection else None
        owners = set(node.affected_owners)
        for document in documents:
            for other in _iter_recovery_nodes(document):
                same_source = (
                    other.source_file == node.source_file and other.authored_id == node.authored_id
                )
                shared_config = bool(
                    selected_path and authored_config_path(root, document, other) == selected_path
                )
                instance = (other.config or {}).get("instanceOf") == node.authored_id
                shared_definition = node.node_type == NodeType.SUBMODEL and (
                    other.config or {}
                ).get("definitionId") == node.config.get("definition_id")
                if same_source or shared_config or instance or shared_definition:
                    owners.add(f"{document.source_file}: {other.label}")
        node.affected_owners = sorted(owners)


def create_draft(root: Path, request: RecoveryDraftCreate) -> RecoveryDraft:
    root = root.resolve()
    if len(record_ids(root)) >= MAX_DRAFTS:
        raise conflict(
            "Recovery history is full; archive old recovery records before creating another."
        )
    source = safe_path(root, request.source_file)
    document = load_pipeline_editor_document(source, project_root=root)
    if document.source_revision != request.source_revision:
        raise conflict("The pipeline changed; reload it before creating a recovery draft.")
    roots, artifacts = snapshot(root, document.source_file)
    targets = [
        find_target(document, target.source_file, target.recovery_id) for target in request.targets
    ]
    if len({(target.source_file, target.authored_id) for target in targets}) != len(targets):
        raise conflict("A recovery group cannot select the same authored node twice.")
    nodes = [
        make_draft_node(root, document, target, reset=request.mode == "reset") for target in targets
    ]
    _owners(root, roots, nodes)
    draft = RecoveryDraft(
        draft_id=uuid.uuid4().hex,
        draft_revision=uuid.uuid4().hex,
        source_file=document.source_file,
        source_revision=document.source_revision,
        contract_fingerprint=contracts().fingerprint,
        mode=request.mode,
        state="review_required",
        nodes=nodes,
        updated_at=_now(),
    )
    _state(draft)
    record: dict[str, Any] = {
        "format": 1,
        "draft": draft.model_dump(mode="json"),
        "roots": roots,
        "artifacts": artifacts,
        "extras": [],
        "operations": {},
        "journal": None,
        "original_nodes": {node.key: node.model_dump(mode="json") for node in nodes},
        "applied_edits": [],
    }
    _require_fresh(root, record, draft)
    save_record(root, record)
    return draft


def edit_draft(root: Path, draft_id: str, request: RecoveryDraftPatch) -> RecoveryDraft:
    root = root.resolve()
    record, draft = _load(root, draft_id)
    _require_revision(draft, request.draft_revision)
    _require_fresh(root, record, draft)
    if draft.state in _FINAL:
        raise conflict("An applied, restored or discarded recovery record cannot be edited.")
    keys = {node.key for node in draft.nodes}
    if request.configs.keys() - keys:
        raise conflict("The edit contains nodes outside this recovery group.")
    for node in draft.nodes:
        if node.key not in request.configs:
            continue
        if not node.editable and request.configs[node.key] != node.config:
            raise conflict(
                "A node requiring a manual source action cannot be edited in this draft."
            )
        next_config = deepcopy(request.configs[node.key])
        if node.node_type == NodeType.SUBMODEL:
            for identity in ("name", "definition_id", "instance_of"):
                if next_config.get(identity) != node.config.get(identity):
                    raise conflict(f"The submodel {identity} is immutable in a recovery draft.")
            for field in ("input_ports", "output_ports"):
                previous_ports = record["original_nodes"][node.key]["config"].get(field)
                next_ports = next_config.get(field)
                if isinstance(previous_ports, list) and isinstance(next_ports, list):
                    original_names = {
                        port.get("name")
                        for port in previous_ports
                        if isinstance(port, dict) and isinstance(port.get("name"), str)
                    }
                    next_names = {
                        port.get("name")
                        for port in next_ports
                        if isinstance(port, dict) and isinstance(port.get("name"), str)
                    }
                    if not original_names <= next_names:
                        raise conflict(
                            "Recovery cannot rename or remove existing public port identities."
                        )
            if next_config.get("file") != node.config.get("file"):
                try:
                    _reg, child, _fields, replacement = _submodel_literals(
                        root,
                        node.source_file,
                        node.authored_id,
                        file=str(next_config["file"]),
                    )
                    definition_identity = node.config.get("definition_id")
                    if definition_identity and definition_identity != replacement["definition_id"]:
                        raise conflict("The replacement file belongs to a different definition.")
                    next_config = replacement
                    _normalise_ports(next_config)
                    from haute._pipeline_recovery import _source_references

                    paths = {
                        child.relative_to(root).as_posix(),
                        child.with_suffix(".haute.json").relative_to(root).as_posix(),
                    }
                    references, registrations = _source_references(
                        child.read_bytes().decode("utf-8-sig"), child=True
                    )
                    if registrations:
                        raise conflict(
                            "Nested submodels are unsupported; the file cannot be relinked."
                        )
                    for reference in references:
                        paths.add((Path(node.source_file).parent / reference).as_posix())
                    for relative in paths:
                        if relative not in record["artifacts"]:
                            record["artifacts"][relative] = encode(read_artifact(root, relative))
                            record["extras"].append(relative)
                except (PipelineRepairError, HauteError, UnicodeError) as exc:
                    node.config = next_config
                    node.issues = [
                        RecoveryIssue(path="/file", code="invalid_definition", message=str(exc))
                    ]
                    continue
        node.config = next_config
        node.issues = validate_node(node)
    if len(record["artifacts"]) > MAX_ARTIFACTS:
        raise conflict("Recovery scope exceeds the artifact limit.")
    draft.reviewed = request.reviewed
    draft.issues = []
    _state(draft)
    _store(root, record, draft)
    return draft


def discard_draft(root: Path, draft_id: str, revision: str) -> RecoveryDraft:
    root = root.resolve()
    record, draft = _load(root, draft_id)
    _require_revision(draft, revision)
    if draft.state in {"applied", "restored", "applying"}:
        raise conflict("Applied recovery evidence cannot be discarded; use Restore instead.")
    draft.state = "discarded"
    _store(root, record, draft)
    return draft


def _validate_conservation(before: PipelineEditorDocument, after: PipelineEditorDocument) -> None:
    originals = {
        (node.source_file, node.authored_id): node for node in _iter_recovery_nodes(before)
    }
    proposed = {(node.source_file, node.authored_id): node for node in _iter_recovery_nodes(after)}
    if after.load_status == "source_only" or not originals.keys() <= proposed.keys():
        raise conflict("The proposal would lose authored nodes or make the source unreadable.")
    if any(
        node.availability == "ready" and proposed[key].availability != "ready"
        for key, node in originals.items()
    ):
        raise conflict("The proposal would break a currently ready node or shared occurrence.")

    # Connection calls themselves are never rewritten by this recovery operation.
    # Compare resolved plus unresolved semantic identities: recovery may resolve an edge.
    def connections(doc: PipelineEditorDocument) -> list[_Connection]:
        result: list[_Connection] = []

        def visit(graph: PipelineEditorDocument | RecoveryGraphSnapshot, identity: str) -> None:
            graph_edges: list[RecoveryPipelineEdge | RecoveryUnresolvedConnection] = [
                *graph.edges,
                *graph.unresolved_connections,
            ]
            for edge in graph_edges:
                source_port = edge.source_port or getattr(edge, "source_handle", None)
                target_port = edge.target_port or getattr(edge, "target_handle", None)
                if source_port and source_port.startswith("out__"):
                    source_port = source_port[5:]
                if target_port and target_port.startswith("in__"):
                    target_port = target_port[4:]
                result.append(
                    (
                        identity,
                        edge.source_authored_id,
                        edge.target_authored_id,
                        source_port,
                        target_port,
                    )
                )
            for key, definition in (graph.submodels or {}).items():
                visit(definition.graph, key)

        visit(doc, "")
        return result

    from collections import Counter

    if Counter(connections(before)) - Counter(connections(after)):
        raise conflict("The proposal would lose an authored connection or port binding.")


def _build_plan(root: Path, record: dict[str, Any], draft: RecoveryDraft) -> _Plan:
    from haute._config_io import _prepare_config_for_sidecar

    edits: list[RepairArtifactEdit] = []
    expected: dict[str, str] = {}
    issues: list[RecoveryIssue] = []
    original_document = load_pipeline_editor_document(root / draft.source_file, project_root=root)
    proposals: dict[Path, str] = {}
    for node in draft.nodes:
        target = find_target(original_document, node.source_file, node.recovery_id)
        shared_path = authored_config_path(root, original_document, target)
        if shared_path is None:
            continue
        if node.node_type == NodeType.SUBMODEL:
            proposed_settings = {
                field: node.config.get(field)
                for field in ("input_ports", "output_ports", "definition_id")
            }
        else:
            if node.node_type is None:
                raise conflict("This node type is not installed.")
            proposed_settings = _prepare_config_for_sidecar(NodeType(node.node_type), node.config)
        proposed_digest = digest(proposed_settings)
        if shared_path in proposals and proposals[shared_path] != proposed_digest:
            raise conflict(
                "The recovery group proposes conflicting settings for a shared artifact. "
                "Use the same settings for all its owners."
            )
        proposals[shared_path] = proposed_digest
    with TemporaryDirectory(prefix="haute-recovery-") as directory:
        copy = Path(directory)
        for relative, encoded in record["artifacts"].items():
            content = decode(encoded)
            if content is not None:
                path = safe_path(copy, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        if not (copy / "haute.toml").exists():
            (copy / "haute.toml").write_text('[project]\nname="recovery-preview"\n')
        remaining = sorted(draft.nodes, key=lambda node: node.node_type != NodeType.SUBMODEL)
        # Structural adapters first, then dependency order; unresolved nodes remain editable.
        while remaining:
            failures: list[RecoveryIssue] = []
            progress = False
            for node in list(remaining):
                document = load_pipeline_editor_document(
                    copy / draft.source_file, project_root=copy
                )
                try:
                    apply_node_to_copy(copy, document, node)
                except (PipelineRepairError, HauteError, ValueError) as exc:
                    failures.append(
                        RecoveryIssue(path=node.key, code="source_plan_blocked", message=str(exc))
                    )
                    continue
                remaining.remove(node)
                progress = True
            if not progress:
                return [], {}, failures, None
        for node in draft.nodes:
            issues.extend(static_code_issues(copy, node))
        for relative in record["roots"]:
            before = load_pipeline_editor_document(root / relative, project_root=root)
            after = load_pipeline_editor_document(copy / relative, project_root=copy)
            _validate_conservation(before, after)
            expected[relative] = _recovery_structure(after)
        active = load_pipeline_editor_document(copy / draft.source_file, project_root=copy)
        for node in draft.nodes:
            recovered = find_target(active, node.source_file, node.recovery_id)
            if recovered.availability == "unavailable":
                messages = [
                    diagnostic.message
                    for diagnostic in active.diagnostics
                    if diagnostic.element_id == recovered.recovery_id
                ]
                issues.append(
                    RecoveryIssue(
                        path=node.key,
                        code="still_unavailable",
                        message="The candidate still cannot load. " + " ".join(messages[:3]),
                    )
                )
        files = {p.relative_to(copy).as_posix() for p in copy.rglob("*") if p.is_file()}
        for relative in sorted(files | record["artifacts"].keys()):
            before_bytes = decode(record["artifacts"].get(relative))
            after_bytes = read_artifact(copy, relative)
            # The temporary project marker is not an authored repair artifact.
            if relative == "haute.toml" or before_bytes == after_bytes:
                continue
            edits.append(
                RepairArtifactEdit(
                    path=safe_path(root, relative),
                    wire_path=relative,
                    before=before_bytes,
                    after=after_bytes,
                    description="Apply the reviewed recovery candidate.",
                )
            )
        return edits, expected, issues, cast(Literal["ready", "degraded"], active.load_status)


def _edit_json(edit: RepairArtifactEdit) -> dict[str, Any]:
    return {"path": edit.wire_path, "before": encode(edit.before), "after": encode(edit.after)}


def _plan(root: Path, record: dict[str, Any], draft: RecoveryDraft, *, restore: bool) -> _Plan:
    if restore:
        if draft.state != "applied":
            raise conflict("Only an applied recovery can be restored.")
        edits = []
        for entry in record["applied_edits"]:
            current = read_artifact(root, entry["path"])
            if current != decode(entry["after"]):
                raise conflict(
                    "An applied artifact has changed. Restore would overwrite later work."
                )
            edits.append(
                RepairArtifactEdit(
                    path=safe_path(root, entry["path"]),
                    wire_path=entry["path"],
                    before=current,
                    after=decode(entry["before"]),
                    description="Restore the exact artifact bytes saved before recovery.",
                )
            )
        return edits, record["original_structures"], [], None
    _require_fresh(root, record, draft)
    if draft.state in _FINAL:
        raise conflict("This recovery record is no longer an editable draft.")
    issues = [issue for node in draft.nodes for issue in node.issues if issue.severity == "error"]
    if issues or any(not node.editable for node in draft.nodes):
        return [], {}, issues, None
    return _build_plan(root, record, draft)


def _hash(
    draft: RecoveryDraft,
    edits: list[RepairArtifactEdit],
    expected: dict[str, str],
    *,
    restore: bool,
) -> str:
    return digest(
        [draft.model_dump(mode="json"), [_edit_json(edit) for edit in edits], expected, restore]
    )


def preview_draft(
    root: Path, draft_id: str, draft_revision: str, *, restore: bool = False
) -> RecoveryDraftPreview:
    root = root.resolve()
    record, draft = _load(root, draft_id)
    _require_revision(draft, draft_revision)
    try:
        edits, expected, issues, status = _plan(root, record, draft, restore=restore)
    except PipelineRepairError as exc:
        if exc.code == "recovery_stale" or restore:
            raise
        return RecoveryDraftPreview(
            draft=draft, issues=[RecoveryIssue(code=exc.code, message=str(exc))]
        )
    can_apply = bool(edits) and not any(issue.severity == "error" for issue in issues)
    if not edits and not issues:
        issues = [
            RecoveryIssue(code="no_changes", message="No source or setting changes are needed.")
        ]
    return RecoveryDraftPreview(
        draft=draft,
        plan_hash=_hash(draft, edits, expected, restore=restore) if can_apply else None,
        changes=[
            PipelineRepairChange(
                path=edit.wire_path,
                operation=edit.operation,
                description=edit.description,
                diff=_bounded_diff(edit)[0],
                diff_truncated=_bounded_diff(edit)[1],
            )
            for edit in edits
        ],
        issues=issues,
        predicted_load_status=status,
    )


def _write_entries(root: Path, entries: list[dict[str, Any]]) -> None:
    from haute.routes._save_pipeline import _stage_artifact_delete, _stage_artifact_write_bytes

    for entry in entries:
        if read_artifact(root, entry["path"]) != decode(entry["before"]):
            raise conflict("An artifact changed during recovery; the transaction was stopped.")
        path = safe_path(root, entry["path"])
        after = decode(entry["after"])
        if after is None:
            _stage_artifact_delete(path, [])
        else:
            _stage_artifact_write_bytes(path, after, [])
            with path.open("r+b") as handle:
                os.fsync(handle.fileno())


def _finish_operation(root: Path, record: dict[str, Any]) -> RecoveryDraftApplyResponse:
    journal = record["journal"]
    draft = _draft(record)
    for relative, structure in journal["expected"].items():
        document = load_pipeline_editor_document(root / relative, project_root=root)
        if _recovery_structure(document) != structure:
            raise conflict("Applied artifacts do not match the validated graph structure.")
    draft.state = "restored" if journal["restore"] else "applied"
    draft.draft_revision = uuid.uuid4().hex
    draft.updated_at = _now()
    document = load_pipeline_editor_document(root / draft.source_file, project_root=root)
    # A separate editor need not participate in Haute's mutation lock. Verify
    # bytes again after parsing, before recording a successful commit.
    if any(
        read_artifact(root, entry["path"]) != decode(entry["after"]) for entry in journal["edits"]
    ):
        raise conflict("An artifact changed during recovery verification.")
    result = RecoveryDraftApplyResponse(
        draft=draft,
        document=document,
        applied_artifacts=[entry["path"] for entry in journal["edits"]],
    )
    if not journal["restore"]:
        record["applied_edits"] = journal["edits"]
    record["operations"][journal["operation_id"]] = {
        "request_hash": journal["request_hash"],
        "response": result.model_dump(mode="json"),
    }
    record["journal"] = None
    _store(root, record, draft, bump=False)
    return result


def _recover_journal(root: Path, record: dict[str, Any], *, force_rollback: bool = False) -> None:
    """Finish a complete commit or roll back a partial commit using only expected bytes."""
    journal = record["journal"]
    entries = journal["edits"]
    currents = {entry["path"]: encode(read_artifact(root, entry["path"])) for entry in entries}
    if not force_rollback and all(currents[entry["path"]] == entry["after"] for entry in entries):
        try:
            _finish_operation(root, record)
            return
        except PipelineRepairError:
            pass
    if any(currents[entry["path"]] not in (entry["before"], entry["after"]) for entry in entries):
        raise conflict(
            "An interrupted recovery overlaps an external edit. Its journal and original "
            "bytes are retained; reconcile those files manually.",
            "recovery_journal_conflict",
        )
    rollback = [
        {"path": entry["path"], "before": entry["after"], "after": entry["before"]}
        for entry in reversed(entries)
        if currents[entry["path"]] == entry["after"]
    ]
    _write_entries(root, rollback)
    record["draft"] = journal["previous_draft"]
    record["journal"] = None
    save_record(root, record)


def recover_pending_drafts(root: Path) -> None:
    for identity in record_ids(root.resolve()):
        record = load_record(root.resolve(), identity)
        if record.get("journal"):
            _recover_journal(root.resolve(), record)


def apply_draft(
    root: Path, draft_id: str, request: RecoveryDraftApply, *, restore: bool = False
) -> RecoveryDraftApplyResponse:
    root = root.resolve()
    record, draft = _load(root, draft_id)
    request_hash = digest([request.model_dump(mode="json"), restore])
    previous = record["operations"].get(request.operation_id)
    if previous:
        if previous["request_hash"] != request_hash:
            raise conflict(
                "An operation identity cannot be reused for a different recovery request."
            )
        return RecoveryDraftApplyResponse.model_validate(previous["response"])
    _require_revision(draft, request.draft_revision)
    current_document = load_pipeline_editor_document(root / draft.source_file, project_root=root)
    if current_document.source_revision != request.source_revision:
        raise conflict("The pipeline changed after this recovery preview.")
    if not restore and not draft.reviewed:
        raise conflict("Review the settings and affected owners before applying recovery.")
    edits, expected, issues, _status = _plan(root, record, draft, restore=restore)
    if not edits or any(issue.severity == "error" for issue in issues):
        raise conflict("The recovery proposal is incomplete; correct its issues before applying.")
    if request.plan_hash != _hash(draft, edits, expected, restore=restore):
        raise conflict("The recovery plan changed; review a fresh diff before applying.")
    for edit in edits:
        if read_artifact(root, edit.wire_path) != edit.before:
            raise conflict("A repair artifact changed after the preview.")
    record["original_structures"] = record.get("original_structures") or {
        relative: _recovery_structure(
            load_pipeline_editor_document(root / relative, project_root=root)
        )
        for relative in record["roots"]
    }
    journal: dict[str, Any] = {
        "operation_id": request.operation_id,
        "request_hash": request_hash,
        "restore": restore,
        "edits": [_edit_json(edit) for edit in edits],
        "expected": expected,
        "previous_draft": draft.model_dump(mode="json"),
    }
    record["journal"] = journal
    draft.state = "applying"
    _store(
        root, record, draft, bump=False
    )  # Durable evidence and intent precede the first source write.
    try:
        _write_entries(root, journal["edits"])
        return _finish_operation(root, record)
    except BaseException:
        # Read the durable journal again: a final record write may itself have failed.
        pending = load_record(root, draft_id)
        if pending.get("journal"):
            _recover_journal(root, pending, force_rollback=True)
        raise
