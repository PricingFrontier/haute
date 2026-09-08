"""Source evidence and surgical generation for recovery drafts (no execution)."""

from __future__ import annotations

import ast
import builtins
import json
import symtable
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from haute._ast_helpers import _extract_function_bodies, _get_decorator_kwargs
from haute._code_extraction import _extract_explore_user_code
from haute._config_builder import _attach_code_from_body
from haute._config_io import (
    _normalise_loaded_config,
    config_path_for_node,
    has_config_folder,
    reject_duplicate_keys_hook,
)
from haute._node_config_recovery import (
    node_config_schema,
    reconcile_config,
    validate_recovery_config,
)
from haute._pipeline_repair import PipelineRepairError, _iter_recovery_nodes
from haute._pipeline_repair_actions import (
    _parse,
    _replace_spans,
    _reset_node,
    _target_graph,
    _update_submodel,
)
from haute._recovery_schemas import RecoveryDraftNode, RecoveryFieldChange, RecoveryIssue
from haute._recovery_storage import conflict, digest, read_artifact, safe_path
from haute._submodel_recovery import SubmodelRegistrationEvidence, submodel_registration_evidence
from haute._types import GraphNode, NodeData, NodeType, SubmodelInputPort, SubmodelOutputPort
from haute.errors import HauteError
from haute.schemas import PipelineEditorDocument, RecoveryPipelineNode


def authored_config_path(
    root: Path,
    document: PipelineEditorDocument,
    node: RecoveryPipelineNode,
) -> Path | None:
    """Read the authored reference, which may differ from the palette's derived path."""
    if node.node_type == NodeType.SUBMODEL:
        tree = _parse(safe_path(root, node.source_file or document.source_file).read_bytes())
        registrations = [
            e
            for statement in tree.body
            if (e := submodel_registration_evidence(statement)) and e.name == node.authored_id
        ]
        if len(registrations) != 1:
            return None
        return safe_path(
            root, (Path(document.source_file).parent / registrations[0].path).as_posix()
        )
    try:
        tree = _parse(safe_path(root, node.source_file or document.source_file).read_bytes())
    except PipelineRepairError:
        # A malformed sibling is still represented by the recovery document.
        # Its complete source remains in the impact snapshot; do not infer a config path.
        return None
    functions = [
        s for s in tree.body if isinstance(s, ast.FunctionDef) and s.name == node.authored_id
    ]
    for function in functions:
        for decorator in function.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for kw in decorator.keywords:
                if (
                    kw.arg == "config"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    return safe_path(
                        root, (Path(document.source_file).parent / kw.value.value).as_posix()
                    )
    return None


def find_target(
    document: PipelineEditorDocument, source: str, identity: str
) -> RecoveryPipelineNode:
    matches = [
        node
        for node in _iter_recovery_nodes(document)
        if node.source_file == source and node.recovery_id == identity
    ]
    if len(matches) != 1:
        raise conflict("Recovery target does not resolve to one authored node.")
    target = matches[0]
    if (
        sum(
            node.authored_id == target.authored_id and node.source_file == source
            for node in _iter_recovery_nodes(document)
        )
        != 1
    ):
        raise conflict("Duplicate authored identities require a source edit.")
    return target


def _submodel_literals(
    root: Path,
    source: str,
    authored_id: str,
    *,
    file: str | None = None,
) -> tuple[SubmodelRegistrationEvidence, Path, dict[str, ast.expr], dict[str, Any]]:
    parent = safe_path(root, source)
    tree = _parse(parent.read_bytes())
    evidence = [
        e
        for statement in tree.body
        if (e := submodel_registration_evidence(statement)) and e.name == authored_id
    ]
    if len(evidence) != 1:
        raise conflict("Submodel recovery requires one unambiguous literal registration.")
    registration = evidence[0]
    reference = file or registration.path
    # This intentionally validates lexical paths before resolving aliases.
    relative = (parent.parent.relative_to(root) / reference).as_posix()
    child = safe_path(root, relative)
    if child.suffix != ".py" or not child.is_file():
        raise conflict("The submodel file is missing; select an existing definition file.")
    constructors = [
        statement.value
        for statement in _parse(child.read_bytes()).body
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == "submodel"
        and isinstance(statement.value, ast.Call)
    ]
    if len(constructors) != 1:
        raise conflict("The child needs one literal haute.Submodel constructor.")
    constructor = constructors[0]
    if ast.unparse(constructor.func) != "haute.Submodel":
        raise conflict("The child constructor is not haute.Submodel.")
    fields = {kw.arg: kw.value for kw in constructor.keywords if kw.arg is not None}
    if len(fields) != len(constructor.keywords):
        raise conflict("The submodel constructor contains computed or duplicate fields.")
    values: dict[str, Any] = {"file": reference, "name": registration.name}
    try:
        for field in ("definition_id", "input_ports", "output_ports"):
            values[field] = ast.literal_eval(fields[field])
        for kw in registration.call.keywords:
            if kw.arg == "instance_of":
                values["instance_of"] = ast.literal_eval(kw.value)
    except (KeyError, ValueError, TypeError) as exc:
        raise conflict("Definition identity and ports must be literal values.") from exc
    if registration.definition_id and registration.definition_id != values["definition_id"]:
        raise conflict("Registration and child definition identities disagree.")
    return registration, child, fields, values


def _normalise_ports(config: dict[str, Any]) -> list[RecoveryFieldChange]:
    changes: list[RecoveryFieldChange] = []
    for field in ("input_ports", "output_ports"):
        ports = config.get(field)
        if not isinstance(ports, list):
            continue
        for index, port in enumerate(ports):
            if isinstance(port, dict) and "portId" in port and "name" not in port:
                port["name"] = port.pop("portId")
                port.pop("label", None)
                changes.append(
                    RecoveryFieldChange(
                        path=f"/{field}/{index}/name",
                        outcome="retained",
                        reason="Format update: portId becomes name; public identity is preserved.",
                    )
                )
    return changes


def validate_structural(config: dict[str, Any]) -> list[RecoveryIssue]:
    issues: list[RecoveryIssue] = []
    allowed = {"file", "name", "definition_id", "instance_of", "input_ports", "output_ports"}
    for field in config.keys() - allowed:
        issues.append(
            RecoveryIssue(
                path=f"/{field}",
                code="unknown_field",
                message="Unknown structural field; correct it explicitly.",
            )
        )
    for field in ("file", "name", "definition_id"):
        if not isinstance(config.get(field), str) or not config[field]:
            issues.append(
                RecoveryIssue(path=f"/{field}", code="required", message=f"{field} is required.")
            )
    names: set[str] = set()
    for field, model in (("input_ports", SubmodelInputPort), ("output_ports", SubmodelOutputPort)):
        ports = config.get(field)
        if not isinstance(ports, list):
            issues.append(
                RecoveryIssue(
                    path=f"/{field}", code="invalid_ports", message="Ports must be a list."
                )
            )
            continue
        for index, port in enumerate(ports):
            try:
                parsed = model.model_validate(port)
                if parsed.name in names:
                    raise ValueError("Public port names must be unique across both directions.")
                names.add(parsed.name)
            except (ValidationError, ValueError) as exc:
                issues.append(
                    RecoveryIssue(path=f"/{field}/{index}", code="invalid_port", message=str(exc))
                )
    return issues


def validate_node(node: RecoveryDraftNode) -> list[RecoveryIssue]:
    if not node.editable:
        return node.issues
    if node.node_type == NodeType.SUBMODEL:
        return validate_structural(node.config)
    if node.node_type not in {kind.value for kind in NodeType}:
        return [RecoveryIssue(code="unsupported_type", message="This node type is not installed.")]
    return validate_recovery_config(
        NodeType(node.node_type), node.config, input_names=node.input_names
    )


def _require_generated_body(
    node: RecoveryDraftNode,
    function: ast.FunctionDef,
    *,
    params: list[str],
    reference: str | None,
    receiver: str,
    config_base_depth: int,
) -> None:
    """Only regenerate a non-code node when its body is a recognised template."""
    from haute.codegen import _node_to_code

    assert node.node_type is not None
    node_type = NodeType(node.node_type)
    if "code" in node_config_schema(node_type)["properties"]:
        return
    problem = (
        "This node's body is not a recognised generated scaffold. Preserve it through "
        "a manual source edit, or explicitly choose Reset all settings and code."
    )
    if (
        function.args.defaults
        or function.args.kwonlyargs
        or function.args.vararg
        or function.args.kwarg
        or any(issue.code == "invalid_value" and issue.severity == "error" for issue in node.issues)
    ):
        raise conflict(problem)
    config = deepcopy(node.config)
    config.pop("contract", None)  # Matching a body never derives a runtime column contract.
    try:
        generated = _node_to_code(
            GraphNode(
                id=node.authored_id,
                data=NodeData(label=node.authored_id, nodeType=node_type, config=config),
            ),
            source_names=params,
            derive_contract=False,
        )
    except (HauteError, ValueError) as exc:
        raise conflict(problem) from exc
    expected = ast.parse(generated).body[0]
    assert isinstance(expected, ast.FunctionDef)
    default_reference = (
        config_path_for_node(node_type, node.authored_id).as_posix()
        if has_config_folder(node_type)
        else None
    )
    # Generated syntax contains no user code here; adapt only its known bindings.
    for part in ast.walk(expected):
        if isinstance(part, ast.Name) and part.id == "pipeline":
            part.id = receiver
        elif isinstance(part, ast.Constant) and reference and part.value == default_reference:
            part.value = reference

    def statements(body: list[ast.stmt]) -> list[ast.stmt]:
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            return body[1:]
        return body

    def syntax(body: list[ast.stmt]) -> str:
        return ast.dump(ast.Module(body=body, type_ignores=[]))

    actual_body = statements(function.body)
    # Recovery can have installed this exact local config-base binding previously.
    local_base = ast.parse(
        "from pathlib import Path as _HauteResetPath\n"
        f"_HAUTE_CONFIG_BASE = _HauteResetPath(__file__).resolve().parents[{config_base_depth}]\n"
    ).body
    if syntax(actual_body[:2]) == syntax(local_base):
        actual_body = actual_body[2:]
    if syntax(actual_body) != syntax(statements(expected.body)):
        raise conflict(problem)


def make_draft_node(
    root: Path,
    document: PipelineEditorDocument,
    target: RecoveryPipelineNode,
    *,
    reset: bool,
) -> RecoveryDraftNode:
    node = RecoveryDraftNode(
        key=digest([target.source_file, target.authored_id])[:24],
        source_file=target.source_file or document.source_file,
        recovery_id=target.recovery_id,
        authored_id=target.authored_id,
        label=target.label,
        node_type=target.node_type,
        config={},
        changes=[],
        issues=[],
    )
    graph = _target_graph(document, target)
    incoming = [edge for edge in graph.edges if edge.target_recovery_id == target.recovery_id]
    if target.node_type == NodeType.EDGE_JOIN:
        incoming.sort(key=lambda edge: edge.target_handle != "base")
    source_nodes = {item.recovery_id: item for item in graph.nodes}
    for edge in incoming:
        upstream = source_nodes[edge.source_recovery_id]
        name = (
            edge.input_name
            or upstream.source_handle_input_names.get(edge.source_handle or "")
            or upstream.default_input_name
        )
        if name:
            node.input_names.append(name)
    try:
        if target.node_type == NodeType.SUBMODEL_PORT:
            raise conflict(
                "Public ports are owned by their submodel definition; recover that submodel."
            )
        if target.node_type == NodeType.SUBMODEL:
            try:
                _registration, _child, _fields, config = _submodel_literals(
                    root,
                    node.source_file,
                    node.authored_id,
                )
            except PipelineRepairError as exc:
                # A trustworthy registration still permits an explicit file relink.
                parent_tree = _parse(safe_path(root, node.source_file).read_bytes())
                evidence = [
                    e
                    for statement in parent_tree.body
                    if (e := submodel_registration_evidence(statement))
                    and e.name == node.authored_id
                ]
                if len(evidence) != 1:
                    raise
                config = {
                    "file": evidence[0].path,
                    "name": evidence[0].name,
                    "definition_id": evidence[0].definition_id or "",
                    "input_ports": [],
                    "output_ports": [],
                }
                node.issues.append(RecoveryIssue(code="missing_definition", message=str(exc)))
            node.config = config
            node.changes = _normalise_ports(config)
            node.changes.append(
                RecoveryFieldChange(
                    path="",
                    outcome="needs_review",
                    reason="Update registration and ports; preserve child source and settings.",
                )
            )
            if reset:
                node.changes.append(
                    RecoveryFieldChange(
                        path="",
                        outcome="retained",
                        reason="Submodel reset preserves children, identities and public ports.",
                    )
                )
            node.issues.extend(validate_structural(node.config))
            return node
        if target.node_type not in {kind.value for kind in NodeType}:
            raise conflict(
                "This node type is not installed; install its definition or edit the source."
            )
        node_type = NodeType(target.node_type)
        source = safe_path(root, node.source_file).read_bytes().decode("utf-8-sig")
        tree = ast.parse(source)
        functions = [
            s for s in tree.body if isinstance(s, ast.FunctionDef) and s.name == node.authored_id
        ]
        if len(functions) != 1 or len(functions[0].decorator_list) != 1:
            raise conflict("Recovery requires one function with one recognised node decorator.")
        function = functions[0]
        decorator = function.decorator_list[0]
        kwargs = _get_decorator_kwargs(decorator)
        if "of" in kwargs or (target.config or {}).get("instanceOf"):
            owner = kwargs.get("of", (target.config or {}).get("instanceOf"))
            node.affected_owners = [str(owner)]
            raise conflict(f"This is a shared node instance. Recover its owner {owner!r}.")
        if isinstance(decorator, ast.Call) and len({kw.arg for kw in decorator.keywords}) != len(
            decorator.keywords
        ):
            raise conflict("Duplicate decorator arguments require an explicit source correction.")
        raw: dict[str, Any] = {key: value for key, value in kwargs.items() if key != "config"}
        reference = kwargs.get("config")
        if reference is not None and not isinstance(reference, str):
            raise conflict("A node config reference must be a literal relative path.")
        if reference:
            relative = (Path(document.source_file).parent / reference).as_posix()
            content = read_artifact(root, relative)
            if content is None:
                node.changes.append(
                    RecoveryFieldChange(
                        path="",
                        outcome="needs_review",
                        reason="Missing config file: the draft starts from current defaults.",
                    )
                )
            else:
                try:
                    value = json.loads(
                        content,
                        object_pairs_hook=reject_duplicate_keys_hook,
                        parse_constant=lambda _: (_ for _ in ()).throw(
                            ValueError("Non-finite JSON")
                        ),
                    )
                    if not isinstance(value, dict):
                        raise ValueError("The configuration is not an object.")
                    raw = {**value, **raw}
                    if node_type == NodeType.BANDING:
                        raw = _normalise_loaded_config(raw, node_type)
                except (ValueError, UnicodeError) as exc:
                    node.changes.append(
                        RecoveryFieldChange(
                            path="",
                            outcome="needs_review",
                            reason=(
                                "Unreadable configuration is archived unchanged. "
                                f"Re-enter its settings: {exc}"
                            ),
                        )
                    )
        params = [arg.arg for arg in (*function.args.posonlyargs, *function.args.args)]
        body = _extract_function_bodies(source, tree=tree)[function.name]
        raw = _attach_code_from_body(raw, node_type, body, params)
        if node_type == NodeType.EXPLORE:
            raw["code"] = _extract_explore_user_code(body, params)
        if "source_type" in raw and "sourceType" not in raw:
            raw["sourceType"] = raw.pop("source_type")
            node.changes.append(
                RecoveryFieldChange(
                    path="/sourceType",
                    outcome="retained",
                    reason="Known decorator spelling: source_type maps to sourceType.",
                )
            )
        result = reconcile_config(node_type, raw, reset=reset)
        node.config = result.config
        node.changes.extend(result.changes)
        # Re-validate with actual connected names; initial reconciliation has no graph context.
        node.issues = validate_node(node)
        if not reset:
            _require_generated_body(
                node,
                function,
                params=params,
                reference=reference,
                receiver="pipeline" if node.source_file == document.source_file else "submodel",
                config_base_depth=len(
                    Path(node.source_file)
                    .parent.relative_to(Path(document.source_file).parent)
                    .parts
                ),
            )
        node.changes.append(
            RecoveryFieldChange(
                path="/code",
                outcome="needs_review",
                reason=(
                    "Reset replaces all custom code and settings; original bytes remain restorable."
                    if reset
                    else "Regenerate the current wrapper and preserve extracted user code. "
                    "Review the source diff before applying; original bytes remain restorable."
                ),
            )
        )
    except (PipelineRepairError, HauteError, SyntaxError, UnicodeError) as exc:
        node.editable = False
        node.issues = [RecoveryIssue(code="manual_source_action", message=str(exc))]
    return node


def apply_node_to_copy(
    root: Path, document: PipelineEditorDocument, node: RecoveryDraftNode
) -> None:
    """Write to an isolated artifact copy, never to the user's project."""
    target = find_target(document, node.source_file, node.recovery_id)
    path = safe_path(root, node.source_file)
    root_path = safe_path(root, document.source_file)
    if node.node_type == NodeType.SUBMODEL:
        registration, child, fields, original = _submodel_literals(
            root,
            node.source_file,
            node.authored_id,
            file=str(node.config["file"]),
        )
        for field in ("name", "definition_id", "instance_of"):
            if node.config.get(field) != original.get(field):
                raise conflict(f"Recovery cannot rename or change the submodel's {field} identity.")
        if not str(original["definition_id"]):
            raise conflict("A submodel definition requires a trustworthy identity.")
        child_tree = _parse(child.read_bytes())
        child_names = {
            statement.name
            for statement in child_tree.body
            if isinstance(statement, ast.FunctionDef) and statement.decorator_list
        }
        for field in ("input_ports", "output_ports"):
            ports = node.config[field]
            if not isinstance(ports, list):
                raise conflict("Public ports must be lists.")
            for raw_port in ports:
                port = (
                    SubmodelInputPort.model_validate(raw_port)
                    if field == "input_ports"
                    else SubmodelOutputPort.model_validate(raw_port)
                )
                endpoints = port.targets if isinstance(port, SubmodelInputPort) else [port.source]
                for endpoint in endpoints:
                    if endpoint.node_id not in child_names:
                        raise conflict(
                            f"Public port {port.name!r} references missing child "
                            f"{endpoint.node_id!r}; select an existing child node."
                        )
        # Replace just the port literals; unrelated constructor arguments and all children survive.
        child.write_bytes(
            _replace_spans(
                child.read_bytes(),
                [
                    (fields[field], repr(node.config[field]))
                    for field in ("input_ports", "output_ports")
                ],
            )
        )
        if registration.path != node.config["file"]:
            arguments = registration.call.args
            literal = (
                arguments[0]
                if arguments
                else next(kw.value for kw in registration.call.keywords if kw.arg == "file")
            )
            path.write_bytes(
                _replace_spans(path.read_bytes(), [(literal, repr(node.config["file"]))])
            )
        edits, _warnings = _update_submodel(root, path, target, document)
    else:
        edits, _warnings = _reset_node(
            root,
            root_path,
            path,
            target,
            document,
            replacement_config=deepcopy(node.config),
            shared_config_confirmed=True,
        )
    for edit in edits:
        relative = edit.path.relative_to(root).as_posix()
        destination = safe_path(root, relative)
        if edit.after is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(edit.after)


def static_code_issues(root: Path, node: RecoveryDraftNode) -> list[RecoveryIssue]:
    """Reject definite unbound globals in the regenerated function, without running it."""
    if node.node_type in {NodeType.SUBMODEL, NodeType.SUBMODEL_PORT}:
        return []
    source = safe_path(root, node.source_file).read_bytes().decode("utf-8-sig")
    try:
        symbols = symtable.symtable(source, node.source_file, "exec")
    except SyntaxError as exc:
        return [RecoveryIssue(path="/code", code="invalid_code", message=str(exc))]
    globals_ = {
        name
        for name in symbols.get_identifiers()
        if (symbol := symbols.lookup(name)).is_assigned() or symbol.is_imported()
    }
    globals_.update(vars(builtins))
    globals_.update({"__file__", "__name__", "__package__", "__doc__", "__builtins__"})
    functions = [child for child in symbols.get_children() if child.get_name() == node.authored_id]
    issues: list[RecoveryIssue] = []

    def visit(scope: symtable.SymbolTable) -> None:
        for name in scope.get_identifiers():
            symbol = scope.lookup(name)
            if symbol.is_referenced() and symbol.is_global() and name not in globals_:
                issues.append(
                    RecoveryIssue(
                        path="/code",
                        code="unbound_name",
                        message=f"{name!r} has no parameter, local or global declaration. "
                        "Update the retained code explicitly.",
                    )
                )
        for child in scope.get_children():
            visit(child)

    for function in functions:
        visit(function)
    return issues
