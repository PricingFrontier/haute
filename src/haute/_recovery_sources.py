"""Raw authored settings/code evidence for recovery (no execution)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from haute._artifact_paths import conflict, read_artifact, safe_path
from haute._ast_helpers import _function_body_source, _get_decorator_kwargs
from haute._code_extraction import is_declaration_body
from haute._config_builder import (
    _attach_code_from_body,
    _with_lifted_steps,
    uncalled_function_body,
)
from haute._config_io import (
    config_from_sidecar,
    reject_duplicate_keys_hook,
)
from haute._keyword_parameters import KEYWORD_PARAMETERS_KEY, keyword_parameters_from_signature
from haute._polars_steps import STEPPED_NODE_TYPES
from haute._recovery_schemas import RecoveryFieldChange
from haute._source_layout import python_lines
from haute._standalone_nodes import CODE_NODE_TYPES
from haute._types import NodeType
from haute.errors import ParseError
from haute.schemas import PipelineEditorDocument, RecoveryPipelineNode


def read_raw_node_settings(
    root: Path,
    document: PipelineEditorDocument,
    target: RecoveryPipelineNode,
) -> tuple[NodeType, dict[str, Any], list[RecoveryFieldChange]]:
    """Raw authored settings and code for one ordinary node, with provenance notes."""
    if target.node_type not in {kind.value for kind in NodeType}:
        raise conflict(
            "This node type is not installed; install its definition or edit the source."
        )
    node_type = NodeType(target.node_type)
    source_file = target.source_file or document.source_file
    source = safe_path(root, source_file).read_bytes().decode("utf-8-sig")
    tree = ast.parse(source)
    # An async node function is refused by the parser; recovery rewrites it as a plain def.
    functions = [
        s
        for s in tree.body
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == target.authored_id
    ]
    if len(functions) != 1 or len(functions[0].decorator_list) != 1:
        raise conflict("Recovery requires one function with one recognised node decorator.")
    function = functions[0]
    decorator = function.decorator_list[0]
    kwargs = _get_decorator_kwargs(decorator)
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
        # A sidecar-backed node takes its settings from the sidecar alone, as
        # the parser does: any other decorator keyword is removed, never
        # written over the sidecar.
        for key in sorted(raw.keys() - {"contract"}):
            changes.append(
                RecoveryFieldChange(
                    path=f"/{key}",
                    outcome="removed",
                    reason=(
                        f"The decorator keyword {key!r} has no effect beside config=; "
                        f"the node takes its settings from {reference}."
                    ),
                )
            )
            del raw[key]
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
                # The file's format decoded, as a load decodes it: the settings as the
                # editor holds them, which recovery checks against the contract.
                raw = {**config_from_sidecar(node_type, value), **raw}
                if node_type in STEPPED_NODE_TYPES and "steps" in raw:
                    # The form before a stepped node's code was its only copy of its
                    # steps (READ-44): they are rebuilt from the body below.
                    del raw["steps"]
                    changes.append(
                        RecoveryFieldChange(
                            path="/steps",
                            outcome="removed",
                            reason=(
                                f"{reference} held the node's steps, which its code now "
                                "holds alone; they are rebuilt from the function's body."
                            ),
                        )
                    )
            except (ValueError, UnicodeError) as exc:
                # There is no archive to fall back to: replacing an unreadable
                # sidecar from decorator evidence alone would silently discard
                # its contents. Reset is the explicit destructive path.
                raise conflict(
                    f"The node's configuration file {reference!r} is unreadable ({exc}). "
                    "Correct the file manually, or use Reset node to replace it."
                ) from exc
    params = [arg.arg for arg in (*function.args.posonlyargs, *function.args.args)]
    body = _function_body_source(python_lines(source), function)
    keyword_only = [arg.arg for arg in function.args.kwonlyargs]
    body_dropped = uncalled_function_body(node_type, body, [*params, *keyword_only], params)
    if body_dropped:
        # The body has no place under the current contract; moved into a hook,
        # its old calls would name inputs the hook never binds.
        changes.append(
            RecoveryFieldChange(path="/code", outcome="removed", reason=_uncalled_reason(node_type))
        )
        body = ""
    raw = _attach_code_from_body(raw, node_type, body, params)
    if node_type in STEPPED_NODE_TYPES and node_type != NodeType.POLARS:
        if "steps" in raw:
            # An Explore decorator's steps, the form before READ-43.
            del raw["steps"]
            changes.append(
                RecoveryFieldChange(
                    path="/steps",
                    outcome="removed",
                    reason=(
                        "The decorator held the node's steps, which its code now holds "
                        "alone; they are rebuilt from the function's body."
                    ),
                )
            )
        # A stepped node has its steps rebuilt from its body (READ-43, READ-44), as a load
        # rebuilds them; one whose body was dropped is written as a declaration, which has
        # none yet.
        raw = _with_lifted_steps(
            raw,
            function.name,
            node_type,
            declaration=body_dropped or is_declaration_body(body),
        )
    try:
        keyword_parameters = keyword_parameters_from_signature(
            function.args, node_type=node_type, stepped=bool(reference), node_id=function.name
        )
    except ParseError as exc:
        # The regenerated function cannot carry the form: say so, as for a body.
        changes.append(
            RecoveryFieldChange(
                path=f"/{KEYWORD_PARAMETERS_KEY}",
                outcome="removed",
                reason=f"{exc.message} Recover writes the function without it.",
            )
        )
    else:
        if keyword_parameters:
            raw[KEYWORD_PARAMETERS_KEY] = keyword_parameters
    return node_type, raw, changes


def _uncalled_reason(node_type: NodeType) -> str:
    """Why recover dropped a function body the decorator never calls, and what to do."""
    replaced = (
        "so its decorator never runs this body. Recover replaced the function with the "
        "node's declaration; the removed lines are in the source diff."
    )
    if node_type not in CODE_NODE_TYPES:
        return f"This node type ({node_type.value}) carries no code, {replaced}"
    marker = "keyword-only obj" if node_type == NodeType.EXTERNAL_FILE else "first parameter df"
    return (
        f"The function is not a hook ({marker}), {replaced} "
        "Add any custom code back in the node's editor."
    )
