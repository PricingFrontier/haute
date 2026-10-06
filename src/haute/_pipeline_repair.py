"""Revision-safe repair planning and shared transactional application.

This module deliberately does not perform graph code generation.  It plans
exact edits against server-reloaded recovery identities and leaves writes to
the route's shared save transaction boundary.
"""

from __future__ import annotations

import ast
import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from haute._ast_helpers import (
    _chained_receiver_calls,
    _connect_call_edge,
    _extract_function_bodies,
    _is_pipeline_authored_decorator,
    _is_submodel_authored_decorator,
)
from haute._cache import canonical_json
from haute._graph_builders import PipelineNodeSkeleton, _extract_decorated_node_skeletons
from haute._pipeline_recovery import load_pipeline_editor_document
from haute.errors import HauteError
from haute.parser import parse_pipeline_file
from haute.schemas import (
    PipelineEditorDocument,
    PipelineRepairApplyResponse,
    PipelineRepairChange,
    PipelineRepairPlanResponse,
    PipelineRepairRecoverRequest,
    PipelineRepairRemoveRequest,
    RecoveryGraphSnapshot,
    RecoveryPipelineNode,
    RecoveryUnresolvedConnection,
)

_UTF8_BOM = b"\xef\xbb\xbf"
_MAX_PUBLIC_DIFF = 131_072


class PipelineRepairError(ValueError):
    """Expected remove-only planning conflict with safe structured detail."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        **fields: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.fields = fields

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.fields}


@dataclass(frozen=True, slots=True)
class RepairArtifactEdit:
    """One complete internal edit; bytes never cross the HTTP boundary."""

    path: Path
    wire_path: str
    before: bytes | None
    after: bytes | None
    description: str
    expose_diff: bool = True

    @property
    def operation(self) -> Literal["update", "delete"]:
        return "delete" if self.after is None else "update"


@dataclass(frozen=True, slots=True)
class PipelineRepairPlan:
    """Internal plan paired with its bounded public representation."""

    response: PipelineRepairPlanResponse
    edits: tuple[RepairArtifactEdit, ...]
    root_path: Path
    target_path: Path
    expected_structure: str | None = None


@dataclass(frozen=True, slots=True)
class _JsonMember:
    key: str
    key_start: int
    value_start: int
    value_end: int
    comma_after: int | None


def _wire_path(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as exc:  # pragma: no cover - every caller validates first
        raise PipelineRepairError(
            "repair_path_outside_project",
            "A repair artifact resolves outside the project directory.",
            status_code=403,
        ) from exc


def _resolve_project_file(project_root: Path, raw_path: str, *, suffix: str) -> Path:
    if not raw_path.strip() or "\x00" in raw_path:
        raise PipelineRepairError(
            "repair_path_invalid",
            "Repair source identity is empty or malformed.",
            status_code=400,
        )
    root = project_root.resolve()
    candidate = Path(raw_path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise PipelineRepairError(
            "repair_path_outside_project",
            "Repair source identity resolves outside the project directory.",
            status_code=403,
        )
    if resolved.suffix.casefold() != suffix.casefold():
        raise PipelineRepairError(
            "repair_path_invalid",
            f"Repair source must be a {suffix} file.",
            status_code=400,
        )
    if not resolved.is_file():
        raise PipelineRepairError(
            "repair_source_missing",
            "The pipeline source selected for repair no longer exists.",
            status_code=409,
        )
    return resolved


def _resolve_config_reference(
    *,
    root_path: Path,
    project_root: Path,
    reference: str,
) -> Path:
    candidate = Path(reference)
    resolved = (candidate if candidate.is_absolute() else root_path.parent / candidate).resolve()
    if not resolved.is_relative_to(project_root.resolve()):
        raise PipelineRepairError(
            "repair_config_outside_project",
            "The node's referenced config resolves outside the project directory.",
            status_code=403,
        )
    return resolved


def _iter_recovery_nodes(
    graph: PipelineEditorDocument | RecoveryGraphSnapshot,
) -> list[RecoveryPipelineNode]:
    nodes = list(graph.nodes)
    for definition in (graph.submodels or {}).values():
        nodes.extend(_iter_recovery_nodes(definition.graph))
    return nodes


def _iter_unresolved_connections(
    graph: PipelineEditorDocument | RecoveryGraphSnapshot,
) -> list[RecoveryUnresolvedConnection]:
    connections = list(graph.unresolved_connections)
    for definition in (graph.submodels or {}).values():
        connections.extend(_iter_unresolved_connections(definition.graph))
    return connections


def _find_target(
    document: PipelineEditorDocument,
    *,
    target_source_file: str,
    target_recovery_id: str,
    require_unavailable: bool = True,
) -> RecoveryPipelineNode:
    normalised_source = target_source_file.replace("\\", "/").casefold()
    matches = [
        node
        for node in _iter_recovery_nodes(document)
        if node.recovery_id == target_recovery_id
        and (node.source_file or "").replace("\\", "/").casefold() == normalised_source
    ]
    if len(matches) != 1:
        raise PipelineRepairError(
            "repair_target_not_unique",
            "The unavailable-node identity no longer resolves to exactly one element.",
            match_count=len(matches),
        )
    target = matches[0]
    if require_unavailable and target.availability != "unavailable":
        raise PipelineRepairError(
            "repair_target_not_unavailable",
            "Only an unavailable node can be changed through recovery repair.",
            availability=target.availability,
        )
    if require_unavailable and target.source_span is None:
        raise PipelineRepairError(
            "repair_target_span_missing",
            "The unavailable node has no trustworthy source span; open the source manually.",
        )
    same_authored_identity = [
        node
        for node in _iter_recovery_nodes(document)
        if (node.source_file or "").replace("\\", "/").casefold() == normalised_source
        and node.authored_id == target.authored_id
    ]
    if len(same_authored_identity) != 1:
        raise PipelineRepairError(
            "repair_target_authored_identity_ambiguous",
            "Duplicate authored node identities cannot be removed automatically.",
            authored_id=target.authored_id,
            match_count=len(same_authored_identity),
        )
    return target


def _decode_utf8_artifact(raw: bytes, *, artifact: str) -> tuple[bytes, str, bytes]:
    prefix = _UTF8_BOM if raw.startswith(_UTF8_BOM) else b""
    body = raw[len(prefix) :]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PipelineRepairError(
            "repair_encoding_unsupported",
            f"{artifact} is not valid UTF-8 and cannot be patched safely.",
        ) from exc
    return prefix, text, body


def _line_byte_range(body: bytes, start_line: int, end_line: int) -> tuple[int, int]:
    lines = body.splitlines(keepends=True)
    if not lines and start_line == end_line == 1:
        return 0, 0
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise PipelineRepairError(
            "repair_span_shifted",
            "The recorded source span no longer fits the current file.",
            start_line=start_line,
            end_line=end_line,
        )
    return sum(map(len, lines[: start_line - 1])), sum(map(len, lines[:end_line]))


def _apply_byte_ranges(body: bytes, ranges: list[tuple[int, int]]) -> bytes:
    ordered = sorted(ranges, reverse=True)
    prior_start = len(body) + 1
    updated = body
    for start, end in ordered:
        if start < 0 or end < start or end > len(body) or end > prior_start:
            raise PipelineRepairError(
                "repair_span_ambiguous",
                "Repair source spans overlap or are no longer trustworthy.",
            )
        updated = updated[:start] + updated[end:]
        prior_start = start
    return updated


def _extract_skeletons(
    source: str,
    *,
    receiver: str,
) -> tuple[ast.Module, list[PipelineNodeSkeleton]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise PipelineRepairError(
            "repair_syntax_unsupported",
            "Syntax-broken source must be corrected in an editor before repairing.",
        ) from None

    predicate = (
        _is_pipeline_authored_decorator
        if receiver == "pipeline"
        else _is_submodel_authored_decorator
    )
    bodies = _extract_function_bodies(source, tree=tree)
    return (
        tree,
        list(
            _extract_decorated_node_skeletons(
                tree,
                predicate,
                bodies,
                source=source,
            )
        ),
    )


def _implicit_consumers(
    skeletons: list[PipelineNodeSkeleton],
    *,
    target_authored_id: str,
) -> list[dict[str, str]]:
    consumers: list[dict[str, str]] = []
    for skeleton in skeletons:
        if skeleton.authored_id == target_authored_id:
            continue
        if target_authored_id in skeleton.param_names:
            consumers.append(
                {
                    "function": skeleton.authored_id,
                    "parameter": target_authored_id,
                }
            )
    return consumers


def _connection_links(statement: ast.stmt, *, receiver: str) -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    for link in _chained_receiver_calls(statement, receiver=receiver, method="connect"):
        try:
            edge = _connect_call_edge(link, receiver)
        except Exception as exc:
            raise PipelineRepairError(
                "repair_connection_declaration_invalid",
                "An authored connection cannot be interpreted safely for removal.",
            ) from exc
        if edge is None:
            raise PipelineRepairError(
                "repair_connection_declaration_invalid",
                "An authored connection cannot be interpreted safely for removal.",
            )
        edges.append((edge[0], edge[1]))
    return edges


def _connection_line_ranges(
    tree: ast.Module,
    source: str,
    body: bytes,
    *,
    receiver: str,
    target_authored_id: str,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for statement in tree.body:
        links = _connection_links(statement, receiver=receiver)
        if not links:
            continue
        references_target = [target_authored_id in edge for edge in links]
        if not any(references_target):
            continue
        if not all(references_target):
            raise PipelineRepairError(
                "repair_connection_chain_mixed",
                "A chained connection statement also contains an unrelated connection; "
                "split it before removing the node.",
            )
        if statement.col_offset != 0:
            raise PipelineRepairError(
                "repair_connection_span_ambiguous",
                "A connection declaration is not a standalone top-level statement.",
            )
        statement_end_line = statement.end_lineno or statement.lineno
        source_lines = body.splitlines(keepends=True)
        end_column = statement.end_col_offset
        if (
            end_column is None
            or statement_end_line > len(source_lines)
            or source_lines[statement_end_line - 1].rstrip(b"\r\n")[end_column:].strip()
        ):
            raise PipelineRepairError(
                "repair_connection_span_ambiguous",
                "A connection declaration shares its line with authored content.",
            )
        if any(
            other is not statement
            and other.lineno <= statement_end_line
            and (other.end_lineno or other.lineno) >= statement.lineno
            for other in tree.body
        ):
            raise PipelineRepairError(
                "repair_connection_span_ambiguous",
                "A connection declaration shares a source line with another statement.",
            )
        ranges.append(
            _line_byte_range(
                body,
                statement.lineno,
                statement_end_line,
            )
        )
    return ranges


def _skip_json_ws(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _json_object_members(text: str, object_start: int) -> list[_JsonMember]:
    decoder = json.JSONDecoder()
    if object_start >= len(text) or text[object_start] != "{":
        raise PipelineRepairError(
            "repair_sidecar_invalid",
            "The position sidecar is not a JSON object and cannot be patched safely.",
        )
    members: list[_JsonMember] = []
    index = _skip_json_ws(text, object_start + 1)
    if index < len(text) and text[index] == "}":
        return members
    while index < len(text):
        key_start = index
        try:
            key, key_end = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            raise PipelineRepairError(
                "repair_sidecar_invalid",
                "The position sidecar cannot be patched safely.",
            ) from exc
        if not isinstance(key, str):
            raise PipelineRepairError(
                "repair_sidecar_invalid",
                "The position sidecar contains a non-string object key.",
            )
        index = _skip_json_ws(text, key_end)
        if index >= len(text) or text[index] != ":":
            raise PipelineRepairError(
                "repair_sidecar_invalid",
                "The position sidecar cannot be patched safely.",
            )
        value_start = _skip_json_ws(text, index + 1)
        try:
            _value, value_end = decoder.raw_decode(text, value_start)
        except json.JSONDecodeError as exc:
            raise PipelineRepairError(
                "repair_sidecar_invalid",
                "The position sidecar cannot be patched safely.",
            ) from exc
        index = _skip_json_ws(text, value_end)
        comma_after: int | None = None
        if index < len(text) and text[index] == ",":
            comma_after = index
            index = _skip_json_ws(text, index + 1)
        elif index >= len(text) or text[index] != "}":
            raise PipelineRepairError(
                "repair_sidecar_invalid",
                "The position sidecar cannot be patched safely.",
            )
        members.append(
            _JsonMember(
                key=key,
                key_start=key_start,
                value_start=value_start,
                value_end=value_end,
                comma_after=comma_after,
            )
        )
        if comma_after is None:
            return members
    raise PipelineRepairError(
        "repair_sidecar_invalid",
        "The position sidecar cannot be patched safely.",
    )


def _remove_position_entry(sidecar_bytes: bytes, authored_id: str) -> bytes:
    prefix, text, _body = _decode_utf8_artifact(sidecar_bytes, artifact="Position sidecar")
    root_start = _skip_json_ws(text, 0)
    root_members = _json_object_members(text, root_start)
    positions_members = [member for member in root_members if member.key == "positions"]
    if len(positions_members) > 1:
        raise PipelineRepairError(
            "repair_sidecar_ambiguous",
            "The position sidecar contains duplicate positions objects.",
        )
    positions = positions_members[0] if positions_members else None
    if (
        positions is None
        or positions.value_start >= len(text)
        or text[positions.value_start] != "{"
    ):
        return sidecar_bytes
    position_members = _json_object_members(text, positions.value_start)
    target_indices = [
        index for index, member in enumerate(position_members) if member.key == authored_id
    ]
    if len(target_indices) > 1:
        raise PipelineRepairError(
            "repair_sidecar_ambiguous",
            "The position sidecar contains duplicate entries for the selected node.",
        )
    target_index = target_indices[0] if target_indices else None
    if target_index is None:
        return sidecar_bytes
    target = position_members[target_index]
    if target.comma_after is not None:
        remove_start = target.key_start
        remove_end = target.comma_after + 1
    elif target_index > 0:
        previous = position_members[target_index - 1]
        if previous.comma_after is None:  # pragma: no cover - parser invariant
            raise PipelineRepairError(
                "repair_sidecar_invalid",
                "The position sidecar cannot be patched safely.",
            )
        remove_start = previous.comma_after
        remove_end = target.value_end
    else:
        remove_start = target.key_start
        remove_end = target.value_end
    before = text[:remove_start].encode("utf-8")
    removed = text[remove_start:remove_end].encode("utf-8")
    body_bytes = text.encode("utf-8")
    updated = body_bytes[: len(before)] + body_bytes[len(before) + len(removed) :]
    return prefix + updated


def _bounded_diff(edit: RepairArtifactEdit) -> tuple[str, bool]:
    if not edit.expose_diff:
        return "", False
    before = (edit.before or b"").decode("utf-8-sig", errors="replace").splitlines(keepends=True)
    after_bytes = edit.after or b""
    after = after_bytes.decode("utf-8-sig", errors="replace").splitlines(keepends=True)
    rendered = "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{edit.wire_path}",
            tofile=f"b/{edit.wire_path}",
        )
    )
    if len(rendered) <= _MAX_PUBLIC_DIFF:
        return rendered, False
    marker = "\n… diff truncated; apply still uses the complete server plan …\n"
    return rendered[: _MAX_PUBLIC_DIFF - len(marker)] + marker, True


def build_remove_unavailable_node_plan(
    *,
    project_root: Path,
    request: PipelineRepairRemoveRequest,
) -> PipelineRepairPlan:
    """Reload current recovery state and build a deterministic no-write plan."""

    root = project_root.resolve()
    root_path = _resolve_project_file(root, request.source_file, suffix=".py")
    document = load_pipeline_editor_document(root_path, project_root=root)
    if document.source_revision != request.source_revision:
        raise PipelineRepairError(
            "repair_revision_conflict",
            "The pipeline changed after this recovery document loaded; reload before repairing.",
            current_revision=document.source_revision,
        )
    target_path = _resolve_project_file(root, request.target_source_file, suffix=".py")
    target_wire_path = _wire_path(target_path, root)
    target = _find_target(
        document,
        target_source_file=target_wire_path,
        target_recovery_id=request.target_recovery_id,
    )

    source_bytes = target_path.read_bytes()
    prefix, source, source_body = _decode_utf8_artifact(source_bytes, artifact="Pipeline source")
    receiver = "pipeline" if target_path == root_path else "submodel"
    tree, skeletons = _extract_skeletons(source, receiver=receiver)
    consumers = _implicit_consumers(
        skeletons,
        target_authored_id=target.authored_id,
    )
    if consumers:
        names = ", ".join(repr(consumer["function"]) for consumer in consumers)
        raise PipelineRepairError(
            "repair_implicit_consumers",
            f"{target.authored_id!r} is an input parameter of {names}. Remove those nodes "
            f"first, or recover {target.authored_id!r} instead.",
            consumers=consumers,
        )

    span = target.source_span
    assert span is not None  # narrowed by _find_target
    source_ranges = [
        _line_byte_range(source_body, span.start_line, span.end_line),
        *_connection_line_ranges(
            tree,
            source,
            source_body,
            receiver=receiver,
            target_authored_id=target.authored_id,
        ),
    ]
    updated_source = prefix + _apply_byte_ranges(source_body, source_ranges)
    if updated_source == source_bytes:
        raise PipelineRepairError(
            "repair_plan_empty",
            "Removing this node produced no source edit.",
        )

    edits = [
        RepairArtifactEdit(
            path=target_path,
            wire_path=target_wire_path,
            before=source_bytes,
            after=updated_source,
            description=(
                f"Remove unavailable node {target.authored_id!r} and its explicit connections."
            ),
        )
    ]

    sidecar_path = target_path.with_suffix(".haute.json")
    if sidecar_path.is_file():
        sidecar_before = sidecar_path.read_bytes()
        sidecar_after = _remove_position_entry(sidecar_before, target.authored_id)
        if sidecar_after != sidecar_before:
            edits.append(
                RepairArtifactEdit(
                    path=sidecar_path,
                    wire_path=_wire_path(sidecar_path, root),
                    before=sidecar_before,
                    after=sidecar_after,
                    description=f"Remove canvas position for {target.authored_id!r}.",
                )
            )

    if target.config_reference:
        # Resolving the reference validates it even when the config is kept.
        config_path = _resolve_config_reference(
            root_path=root_path,
            project_root=root,
            reference=target.config_reference,
        )
        config_wire_path = _wire_path(config_path, root)
        if request.delete_config:
            if (
                config_path.suffix.casefold() == ".py"
                or config_path.name.casefold().endswith(".haute.json")
                or any(edit.path == config_path for edit in edits)
            ):
                raise PipelineRepairError(
                    "repair_config_not_deletable",
                    "The referenced config overlaps a managed pipeline artifact and cannot "
                    "be deleted as a node config.",
                )
            if not config_path.is_file():
                raise PipelineRepairError(
                    "repair_config_missing",
                    "The separately requested config deletion target no longer exists.",
                )
            shared_references = [
                node.recovery_id
                for node in _iter_recovery_nodes(document)
                if node is not target
                and node.config_reference is not None
                and _resolve_config_reference(
                    root_path=root_path,
                    project_root=root,
                    reference=node.config_reference,
                )
                == config_path
            ]
            if shared_references:
                raise PipelineRepairError(
                    "repair_config_shared",
                    "The referenced config is shared by another node and cannot be deleted.",
                    sharing_recovery_ids=shared_references,
                )
            edits.append(
                RepairArtifactEdit(
                    path=config_path,
                    wire_path=config_wire_path,
                    before=config_path.read_bytes(),
                    after=None,
                    description=f"Delete separately approved config {config_wire_path!r}.",
                    expose_diff=False,
                )
            )

    public_changes: list[PipelineRepairChange] = []
    for edit in edits:
        public_diff, diff_truncated = _bounded_diff(edit)
        public_changes.append(
            PipelineRepairChange(
                path=edit.wire_path,
                operation=edit.operation,
                description=edit.description,
                diff=public_diff,
                diff_truncated=diff_truncated,
            )
        )

    response = PipelineRepairPlanResponse(
        source_file=_wire_path(root_path, root),
        source_revision=request.source_revision,
        target_source_file=target_wire_path,
        target_recovery_id=target.recovery_id,
        target_authored_id=target.authored_id,
        delete_config=request.delete_config,
        changes=public_changes,
    )
    return PipelineRepairPlan(
        response=response,
        edits=tuple(edits),
        root_path=root_path,
        target_path=target_path,
    )


def apply_remove_unavailable_node_plan(
    *,
    project_root: Path,
    request: PipelineRepairRemoveRequest,
) -> PipelineRepairApplyResponse:
    """Compute, commit, and verify one confirmed remove-only repair.

    The caller owns ``save_lock``.  The plan is computed here, against the
    revision the request names; no client-returned patch data is accepted.
    """

    plan = build_remove_unavailable_node_plan(project_root=project_root, request=request)
    return _commit_repair_plan(project_root=project_root, plan=plan)


def _commit_repair_plan(
    *,
    project_root: Path,
    plan: PipelineRepairPlan,
) -> PipelineRepairApplyResponse:
    """Apply exact server edits under the caller's save lock, with rollback."""
    from haute.routes._save_pipeline import (
        _rollback_artifacts,
        _stage_artifact_delete,
        _stage_artifact_write_bytes,
        _TouchedFile,
    )

    # The plan read its artifacts after the document's revision was checked.
    # Re-checking the revision now proves those reads saw the revision the user
    # confirmed, so an external edit in between is refused, never overwritten.
    current_document = load_pipeline_editor_document(plan.root_path, project_root=project_root)
    if current_document.source_revision != plan.response.source_revision:
        raise PipelineRepairError(
            "repair_revision_conflict",
            "The pipeline changed while this repair was planned; reload before repairing.",
            current_revision=current_document.source_revision,
        )
    for edit in plan.edits:
        current = edit.path.read_bytes() if edit.path.is_file() else None
        if current != edit.before or (edit.path.exists() and not edit.path.is_file()):
            raise PipelineRepairError(
                "repair_artifact_conflict",
                "A repair artifact changed after planning; reload and try again.",
                path=edit.wire_path,
            )

    touched: list[_TouchedFile] = []
    try:
        for edit in plan.edits:
            if edit.after is None:
                _stage_artifact_delete(edit.path, touched)
            else:
                _stage_artifact_write_bytes(edit.path, edit.after, touched)

        document = load_pipeline_editor_document(plan.root_path, project_root=project_root)
        if (
            plan.expected_structure is not None
            and _recovery_structure(document) != plan.expected_structure
        ):
            raise PipelineRepairError(
                "repair_post_write_conservation_failed",
                "The repaired node/connection structure differs from the planned repair.",
            )
        remaining_target = [
            node
            for node in _iter_recovery_nodes(document)
            if node.authored_id == plan.response.target_authored_id
            and (node.source_file or "").replace("\\", "/").casefold()
            == plan.response.target_source_file.casefold()
        ]
        removing = plan.response.repair_kind == "remove_unavailable_node"
        if removing and remaining_target:
            raise PipelineRepairError(
                "repair_post_write_conservation_failed",
                "The selected node remained after the repair was staged.",
            )
        if not removing and (
            len(remaining_target) != 1 or remaining_target[0].availability == "unavailable"
        ):
            raise PipelineRepairError(
                "repair_post_write_conservation_failed",
                "The selected node did not recover after the repair was staged.",
            )
        if document.load_status == "source_only":
            raise PipelineRepairError(
                "repair_post_write_conservation_failed",
                "The repaired source could not be conserved as a graph.",
            )
        try:
            parse_pipeline_file(plan.root_path)
        except HauteError:
            if document.load_status == "ready":
                raise PipelineRepairError(
                    "repair_post_write_verification_failed",
                    "Strict parsing disagreed with the recovered ready state.",
                ) from None
        else:
            if document.load_status != "ready":
                raise PipelineRepairError(
                    "repair_post_write_verification_failed",
                    "Recovery remained degraded after strict parsing succeeded.",
                )
    except BaseException as exc:
        rollback_failures = _rollback_artifacts(touched)
        if rollback_failures:
            raise PipelineRepairError(
                "repair_rollback_failed",
                "Repair failed and one or more original artifacts could not be restored.",
                status_code=500,
                paths=[_wire_path(path, project_root) for path in rollback_failures],
            ) from exc
        raise

    return PipelineRepairApplyResponse(
        repair_kind=plan.response.repair_kind,
        applied_artifacts=[edit.wire_path for edit in plan.edits],
        changes=plan.response.changes,
        document=document,
        field_changes=plan.response.field_changes,
        completeness=plan.response.completeness,
        previous_config=plan.response.previous_config,
    )


def build_recover_unavailable_node_plan(
    *,
    project_root: Path,
    request: PipelineRepairRecoverRequest,
) -> PipelineRepairPlan:
    """Build a reset or settings recovery using server-owned source evidence."""
    from haute._pipeline_repair_actions import build_recovery_action_plan

    return build_recovery_action_plan(project_root=project_root, request=request)


def apply_recover_unavailable_node_plan(
    *,
    project_root: Path,
    request: PipelineRepairRecoverRequest,
) -> PipelineRepairApplyResponse:
    plan = build_recover_unavailable_node_plan(project_root=project_root, request=request)
    return _commit_repair_plan(project_root=project_root, plan=plan)


def _recovery_structure(document: PipelineEditorDocument) -> str:
    """Stable authored identities for checking the staged document against its preview."""

    def snapshot(graph: PipelineEditorDocument | RecoveryGraphSnapshot) -> dict[str, Any]:
        return {
            "nodes": [(node.source_file, node.authored_id, node.node_type) for node in graph.nodes],
            "edges": [
                (
                    edge.source_authored_id,
                    edge.target_authored_id,
                    edge.source_port,
                    edge.target_port,
                )
                for edge in graph.edges
            ],
            "unresolved": [
                (
                    edge.source_authored_id,
                    edge.target_authored_id,
                    edge.source_port,
                    edge.target_port,
                )
                for edge in graph.unresolved_connections
            ],
            "submodels": {
                key: snapshot(definition.graph)
                for key, definition in (graph.submodels or {}).items()
            },
        }

    return canonical_json(snapshot(document))
