"""Bounded single-node resets and settings recovery."""

from __future__ import annotations

import ast
import json
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Literal

from haute._config_io import _prepare_config_for_sidecar, config_path_for_node, node_emits_sidecar
from haute._config_validation import reject_unrecognized_config_keys
from haute._pipeline_recovery import _recovery_artifacts, load_pipeline_editor_document
from haute._pipeline_repair import (
    PipelineRepairError,
    PipelineRepairPlan,
    RepairArtifactEdit,
    _bounded_diff,
    _decode_utf8_artifact,
    _find_target,
    _iter_recovery_nodes,
    _recovery_structure,
    _resolve_config_reference,
    _resolve_project_file,
    _wire_path,
)
from haute._python_syntax import (
    SourceNodeReplacement,
    insert_import_after,
    replace_source_nodes,
)
from haute._source_layout import quote_string
from haute._types import GraphNode, NodeData, NodeType
from haute.errors import ConfigError, HauteError
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

if TYPE_CHECKING:
    from haute._node_config_recovery import RecoveryIssue


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


def _with_polars_import(source: bytes) -> bytes:
    """Add ``import polars as pl`` below ``import haute`` when *source* uses ``pl`` unimported.

    A regenerated function can gain annotations (a hook's ``pl.LazyFrame``) that
    a module of declarations never needed to import.
    """
    prefix, text, _body = _decode_utf8_artifact(source, artifact="Repair source")
    tree = ast.parse(text)
    imported = {
        alias.asname or alias.name.split(".")[0]
        for statement in tree.body
        if isinstance(statement, (ast.Import, ast.ImportFrom))
        for alias in statement.names
    }
    uses_pl = any(isinstance(node, ast.Name) and node.id == "pl" for node in ast.walk(tree))
    if not uses_pl or "pl" in imported:
        return source
    added = insert_import_after(text, "import polars as pl", after_module="haute")
    return prefix + added.encode("utf-8")


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
) -> list[RepairArtifactEdit]:
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
        if edge.input_name is not None:
            source_names.append(edge.input_name)
            continue
        # A blocked or unavailable upstream still has authored identity: a damaged
        # chain resets, recovers and saves in any order, never upstream first.
        fallback = (
            source.source_handle_input_names.get(edge.source_handle or "")
            or source.default_input_name
        )
        if fallback:
            source_names.append(fallback)
            continue
        if source.node_type is None:
            raise _unsupported(
                "The upstream node's identity is unknown; repair it before changing this node."
            )
        source_names.append(
            executable_input_name(
                node_type=source.node_type,
                label=source.label,
                source_handle=edge.source_handle,
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
        # A node rebuilt from given settings (recovered, or a scoped save) has
        # its annotation regenerated from them as the reload's parse check
        # derives it; carrying the old one forward keeps a stale annotation
        # that check rejects.
        generated = _node_to_code(
            node,
            source_names=source_names,
            contract_source="builder" if replacement_config is None else "offline",
        )
    except (HauteError, ValueError) as exc:
        raise _unsupported(
            f"This node cannot be generated from these settings and connections: {exc}"
        ) from exc
    edits: list[RepairArtifactEdit] = []
    # A stepped transform owns an optional sidecar; a code-only one owns none.
    if node_emits_sidecar(node):
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
            generated.encode("utf-8"),
            [(literal, quote_string(reference)) for literal in literals],
        ).decode("utf-8")
        # The annotation lives on the decorator; a sidecar copy is what goes stale.
        sidecar_config = {key: value for key, value in config.items() if key != "contract"}
        after = (
            json.dumps(
                _prepare_config_for_sidecar(node_type, sidecar_config, node_label=target.label),
                indent=2,
                ensure_ascii=False,
            )
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
    updated = _with_polars_import(_replace_spans(before, [(function, generated.rstrip("\n"))]))
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
    return edits


def _recover_node(
    root: Path,
    root_path: Path,
    path: Path,
    target: RecoveryPipelineNode,
    document: PipelineEditorDocument,
) -> tuple[
    list[RepairArtifactEdit],
    dict[str, Any],
    list[PipelineRepairFieldChange],
    list[PipelineNodeCompleteness],
]:
    """Rebuild one node's settings with the recovery engine and regenerate its source."""
    from haute._node_config_recovery import reconcile_config
    from haute._recovery_sources import read_raw_node_settings

    if target.node_type is None or target.node_type in {NodeType.SUBMODEL, "submodelPort"}:
        raise _unsupported("Only supported ordinary nodes can be recovered.")
    node_type, raw, raw_changes = read_raw_node_settings(root, document, target)
    # Engine issues are completeness for a direct recover, never a plan gate.
    result = reconcile_config(node_type, raw)
    edits = _reset_node(
        root,
        root_path,
        path,
        target,
        document,
        replacement_config=result.config,
        recover=True,
    )
    # A dropped body's report replaces the engine's view of the empty code slot it left.
    body_dropped = any(change.path == "/code" for change in raw_changes)
    engine_changes = [
        change for change in result.changes if not (body_dropped and change.path == "/code")
    ]
    field_changes = [
        PipelineRepairFieldChange(path=change.path, outcome=change.outcome, reason=change.reason)
        for change in (*raw_changes, *engine_changes)
    ]
    return (
        edits,
        raw,
        field_changes,
        _recover_completeness(node_type, result.config, target.recovery_id, result.issues),
    )


def _recover_completeness(
    node_type: NodeType,
    config: dict[str, Any],
    recovery_id: str,
    issues: Sequence[RecoveryIssue],
) -> list[PipelineNodeCompleteness]:
    """What the recover could not fix: provider gaps plus unresolved engine errors."""
    from haute._polars_io_registry import data_input_completeness, data_output_completeness

    if node_type is NodeType.DATA_INPUT:
        gaps = [(gap.path, gap.code, gap.message) for gap in data_input_completeness(config)]
    elif node_type is NodeType.DATA_OUTPUT:
        gaps = [(gap.path, gap.code, gap.message) for gap in data_output_completeness(config)]
    else:
        gaps = []
    # An error the engine could not resolve must never read as fixed: the
    # recover applies and the node shows it as unfinished.
    for issue in issues:
        if issue.severity != "error":
            continue
        entry = (issue.path or "/", issue.code, issue.message[:1024])
        if entry not in gaps:
            gaps.append(entry)
    return [
        PipelineNodeCompleteness(element_id=recovery_id, path=path, code=code, message=message)
        for path, code, message in gaps
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
    if request.action == "recover":
        edits, previous_config, field_changes, completeness = _recover_node(
            root, root_path, path, target, document
        )
    else:
        edits = _reset_node(root, root_path, path, target, document)
    kind: Literal["reset_node", "recover_node"] = (
        "recover_node" if request.action == "recover" else "reset_node"
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
    kind: Literal["reset_node", "recover_node"],
    edits: list[RepairArtifactEdit],
    field_changes: list[PipelineRepairFieldChange] | None = None,
    completeness: list[PipelineNodeCompleteness] | None = None,
    previous_config: dict[str, Any] | None = None,
) -> PipelineRepairPlan:
    """Verify proposed edits in isolation and bind them to one plan."""
    edits = [edit for edit in edits if edit.before != edit.after]
    if not edits:
        raise _unsupported("No supported change was found for this node.")
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
    # Code generation keeps only declared keys and a sidecar write refuses the
    # rest, so an undeclared key is refused first, for every node type, before
    # anything is written. The per-type validators then check the declared keys.
    try:
        reject_unrecognized_config_keys(
            NodeType(target.node_type), request.config, node_label=target.label
        )
    except ConfigError as exc:
        raise PipelineRepairError(
            "node_config_undeclared_keys",
            f"{exc.message} Nothing was saved.",
            status_code=400,
            unrecognized_config_keys=exc.context["unrecognized_config_keys"],
        ) from exc
    try:
        config = validate_node_config(
            target.node_type, deepcopy(request.config), require_complete=False
        )
    except ValueError as exc:
        raise _unsupported(f"The proposed settings are not loadable: {exc}") from exc
    edits = _reset_node(
        root,
        root_path,
        path,
        target,
        document,
        replacement_config=config,
        recover=True,
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
    )
    return _commit_repair_plan(project_root=root, plan=plan).document
