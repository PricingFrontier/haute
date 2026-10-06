"""Raw authored settings/code evidence for recovery (no execution)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from haute._artifact_paths import conflict, read_artifact, safe_path
from haute._ast_helpers import _extract_function_bodies, _get_decorator_kwargs
from haute._config_builder import (
    _attach_code_from_body,
    _reconcile_steps,
    uncalled_function_body,
    validate_step_container,
)
from haute._config_io import (
    _normalise_loaded_config,
    reject_duplicate_keys_hook,
)
from haute._pipeline_repair import PipelineRepairError
from haute._polars_steps import STEPPED_NODE_TYPES
from haute._recovery_schemas import RecoveryFieldChange
from haute._standalone_nodes import CODE_NODE_TYPES
from haute._types import NodeType
from haute.errors import ConfigError
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
    if node_type in STEPPED_NODE_TYPES and "steps" in raw:
        # The ``.py`` body is the runtime truth here as it is in ordinary
        # parsing: without this, a hand-edited body would be silently
        # regenerated from stale steps when ``NodeData`` materialises the
        # recovered candidate, losing the authored edit. A dropped body was
        # never run, so nothing competes with the steps: they are settings,
        # kept to regenerate the hook.
        #
        # Recovery never raises on a bad field, so a step list the parser
        # rejects outright (a malformed container, or a list beside an
        # ``inputMapping`` an edges surface refuses) is dropped rather than
        # propagated: the node keeps the code its body already holds. The
        # key is removed, never defaulted to ``[]``, because an empty list
        # would materialise as empty code and discard that body.
        try:
            if body_dropped:
                validate_step_container(raw, node_type, reference, function.name)
                reconciled = raw
            else:
                reconciled = _reconcile_steps(raw, node_type, params, reference, function.name)
            reason = str(reconciled.get("_steps_discarded", ""))
        except ConfigError as exc:
            reconciled = {key: value for key, value in raw.items() if key != "steps"}
            reason = f"{exc}. The node keeps the code in its body."
        if "steps" not in reconciled:
            changes.append(RecoveryFieldChange(path="/steps", outcome="removed", reason=reason))
        raw = reconciled
    if "source_type" in raw and "sourceType" not in raw:
        raw["sourceType"] = raw.pop("source_type")
        changes.append(
            RecoveryFieldChange(
                path="/sourceType",
                outcome="retained",
                reason="Known decorator spelling: source_type maps to sourceType.",
            )
        )
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
