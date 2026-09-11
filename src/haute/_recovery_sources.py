"""Raw authored settings/code evidence and scaffold matching for recovery (no execution)."""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from haute._artifact_paths import conflict, read_artifact, safe_path
from haute._ast_helpers import _extract_function_bodies, _get_decorator_kwargs
from haute._code_extraction import _extract_explore_user_code
from haute._config_builder import _attach_code_from_body
from haute._config_io import (
    _normalise_loaded_config,
    config_path_for_node,
    has_config_folder,
    reject_duplicate_keys_hook,
)
from haute._node_config_recovery import node_config_schema
from haute._pipeline_repair import PipelineRepairError
from haute._recovery_schemas import RecoveryFieldChange
from haute._types import GraphNode, NodeData, NodeType
from haute.errors import HauteError
from haute.schemas import PipelineEditorDocument, RecoveryPipelineNode


class SharedInstanceError(PipelineRepairError):
    """A shared node instance must be recovered through its owner."""

    def __init__(self, owner: str) -> None:
        super().__init__(
            "recovery_conflict",
            f"This is a shared node instance. Recover its owner {owner!r}.",
        )
        self.owner = owner


def read_raw_node_settings(
    root: Path,
    document: PipelineEditorDocument,
    target: RecoveryPipelineNode,
) -> tuple[
    NodeType, dict[str, Any], list[RecoveryFieldChange], ast.FunctionDef, list[str], str | None
]:
    """Raw authored settings and code for one ordinary node, with provenance notes."""
    if target.node_type not in {kind.value for kind in NodeType}:
        raise conflict(
            "This node type is not installed; install its definition or edit the source."
        )
    node_type = NodeType(target.node_type)
    source_file = target.source_file or document.source_file
    source = safe_path(root, source_file).read_bytes().decode("utf-8-sig")
    tree = ast.parse(source)
    functions = [
        s for s in tree.body if isinstance(s, ast.FunctionDef) and s.name == target.authored_id
    ]
    if len(functions) != 1 or len(functions[0].decorator_list) != 1:
        raise conflict("Recovery requires one function with one recognised node decorator.")
    function = functions[0]
    decorator = function.decorator_list[0]
    kwargs = _get_decorator_kwargs(decorator)
    if "of" in kwargs or (target.config or {}).get("instanceOf"):
        raise SharedInstanceError(str(kwargs.get("of", (target.config or {}).get("instanceOf"))))
    if isinstance(decorator, ast.Call) and len({kw.arg for kw in decorator.keywords}) != len(
        decorator.keywords
    ):
        raise conflict("Duplicate decorator arguments require an explicit source correction.")
    changes: list[RecoveryFieldChange] = []
    raw: dict[str, Any] = {key: value for key, value in kwargs.items() if key != "config"}
    reference = kwargs.get("config")
    if reference is not None and not isinstance(reference, str):
        raise conflict("A node config reference must be a literal relative path.")
    if reference:
        relative = (Path(document.source_file).parent / reference).as_posix()
        content = read_artifact(root, relative)
        if content is None:
            changes.append(
                RecoveryFieldChange(
                    path="",
                    outcome="needs_review",
                    reason="Missing config file: recovery starts from current defaults.",
                )
            )
        else:
            try:
                value = json.loads(
                    content,
                    object_pairs_hook=reject_duplicate_keys_hook,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")),
                )
                if not isinstance(value, dict):
                    raise ValueError("The configuration is not an object.")
                raw = {**value, **raw}
                if node_type == NodeType.BANDING:
                    raw = _normalise_loaded_config(raw, node_type)
            except (ValueError, UnicodeError) as exc:
                # There is no archive to fall back to: replacing an unreadable
                # sidecar from decorator evidence alone would silently discard
                # its contents. Reset is the explicit destructive path.
                raise conflict(
                    f"The node's configuration file {reference!r} is unreadable ({exc}). "
                    "Correct the file manually, or use Reset node to replace it."
                ) from exc
    params = [arg.arg for arg in (*function.args.posonlyargs, *function.args.args)]
    body = _extract_function_bodies(source, tree=tree)[function.name]
    raw = _attach_code_from_body(raw, node_type, body, params)
    if node_type == NodeType.EXPLORE:
        raw["code"] = _extract_explore_user_code(body, params)
    if "source_type" in raw and "sourceType" not in raw:
        raw["sourceType"] = raw.pop("source_type")
        changes.append(
            RecoveryFieldChange(
                path="/sourceType",
                outcome="retained",
                reason="Known decorator spelling: source_type maps to sourceType.",
            )
        )
    return node_type, raw, changes, function, params, reference


def require_generated_body(
    node_type: NodeType,
    authored_id: str,
    config: dict[str, Any],
    function: ast.FunctionDef,
    *,
    params: list[str],
    reference: str | None,
    receiver: str,
    config_base_depth: int,
) -> None:
    """Only regenerate a non-code node when its body is a recognised template."""
    from haute.codegen import _node_to_code

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
    ):
        raise conflict(problem)
    candidate = deepcopy(config)
    candidate.pop("contract", None)  # Matching a body never derives a runtime column contract.
    try:
        generated = _node_to_code(
            GraphNode(
                id=authored_id,
                data=NodeData(label=authored_id, nodeType=node_type, config=candidate),
            ),
            source_names=params,
            derive_contract=False,
        )
    except (HauteError, ValueError) as exc:
        raise conflict(problem) from exc
    expected = ast.parse(generated).body[0]
    assert isinstance(expected, ast.FunctionDef)
    default_reference = (
        config_path_for_node(node_type, authored_id).as_posix()
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
