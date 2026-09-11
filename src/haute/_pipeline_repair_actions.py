"""Bounded current-format submodel updates and single-node resets."""

from __future__ import annotations

import ast
import json
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from haute._config_io import _prepare_config_for_sidecar, config_path_for_node, has_config_folder
from haute._pipeline_recovery import _recovery_artifacts, load_pipeline_editor_document
from haute._pipeline_repair import (
    PipelineRepairError,
    PipelineRepairPlan,
    RepairArtifactEdit,
    _bounded_diff,
    _decode_utf8_artifact,
    _find_target,
    _iter_recovery_nodes,
    _json_object_members,
    _plan_hash,
    _recovery_structure,
    _resolve_config_reference,
    _resolve_project_file,
    _skip_json_ws,
    _wire_path,
)
from haute._python_syntax import (
    SourceNodeReplacement,
    prepend_function_statements,
    replace_source_nodes,
)
from haute._submodel_paths import resolve_submodel_reference
from haute._submodel_recovery import submodel_registration_evidence
from haute._types import GraphNode, NodeData, NodeType
from haute.errors import HauteError
from haute.schemas import (
    PipelineEditorDocument,
    PipelineNodeCompleteness,
    PipelineNodeSaveRequest,
    PipelineRepairChange,
    PipelineRepairFieldChange,
    PipelineRepairPlanResponse,
    PipelineRepairRecoverRequest,
    RecoveryGraphSnapshot,
    RecoveryPipelineNode,
)


def _unsupported(message: str) -> PipelineRepairError:
    return PipelineRepairError("repair_action_unsupported", message)


def _replace_spans(
    source: bytes,
    changes: Sequence[tuple[ast.expr | ast.FunctionDef, str]],
) -> bytes:
    """Convert AST byte coordinates to the shared formatting-preserving CST boundary."""
    prefix, text, body = _decode_utf8_artifact(source, artifact="Repair source")
    lines = body.splitlines(keepends=True)
    replacements = []
    for node, replacement in changes:
        assert node.end_lineno is not None and node.end_col_offset is not None
        replacements.append(
            SourceNodeReplacement(
                start_line=node.lineno,
                start_column=len(lines[node.lineno - 1][: node.col_offset].decode("utf-8")),
                end_line=node.end_lineno,
                end_column=len(lines[node.end_lineno - 1][: node.end_col_offset].decode("utf-8")),
                source=replacement,
                is_function=isinstance(node, ast.FunctionDef),
            )
        )
    return prefix + replace_source_nodes(text, replacements).encode("utf-8")


def _parse(raw: bytes) -> ast.Module:
    _prefix, source, _body = _decode_utf8_artifact(raw, artifact="Repair source")
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise _unsupported(
            "Update/reset requires valid Python syntax; correct the syntax first."
        ) from exc


def _edit(
    path: Path, root: Path, before: bytes | None, after: bytes, description: str
) -> RepairArtifactEdit:
    return RepairArtifactEdit(
        path=path,
        wire_path=_wire_path(path, root),
        before=before,
        after=after,
        description=description,
    )


def _update_submodel(
    root: Path,
    parent: Path,
    target: RecoveryPipelineNode,
    document: PipelineEditorDocument,
) -> tuple[list[RepairArtifactEdit], list[str]]:
    if target.node_type != NodeType.SUBMODEL:
        raise _unsupported("No current-format update is registered for this node type.")
    before = parent.read_bytes()
    tree = _parse(before)
    matches = [
        e
        for statement in tree.body
        if (e := submodel_registration_evidence(statement)) is not None
        and e.name == target.authored_id
    ]
    if len(matches) != 1:
        raise _unsupported("The submodel registration is not an unambiguous literal call.")
    evidence = matches[0]
    keywords = {kw.arg: kw.value for kw in evidence.call.keywords}
    if set(keywords) - {
        "file",
        "name",
        "alias",
        "definition_id",
        "instance_id",
        "label",
        "instance_of",
    }:
        raise _unsupported("The submodel registration contains unsupported arguments.")
    try:
        child, _config_base = resolve_submodel_reference(
            evidence.path, pipeline_dir=parent.parent, project_root=root
        )
    except ValueError as exc:
        raise _unsupported("The referenced submodel path is outside the project.") from exc
    if not child.is_file():
        raise _unsupported("Restore the missing submodel file before updating its registration.")
    child_before = child.read_bytes()
    child_tree = _parse(child_before)
    constructors = [
        statement.value
        for statement in child_tree.body
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == "submodel"
        and isinstance(statement.value, ast.Call)
    ]
    if len(constructors) != 1:
        raise _unsupported("The child must declare exactly one literal Submodel constructor.")
    constructor = constructors[0]
    if not (
        isinstance(constructor.func, ast.Attribute)
        and isinstance(constructor.func.value, ast.Name)
        and constructor.func.value.id == "haute"
        and constructor.func.attr == "Submodel"
    ):
        raise _unsupported("The child must declare a haute.Submodel constructor.")
    fields = {kw.arg: kw.value for kw in constructor.keywords}
    if (
        len(fields) != len(constructor.keywords)
        or not {"definition_id", "input_ports", "output_ports"} <= fields.keys()
    ):
        raise _unsupported("The child has duplicate or missing definition/port declarations.")
    try:
        definition_id = ast.literal_eval(fields["definition_id"])
    except (ValueError, TypeError) as exc:
        raise _unsupported("The child definition id must be literal.") from exc
    if evidence.definition_id is not None and evidence.definition_id != definition_id:
        raise _unsupported("The registration and child disagree on the definition id.")
    child_changes: list[tuple[ast.expr, str]] = []
    for field in ("input_ports", "output_ports"):
        for literal in ast.walk(fields[field]):
            if not isinstance(literal, ast.Dict):
                continue
            keys = [
                key.value
                for key in literal.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            ]
            if len(keys) != len(literal.keys) or len(keys) != len(set(keys)):
                raise _unsupported("Submodel port objects contain duplicate or computed keys.")
        try:
            ports = ast.literal_eval(fields[field])
        except (ValueError, TypeError) as exc:
            raise _unsupported("Submodel ports must be literal lists.") from exc
        if not isinstance(ports, list) or any(not isinstance(port, dict) for port in ports):
            raise _unsupported("Submodel ports must be literal objects.")
        changed = False
        for port in ports:
            if "portId" in port:
                if "name" in port:
                    raise _unsupported("A port declares both old and current identities.")
                port["name"] = port.pop("portId")
                port.pop("label", None)
                changed = True
            elif "label" in port:
                raise _unsupported(
                    "A legacy port label without a port id cannot be updated safely."
                )
        if changed:
            child_changes.append((fields[field], repr(ports)))
    child_after = _replace_spans(child_before, child_changes)
    from haute._parser_submodels import _extract_definition_contract

    try:
        _extract_definition_contract(_parse(child_after))
    except (HauteError, ValueError) as exc:
        raise _unsupported(
            "The updated submodel port contract is invalid; correct its source."
        ) from exc
    registration = f"pipeline.submodel({evidence.path!r}, {evidence.name!r}"
    if "instance_of" in keywords:
        registration += f", instance_of={ast.literal_eval(keywords['instance_of'])!r}"
    registration += ")"
    old_keywords = {"definition_id", "instance_id", "alias", "label"}.intersection(keywords)
    parent_after = (
        _replace_spans(before, [(evidence.call, registration)]) if old_keywords else before
    )
    edits: list[RepairArtifactEdit] = []
    if before != parent_after:
        edits.append(
            _edit(
                parent,
                root,
                before,
                parent_after,
                f"Update {evidence.name!r} to the current submodel registration.",
            )
        )
    if child_before != child_after:
        edits.append(
            _edit(
                child,
                root,
                child_before,
                child_after,
                "Replace legacy port fields with canonical names; retain all child functions.",
            )
        )
    sidecar = parent.with_suffix(".haute.json")
    if evidence.instance_id and evidence.instance_id != evidence.name and sidecar.is_file():
        sidecar_before = sidecar.read_bytes()
        from haute._config_io import reject_duplicate_keys_hook

        try:
            state = json.loads(sidecar_before, object_pairs_hook=reject_duplicate_keys_hook)
            positions = state.get("positions", {})
        except (ValueError, AttributeError) as exc:
            raise _unsupported("The canvas sidecar cannot be updated safely.") from exc
        if evidence.instance_id in positions:
            if evidence.name in positions:
                raise _unsupported(
                    "Both old and current submodel positions exist; resolve the conflict first."
                )
            prefix, text, _body = _decode_utf8_artifact(sidecar_before, artifact="Canvas sidecar")
            root_members = _json_object_members(text, _skip_json_ws(text, 0))
            position_member = next(member for member in root_members if member.key == "positions")
            members = _json_object_members(text, position_member.value_start)
            old_member = next(member for member in members if member.key == evidence.instance_id)
            _old_key, key_end = json.JSONDecoder().raw_decode(text, old_member.key_start)
            after = prefix + (
                text[: old_member.key_start] + json.dumps(evidence.name) + text[key_end:]
            ).encode("utf-8")
            edits.append(
                _edit(
                    sidecar,
                    root,
                    sidecar_before,
                    after,
                    "Retain the submodel's canvas position under its current occurrence name.",
                )
            )
    warnings = [
        "Child nodes, their configuration and downstream custom code are preserved. "
        "Other invalid nodes may still need repair."
    ]
    references = [
        node.authored_id
        for node in _iter_recovery_nodes(document)
        if node.node_type == NodeType.SUBMODEL and node.config_reference == target.config_reference
    ]
    if len(references) > 1:
        warnings.append(
            "This child definition is shared by: "
            + ", ".join(references)
            + ". Its port update affects every occurrence."
        )
    return edits, warnings


def _target_graph(
    document: PipelineEditorDocument, target: RecoveryPipelineNode
) -> PipelineEditorDocument | RecoveryGraphSnapshot:
    def visit(
        graph: PipelineEditorDocument | RecoveryGraphSnapshot,
    ) -> PipelineEditorDocument | RecoveryGraphSnapshot | None:
        if any(node is target for node in graph.nodes):
            return graph
        for definition in (graph.submodels or {}).values():
            found = visit(definition.graph)
            if found is not None:
                return found
        return None

    graph = visit(document)
    if graph is None:
        raise _unsupported("The node's containing graph is unavailable.")
    return graph


def _reset_node(
    root: Path,
    root_path: Path,
    path: Path,
    target: RecoveryPipelineNode,
    document: PipelineEditorDocument,
    *,
    replacement_config: dict[str, Any] | None = None,
    shared_config_confirmed: bool = False,
    recover: bool = False,
    allow_blocked_sources: bool = False,
) -> tuple[list[RepairArtifactEdit], list[str]]:
    from haute._graph_utils import executable_input_name
    from haute.codegen import _node_to_code

    if target.node_type is None or target.node_type in {NodeType.SUBMODEL, "submodelPort"}:
        raise _unsupported(
            "Only supported ordinary nodes can be reset; submodels retain their contents."
        )
    try:
        node_type = NodeType(target.node_type)
    except ValueError as exc:
        raise _unsupported("This node type is not installed and cannot be reset.") from exc
    before = path.read_bytes()
    tree = _parse(before)
    functions = [
        statement
        for statement in tree.body
        if isinstance(statement, ast.FunctionDef) and statement.name == target.authored_id
    ]
    if len(functions) != 1:
        raise _unsupported("Reset requires exactly one ordinary node function.")
    function = functions[0]
    receiver = "pipeline" if path == root_path else "submodel"
    if len(function.decorator_list) != 1:
        raise _unsupported("Reset cannot discard additional custom decorators.")
    decorator = function.decorator_list[0]
    decorator_call = decorator if isinstance(decorator, ast.Call) else None
    attribute = decorator_call.func if decorator_call is not None else decorator
    if not (
        isinstance(attribute, ast.Attribute)
        and isinstance(attribute.value, ast.Name)
        and attribute.value.id == receiver
        and attribute.attr != "instance"
    ):
        raise _unsupported("Node instances cannot be reset independently of their original node.")
    if decorator_call and any(kw.arg in {"of", "instanceOf"} for kw in decorator_call.keywords):
        raise _unsupported("Node instances cannot be reset independently of their original node.")
    graph = _target_graph(document, target)
    if any(
        target.recovery_id in (edge.source_recovery_id, edge.target_recovery_id)
        for edge in graph.unresolved_connections
    ):
        raise _unsupported(
            "Resolve the node's ambiguous or invalid connections before resetting it."
        )
    nodes = {node.recovery_id: node for node in graph.nodes}
    incoming = [edge for edge in graph.edges if edge.target_recovery_id == target.recovery_id]
    if node_type == NodeType.EDGE_JOIN:
        if {edge.target_handle for edge in incoming} != {"base", "join"} or len(incoming) != 2:
            raise _unsupported("An Edge Join reset requires its base and join connections.")
        incoming.sort(key=lambda edge: edge.target_handle != "base")
    source_names: list[str] = []
    for edge in incoming:
        source = nodes[edge.source_recovery_id]
        if source.availability != "ready" and not allow_blocked_sources:
            raise _unsupported("Repair the upstream nodes before resetting this node.")
        if edge.input_name is not None:
            source_names.append(edge.input_name)
            continue
        if allow_blocked_sources:
            # A blocked or unavailable upstream still has authored identity;
            # a scoped save must not depend on it being repaired first.
            fallback = (
                source.source_handle_input_names.get(edge.source_handle or "")
                or source.default_input_name
            )
            if fallback:
                source_names.append(fallback)
                continue
            if source.node_type is None:
                raise _unsupported(
                    "The upstream node's identity is unknown; repair it before saving this node."
                )
        source_names.append(
            executable_input_name(
                node_type=source.node_type,
                label=source.label,
                source_handle=edge.source_handle,
                alias=(source.config or {}).get("alias"),
                output_port_count=len(source.source_handle_input_names)
                if source.node_type == NodeType.SUBMODEL
                else None,
            )
        )
    if len(set(source_names)) != len(source_names):
        raise _unsupported("Incoming node names are ambiguous.")
    defaults = json.loads(
        Path(__file__).with_name("node_defaults.json").read_text(encoding="utf-8")
    )
    config: dict[str, Any] = (
        defaults[node_type.value] if replacement_config is None else replacement_config
    )
    node = GraphNode(
        id=target.authored_id,
        type=node_type.value,
        position=target.display_position,
        data=NodeData(
            label=target.label, description=target.description, nodeType=node_type, config=config
        ),
    )
    try:
        generated = _node_to_code(
            node, source_names=source_names, derive_contract=replacement_config is None
        )
    except (HauteError, ValueError) as exc:
        raise _unsupported(
            f"The current node template cannot use these connections: {exc}"
        ) from exc
    edits: list[RepairArtifactEdit] = []
    if has_config_folder(node_type):
        authored_reference = next(
            (
                kw.value.value
                for kw in (decorator_call.keywords if decorator_call else [])
                if kw.arg == "config"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ),
            None,
        )
        reference = (
            authored_reference
            or target.config_reference
            or config_path_for_node(node_type, target.authored_id).as_posix()
        )
        config_path = _resolve_config_reference(
            root_path=root_path, project_root=root, reference=reference
        )
        if config_path.suffix.lower() != ".json" or config_path.name.endswith(".haute.json"):
            raise _unsupported(
                "Reset config must be an ordinary JSON file, "
                "not a managed source or canvas sidecar."
            )
        shared = [
            other
            for other in _iter_recovery_nodes(document)
            if other is not target
            and other.config_reference
            and _resolve_config_reference(
                root_path=root_path, project_root=root, reference=other.config_reference
            )
            == config_path
        ]
        if shared and not shared_config_confirmed:
            raise _unsupported(
                "This config is shared by another node and cannot be reset independently."
            )
        default_reference = config_path_for_node(node_type, target.authored_id).as_posix()
        generated_tree = ast.parse(generated)
        literals = [
            literal
            for literal in ast.walk(generated_tree)
            if isinstance(literal, ast.Constant) and literal.value == default_reference
        ]
        generated = _replace_spans(
            generated.encode("utf-8"), [(literal, repr(reference)) for literal in literals]
        ).decode("utf-8")
        if "_HAUTE_CONFIG_BASE" in generated and not any(
            isinstance(statement, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(n, ast.Name)
                and isinstance(n.ctx, ast.Store)
                and n.id == "_HAUTE_CONFIG_BASE"
                for n in ast.walk(statement)
            )
            for statement in tree.body
        ):
            # Bind inside the replacement function, avoiding a module-wide edit.
            depth = len(path.parent.relative_to(root_path.parent).parts)
            base_expression = f"_HauteResetPath(__file__).resolve().parents[{depth}]"
            generated = prepend_function_statements(
                generated,
                "from pathlib import Path as _HauteResetPath\n"
                f"_HAUTE_CONFIG_BASE = {base_expression}\n",
            )
        after = (
            json.dumps(_prepare_config_for_sidecar(node_type, config), indent=2, ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        edits.append(
            _edit(
                config_path,
                root,
                config_path.read_bytes() if config_path.is_file() else None,
                after,
                "Recover this node's settings against the current contract."
                if recover
                else "Replace this node's settings with the current palette defaults.",
            )
        )
    if receiver == "submodel":
        receiver_names = [
            name
            for name in ast.walk(ast.parse(generated))
            if isinstance(name, ast.Name) and name.id == "pipeline"
        ]
        generated = _replace_spans(
            generated.encode("utf-8"), [(name, "submodel") for name in receiver_names]
        ).decode("utf-8")
    # LibCST matches the function's definition span and replaces its decorators too.
    updated = _replace_spans(before, [(function, generated.rstrip("\n"))])
    edits.insert(
        0,
        _edit(
            path,
            root,
            before,
            updated,
            f"Regenerate {target.authored_id!r} from its recovered {node_type.value} settings."
            if recover
            else f"Recreate {target.authored_id!r} using the current {node_type.value} template.",
        ),
    )
    if recover:
        return edits, [
            "Recover settings retains valid values and code. "
            "Its identity, position and connections are retained.",
            "Complete any remaining highlighted settings before running.",
        ]
    return edits, [
        "Reset replaces this node's settings and custom code. "
        "Its identity, position and connections are retained.",
        "Configure the node before running it. An empty Polars node requires code "
        "and deliberately raises until configured.",
    ]


def _recover_node(
    root: Path,
    root_path: Path,
    path: Path,
    target: RecoveryPipelineNode,
    document: PipelineEditorDocument,
) -> tuple[
    list[RepairArtifactEdit],
    list[str],
    dict[str, Any],
    list[PipelineRepairFieldChange],
    list[PipelineNodeCompleteness],
]:
    """Rebuild one node's settings with the recovery engine and regenerate its source."""
    from haute._node_config_recovery import reconcile_config
    from haute._recovery_sources import read_raw_node_settings, require_generated_body

    if target.node_type is None or target.node_type in {NodeType.SUBMODEL, "submodelPort"}:
        raise _unsupported(
            "Only supported ordinary nodes can be recovered; "
            "submodels keep Update to current format."
        )
    node_type, raw, raw_changes, function, params, reference = read_raw_node_settings(
        root, document, target
    )
    result = reconcile_config(node_type, raw)
    # The guard only decides whether the body is recognised generated
    # scaffolding; engine issues are completeness for a direct recover, never
    # a plan gate.
    require_generated_body(
        node_type,
        target.authored_id,
        result.config,
        function,
        params=params,
        reference=reference,
        receiver="pipeline" if path == root_path else "submodel",
        config_base_depth=len(path.parent.relative_to(root_path.parent).parts),
    )
    edits, warnings = _reset_node(
        root, root_path, path, target, document, replacement_config=result.config, recover=True
    )
    field_changes = [
        PipelineRepairFieldChange(path=change.path, outcome=change.outcome, reason=change.reason)
        for change in (*raw_changes, *result.changes)
    ]
    return (
        edits,
        warnings,
        raw,
        field_changes,
        _recover_completeness(node_type, result.config, target.recovery_id),
    )


def _recover_completeness(
    node_type: NodeType, config: dict[str, Any], recovery_id: str
) -> list[PipelineNodeCompleteness]:
    from haute._polars_io_registry import data_input_completeness, data_output_completeness

    if node_type is NodeType.DATA_INPUT:
        gaps = data_input_completeness(config)
    elif node_type is NodeType.DATA_OUTPUT:
        gaps = data_output_completeness(config)
    else:
        return []
    return [
        PipelineNodeCompleteness(
            element_id=recovery_id, path=gap.path, code=gap.code, message=gap.message
        )
        for gap in gaps
    ]


def _preview(
    root: Path, root_path: Path, edits: list[RepairArtifactEdit]
) -> PipelineEditorDocument:
    """Verify proposed bytes in an isolated artifact-only copy; never write the project."""
    with TemporaryDirectory(prefix="haute-repair-preview-") as directory:
        preview_root = Path(directory)
        (preview_root / "haute.toml").write_text(
            '[project]\nname = "repair-preview"\n', encoding="utf-8"
        )
        paths = {path for _role, path in _recovery_artifacts(root_path, root)}
        for path in paths:
            if path.is_file():
                copy = preview_root / path.relative_to(root)
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(path.read_bytes())
        for edit in edits:
            copy = preview_root / edit.path.relative_to(root)
            copy.parent.mkdir(parents=True, exist_ok=True)
            if edit.after is None:
                copy.unlink(missing_ok=True)
            else:
                copy.write_bytes(edit.after)
        return load_pipeline_editor_document(
            preview_root / root_path.relative_to(root), project_root=preview_root
        )


def build_recovery_action_plan(
    *, project_root: Path, request: PipelineRepairRecoverRequest
) -> PipelineRepairPlan:
    root = project_root.resolve()
    root_path = _resolve_project_file(root, request.source_file, suffix=".py")
    document = load_pipeline_editor_document(root_path, project_root=root)
    if document.source_revision != request.source_revision:
        raise PipelineRepairError(
            "repair_revision_conflict",
            "The pipeline changed after this recovery document loaded; reload before repairing.",
        )
    path = _resolve_project_file(root, request.target_source_file, suffix=".py")
    target = _find_target(
        document,
        target_source_file=_wire_path(path, root),
        target_recovery_id=request.target_recovery_id,
    )
    field_changes: list[PipelineRepairFieldChange] = []
    completeness: list[PipelineNodeCompleteness] = []
    previous_config: dict[str, Any] | None = None
    if request.action == "update":
        edits, warnings = _update_submodel(root, path, target, document)
    elif request.action == "recover":
        edits, warnings, previous_config, field_changes, completeness = _recover_node(
            root, root_path, path, target, document
        )
    else:
        edits, warnings = _reset_node(root, root_path, path, target, document)
    kind: Literal["update_node", "reset_node", "recover_node"] = (
        "update_node"
        if request.action == "update"
        else "recover_node"
        if request.action == "recover"
        else "reset_node"
    )
    return _finalise_action_plan(
        root=root,
        root_path=root_path,
        path=path,
        target=target,
        document=document,
        source_revision=request.source_revision,
        kind=kind,
        edits=edits,
        warnings=warnings,
        field_changes=field_changes,
        completeness=completeness,
        previous_config=previous_config,
    )


def _finalise_action_plan(
    *,
    root: Path,
    root_path: Path,
    path: Path,
    target: RecoveryPipelineNode,
    document: PipelineEditorDocument,
    source_revision: str,
    kind: Literal["update_node", "reset_node", "recover_node"],
    edits: list[RepairArtifactEdit],
    warnings: list[str],
    field_changes: list[PipelineRepairFieldChange] | None = None,
    completeness: list[PipelineNodeCompleteness] | None = None,
    previous_config: dict[str, Any] | None = None,
) -> PipelineRepairPlan:
    """Verify proposed edits in isolation and bind them to one confirmable plan."""
    edits = [edit for edit in edits if edit.before != edit.after]
    if not edits:
        raise _unsupported("No supported current-format change was found for this node.")
    preview = _preview(root, root_path, edits)
    recovered = [
        node
        for node in _iter_recovery_nodes(preview)
        if node.authored_id == target.authored_id and node.source_file == target.source_file
    ]
    if (
        preview.load_status == "source_only"
        or len(recovered) != 1
        or recovered[0].availability == "unavailable"
    ):
        messages = [
            diagnostic.message
            for diagnostic in preview.diagnostics
            if diagnostic.element_id == target.recovery_id
        ]
        raise _unsupported(
            "This action still requires valid configuration before the node can be recovered. "
            + " ".join(messages[:3])
        )
    # Recovery rewrites may expose child nodes; they may not lose existing identities.
    before_ids = {(node.source_file, node.authored_id) for node in _iter_recovery_nodes(document)}
    after_ids = {(node.source_file, node.authored_id) for node in _iter_recovery_nodes(preview)}
    if not before_ids <= after_ids:
        raise _unsupported("The proposed repair would lose an existing node identity.")
    response = PipelineRepairPlanResponse(
        repair_kind=kind,
        source_file=document.source_file,
        source_revision=source_revision,
        target_source_file=_wire_path(path, root),
        target_recovery_id=target.recovery_id,
        target_authored_id=target.authored_id,
        delete_config=False,
        plan_hash=_plan_hash(
            source_revision=source_revision,
            source_file=document.source_file,
            target_source_file=_wire_path(path, root),
            target_recovery_id=target.recovery_id,
            delete_config=False,
            edits=edits,
            repair_kind=kind,
        ),
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
        warnings=warnings,
        predicted_load_status=preview.load_status,
        field_changes=field_changes or [],
        completeness=completeness or [],
        previous_config=previous_config,
    )
    return PipelineRepairPlan(
        response=response,
        edits=tuple(edits),
        root_path=root_path,
        target_path=path,
        expected_structure=_recovery_structure(preview),
    )


def apply_scoped_node_save(
    *, project_root: Path, request: PipelineNodeSaveRequest
) -> PipelineEditorDocument:
    """Save one `scoped_editable` node's settings and code in isolation.

    The whole-document mutation/save fences stay untouched: exactly this
    node's function span and exclusively owned config are rewritten, every
    other artifact byte is conserved, and the candidate only has to remain
    loadable — completeness gaps are allowed.
    """
    from haute._config_validation import validate_node_config
    from haute._pipeline_repair import _commit_repair_plan

    root = project_root.resolve()
    root_path = _resolve_project_file(root, request.source_file, suffix=".py")
    document = load_pipeline_editor_document(root_path, project_root=root)
    if document.source_revision != request.source_revision:
        raise PipelineRepairError(
            "repair_revision_conflict",
            "The pipeline changed after this document loaded; reload before saving.",
        )
    path = _resolve_project_file(root, request.target_source_file, suffix=".py")
    target = _find_target(
        document,
        target_source_file=_wire_path(path, root),
        target_recovery_id=request.target_recovery_id,
        require_unavailable=False,
    )
    if not target.scoped_editable or target.node_type is None:
        raise _unsupported("This node cannot be saved in isolation; recover or repair it first.")
    try:
        config = validate_node_config(
            target.node_type, deepcopy(request.config), require_complete=False
        )
    except ValueError as exc:
        raise _unsupported(f"The proposed settings are not loadable: {exc}") from exc
    edits, _warnings = _reset_node(
        root,
        root_path,
        path,
        target,
        document,
        replacement_config=config,
        recover=True,
        allow_blocked_sources=True,
    )
    if all(edit.before == edit.after for edit in edits):
        return document
    plan = _finalise_action_plan(
        root=root,
        root_path=root_path,
        path=path,
        target=target,
        document=document,
        source_revision=request.source_revision,
        kind="recover_node",
        edits=edits,
        warnings=[],
    )
    return _commit_repair_plan(
        project_root=root, plan=plan, plan_hash=plan.response.plan_hash
    ).document
