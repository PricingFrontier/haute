"""Node-config dict construction for the pipeline parser.

These helpers translate the raw decorator ``kwargs`` of a ``@pipeline.<type>``
decorated function into the structured *config dict* that the frontend and
executor consume.  They also handle resolution of external JSON config
files via ``config="path/to/file.json"``.
"""

from __future__ import annotations

import io
import json
import tokenize
from pathlib import Path
from typing import Any

from haute._code_extraction import (
    extract_user_code,
    is_declaration_body,
)
from haute._config_io import NODE_TYPE_TO_FOLDER, has_config_folder, load_node_config
from haute._config_validation import (
    reject_removed_config_keys,
    validate_node_config,
)
from haute._contracts import Contract, declared_contract_json, get_node_contract
from haute._edge_join import normalise_edge_join_decorator_kwargs, require_edge_join_order
from haute._logging import get_logger
from haute._node_config_models import COLUMN_CONFIG_KEYS
from haute._polars_steps import (
    JOIN_MAINTAIN_ORDER,
    STEPPED_NODE_TYPES,
)
from haute._polars_steps_lift import StepLiftError, lift_steps
from haute._project_context import current_project
from haute._standalone_nodes import CODE_NODE_TYPES
from haute._types import NodeEditorState, NodeType
from haute.errors import ConfigError, ContractMismatchError, HauteError, ParseError

__all__ = [
    "_copy_config_keys",
    "_build_node_config",
    "_attach_code_from_body",
    "_resolve_node_config",
    "_sidecar_required_error",
]

logger = get_logger(component="parser.config_builder")


def _copy_config_keys(
    config: dict[str, Any],
    kwargs: dict[str, Any],
    keys: tuple[str, ...] | list[str],
) -> None:
    """Copy matching keys from *kwargs* into *config*.

    Only keys that exist in *kwargs* are copied; missing keys are
    silently skipped.  This is a convenience helper to eliminate the
    repeated ``for key in KEYS: if key in kwargs: config[key] = kwargs[key]``
    pattern in ``_build_node_config``.
    """
    for key in keys:
        if key in kwargs:
            config[key] = kwargs[key]


def _build_node_config(
    node_type: str,
    decorator_kwargs: dict[str, Any],
    body: str,
    param_names: list[str],
) -> dict[str, Any]:
    """Build the config of a node that keeps its settings on its decorator.

    Only an Edge Join and a transform do: every other node type
    keeps them in a JSON sidecar, which ``_resolve_node_config`` loads, or
    refuses the node for lacking, before this runs.
    """
    reject_removed_config_keys(node_type, decorator_kwargs)
    config: dict[str, Any] = {}
    if node_type == NodeType.EDGE_JOIN:
        config.update(normalise_edge_join_decorator_kwargs(decorator_kwargs))
    elif node_type == NodeType.POLARS:
        config["code"] = (
            extract_user_code(body, kind="polars", param_names=param_names) if body else ""
        )
    else:
        raise ValueError(f"{node_type!r} nodes keep their settings in a JSON sidecar.")
    _copy_config_keys(config, decorator_kwargs, COLUMN_CONFIG_KEYS)
    # ``input_mapping`` is also used by ordinary Polars transforms to retain a
    # stable logical input name across topology rewrites.  Keep the decorator
    # metadata on parse so graph -> source -> graph remains a fixpoint.
    if "input_mapping" in decorator_kwargs:
        config["input_mapping"] = decorator_kwargs["input_mapping"]
    return config


def _attach_code_from_body(
    config: dict[str, Any],
    node_type: NodeType,
    body: str,
    param_names: list[str],
) -> dict[str, Any]:
    """Return a config copy with user code extracted from a node body."""
    config = dict(config)
    kind = _EXTRACTION_KIND_BY_CODE_TYPE.get(node_type)
    if kind is not None:
        config["code"] = extract_user_code(body, kind=kind, param_names=param_names) if body else ""
    return config


def _is_contract_resolve_fallback_exception(exc: BaseException) -> bool:
    """Return whether *exc* is a named infrastructure failure.

    Only a missing or unreadable file, a missing optional dependency or an
    MLflow failure degrades the parse-time check to an opaque contract; a
    configuration or programmer error propagates. MLflow is imported lazily
    rather than to populate an ``except`` tuple at import time.
    """
    if isinstance(exc, (OSError, ImportError)):
        return True
    try:
        from mlflow.exceptions import MlflowException
    except ImportError:
        return False
    return isinstance(exc, MlflowException)


def _derive_parse_time_contract(node_type: NodeType, config: dict[str, Any]) -> Contract:
    """Return the contract shape that is safe to derive while parsing.

    ``MODEL_SCORE`` input columns are model-artifact metadata, so deriving
    them calls MLflow.  Parsing runs during ``haute serve`` startup, before
    the backend has bound its port, and must not block on remote model I/O.
    The output side remains a local config value, so we can still validate
    that part of a user-declared contract immediately.
    """
    if node_type == NodeType.MODEL_SCORE:
        output = config.get("output_column", "prediction")
        outputs = frozenset({output} if output else {"prediction"})
        return Contract(inputs=None, outputs=outputs)
    return get_node_contract(node_type, config)


def resolve_parse_time_contract(node_type: NodeType, config: dict[str, Any]) -> Contract:
    """Return the builder contract a parse-time declaration is checked against.

    If the builder contract cannot be resolved right now because of a named
    infrastructure failure (a missing artifact file, a missing optional
    dependency, an unreachable MLflow server), the builder is treated as fully
    opaque. The check re-runs at execution time when runtime resources are
    actually loaded, so a drifted annotation still surfaces - just not at
    offline parse-time. Configuration errors (``ConfigError``) and programmer
    errors propagate so they aren't masked as a "harmless parse-time fallback
    to opaque".
    """
    try:
        return _derive_parse_time_contract(node_type, config)
    except Exception as exc:
        if not _is_contract_resolve_fallback_exception(exc):
            raise
        return Contract.opaque()


def _validate_user_contract(
    node_type: NodeType,
    config: dict[str, Any],
    user_declared: Any,
    func_name: str,
) -> None:
    """Cross-check a user-declared contract against the builder-derived one.

    Raises :class:`ContractMismatchError` when the user's explicit
    ``contract=...`` kwarg disagrees with what the builder would derive
    from the rest of the config.  A matching declaration is silently
    accepted.  An opaque declaration on a builder that also reports
    opaque (for the relevant side) is always accepted — "I don't know"
    from both sides cannot disagree.

    The check is per-side (inputs vs outputs) so the user can declare a
    concrete ``outputs`` even when the builder's ``referenced`` side is
    opaque (MODEL_SCORE is the canonical example).
    """
    try:
        declared = Contract.from_user_declared(user_declared)
    except ValueError as exc:
        # The node's own failure, so the editor offers it Recover settings,
        # which regenerates the annotation, rather than an error the whole
        # document fails on.
        raise ConfigError(
            f"Node {func_name!r} has a contract= that cannot be read: {exc}",
            node_id=func_name,
            node_type=node_type.value,
        ) from exc
    if declared is None:
        return

    derived = resolve_parse_time_contract(node_type, config)

    mismatches: list[str] = []
    for side in ("inputs", "outputs"):
        d_val: frozenset[str] | None = getattr(declared, side)
        b_val: frozenset[str] | None = getattr(derived, side)
        # Opaque on either side → no disagreement possible for that side.
        if d_val is None or b_val is None:
            continue
        if d_val != b_val:
            missing = sorted(d_val - b_val)
            extra = sorted(b_val - d_val)
            mismatches.append(
                f"{side}: declared {sorted(d_val)!r} but builder "
                f"derives {sorted(b_val)!r} "
                f"(missing from builder: {missing!r}, extra in builder: {extra!r})"
            )

    if mismatches:
        raise ContractMismatchError(
            "User-declared contract does not match the contract derived from "
            "the node's configuration; the two must agree so the contract "
            "annotation is trustworthy.",
            node_id=func_name,
            node_type=node_type.value,
            mismatches=mismatches,
        )


def _sidecar_required_error(node_type: NodeType, func_name: str) -> ConfigError:
    """Build the error for a folder-backed node used without a ``config=`` sidecar.

    Names the concrete config folder resolved from ``NODE_TYPE_TO_FOLDER`` (not a
    ``<type>`` placeholder), says the settings belong in that file rather than
    in decorator keywords, and points at ``haute init`` as a starter-sidecar
    generator. Raised from the parse path (:func:`_resolve_node_config`), which
    editor recovery shares, so both surface the same guidance.
    """
    folder = NODE_TYPE_TO_FOLDER[node_type]
    return ConfigError(
        f"Node type {node_type.value!r} stores its config in a JSON sidecar; "
        f'reference it with config="config/{folder}/<name>.json" and put its '
        f"settings in that file, not in decorator keywords. "
        f"Run `haute init` to scaffold a starter project with example "
        f"sidecars, or create config/{folder}/<name>.json by hand.",
        func_name=func_name,
        node_type=node_type.value,
        config_folder=f"config/{folder}",
    )


#: The extraction kind of each node type whose function may carry code.
_EXTRACTION_KIND_BY_CODE_TYPE: dict[NodeType, str] = {
    NodeType.POLARS: "polars",
    NodeType.DATA_INPUT: "hook",
    NodeType.EXTERNAL_FILE: "external",
    NodeType.RATING_STEP: "hook",
    NodeType.MODEL_SCORE: "hook",
    NodeType.SCENARIO_EXPANDER: "hook",
    NodeType.EXPLORE: "hook",
}


def _is_hook(node_type: NodeType, param_names: list[str], edge_param_names: list[str]) -> bool:
    """Whether a code-carrying node's signature marks a hook its decorator calls."""
    if node_type == NodeType.EXTERNAL_FILE:
        return "obj" in param_names[len(edge_param_names) :]
    return edge_param_names[:1] == ["df"]


def uncalled_function_body(
    node_type: NodeType,
    body: str,
    param_names: list[str],
    edge_param_names: list[str],
) -> bool:
    """Whether a node's function carries code its decorator never calls.

    That is code on a type that carries none, or code on a code-carrying type
    whose function is not a hook: every generated body before node
    declarations took one of these forms. The parser rejects such a function
    (:func:`_validate_node_function`); recovery replaces it.
    """
    if node_type == NodeType.POLARS or is_declaration_body(body):
        return False
    return node_type not in CODE_NODE_TYPES or not _is_hook(
        node_type, param_names, edge_param_names
    )


def _validate_node_function(
    node_type: NodeType,
    body: str,
    param_names: list[str],
    edge_param_names: list[str],
    func_name: str,
) -> None:
    """Enforce the declaration and hook shapes a standalone run relies on.

    A configured node's function is either a declaration — a body of only
    ``...`` or ``pass`` — or a hook carrying code, whose first parameter is
    ``df`` (an External File hook takes the keyword-only ``obj`` instead) on
    a type that accepts code. The standalone runtime never calls a
    declaration and always calls a hook, so any other shape would run
    differently there than in the canvas.
    """
    if node_type == NodeType.POLARS:
        return
    declaration = is_declaration_body(body)
    if node_type not in CODE_NODE_TYPES:
        # Its parameters only name inputs (an input may be called df); the body is all
        # that can be wrong.
        if not declaration:
            raise ParseError(
                f"Node '{func_name}' has a function body, but its decorator performs the "
                f"node's work and never calls it. Its node type ({node_type.value}) carries "
                "no code; replace the body with `...`.",
                node_id=func_name,
                node_type=node_type.value,
            )
        return
    is_hook = _is_hook(node_type, param_names, edge_param_names)
    if node_type == NodeType.EXTERNAL_FILE:
        marker = "obj"
        advice = (
            "To add code, keep the inputs as parameters, add the keyword-only parameter "
            "obj and start from df = <first input>."
        )
    else:
        marker = "df"
        advice = (
            "To add code, make the first parameter df (the frame this node produced) and "
            "return the result."
        )
    if is_hook and declaration:
        raise ParseError(
            f"Node '{func_name}' takes {marker} but has no code: add the code, or declare "
            "the node with its inputs as parameters and a `...` body.",
            node_id=func_name,
            node_type=node_type.value,
        )
    if not is_hook and not declaration:
        raise ParseError(
            f"Node '{func_name}' has a function body, but its decorator performs the node's "
            f"work and never calls it. {advice}",
            node_id=func_name,
            node_type=node_type.value,
        )


def _with_lifted_steps(
    config: dict[str, Any],
    func_name: str,
    node_type: NodeType = NodeType.POLARS,
    *,
    declaration: bool = False,
) -> dict[str, Any]:
    """*config* of a stepped node with the steps rebuilt from its code (READ-22, READ-44).

    Each statement a step renders exactly becomes that step and each run of
    other statements one code step (``lift_steps``), so a hand edit or a
    comment keeps the rest. A node that maps its inputs, which a stepped node
    never does, and one whose lines do not split into statements stay
    code-only, as does one with no code: an empty transform, or the placeholder
    an unfinished step list is saved with, whose steps the loader takes from
    ``.haute.json``. A frame surface whose function is a *declaration* has no
    steps yet: an empty list, so the editor opens it on its steps.
    """
    code = str(config.get("code") or "")
    if config.get("input_mapping") is not None:
        return config
    if not code:
        return {**config, "steps": []} if declaration else config
    try:
        steps = lift_steps(node_type, code)
    except StepLiftError:
        # Spaces ending a line, outside a string, are what ``ruff format`` takes off and a
        # rendering never holds: without them the statements are what they were.
        try:
            steps = lift_steps(node_type, _without_line_end_spaces(code))
        except StepLiftError as exc:
            logger.info("polars_steps_not_rebuilt", func_name=func_name, reason=str(exc))
            return config
    return {**config, "steps": steps}


def _without_line_end_spaces(code: str) -> str:
    """*code* with the spaces ending each line taken off, but inside a multiline string."""

    kept: set[int] = set()
    try:
        for token in tokenize.generate_tokens(io.StringIO(code).readline):
            if token.type == tokenize.STRING and token.end[0] > token.start[0]:
                # Every line the string runs over but its last holds its own characters.
                kept.update(range(token.start[0], token.end[0]))
    except (tokenize.TokenError, SyntaxError):
        return code
    return "\n".join(
        line if number in kept else line.rstrip()
        for number, line in enumerate(code.split("\n"), start=1)
    )


def _resolve_node_config(
    decorator_kwargs: dict[str, Any],
    body: str,
    param_names: list[str],
    base_dir: Path | None,
    func_name: str = "",
    explicit_node_type: NodeType | None = None,
    edge_param_names: list[str] | None = None,
) -> tuple[NodeType, dict[str, Any], NodeEditorState]:
    """Resolve node type, config and editor state from decorator kwargs.

    Node types with external JSON config must provide
    ``config="config/…/name.json"``. Node types without a config folder are
    built directly from the decorator kwargs and function body.

    The *explicit_node_type* is provided by the type-specific decorator
    (e.g. ``@pipeline.polars``) and is used directly as the node type.
    *edge_param_names* narrows graph-bound configuration such as Live Switch
    ``inputs`` to positional edge slots while *param_names* remains available
    for function-body extraction.

    Returns ``(node_type, config_dict, editor_state)``: the editor state
    records a stepped node whose steps the body no longer matches.
    """
    from haute._config_validation import (
        reject_camel_case_decorator_kwargs,
        reject_removed_node_reuse_kwargs,
    )

    reject_removed_node_reuse_kwargs(decorator_kwargs)
    reject_camel_case_decorator_kwargs(decorator_kwargs, func_name)
    # Work on a copy to avoid mutating the caller's dict.
    decorator_kwargs = dict(decorator_kwargs)
    node_type = explicit_node_type or NodeType.POLARS
    editor = NodeEditorState()
    _validate_node_function(
        node_type,
        body,
        list(param_names),
        list(edge_param_names if edge_param_names is not None else param_names),
        func_name,
    )
    # Strip the ``contract=`` kwarg before delegating to the per-type
    # config builders — those builders would otherwise flag it as
    # unrecognised.  We re-attach it to the config afterwards (see
    # "Carry over the user's declared contract" below).
    user_contract = decorator_kwargs.pop("contract", None)
    config_ref = decorator_kwargs.pop("config", None)
    if config_ref is not None and not isinstance(config_ref, str):
        # The node's own failure, so the editor offers it Reset, rather than an
        # error the whole document fails on.
        raise ConfigError(
            f"Node {func_name!r} has config={config_ref!r}; config= takes the path of its JSON "
            "config file as a string.",
            func_name=func_name,
            node_type=node_type.value,
        )
    if config_ref and node_type == NodeType.POLARS:
        # A transform's steps live in its code alone (READ-22).
        raise ConfigError(
            f"Node {func_name!r} takes its steps from {config_ref}, the form before a Polars "
            "node's steps lived in its code alone. Write the steps in the function's body "
            "and remove config=.",
            func_name=func_name,
            node_type=node_type.value,
            original_path=config_ref,
        )
    if config_ref:
        if decorator_kwargs:
            # The sidecar holds every setting; a keyword beside it would be
            # dropped here and by the next save, so it fails the parse by name.
            keywords = sorted(decorator_kwargs)
            raise ConfigError(
                f"Node {func_name!r} takes its settings from {config_ref}, so its decorator "
                f"keywords {', '.join(repr(key) for key in keywords)} would be ignored: move "
                "them into that file, or remove them.",
                func_name=func_name,
                node_type=node_type.value,
                keywords=keywords,
            )
        normalised_ref = config_ref.replace("\\", "/")
        base = base_dir or current_project().root
        try:
            # The decorator names the node type, so a sidecar outside its type's folder
            # is decoded from its file format too (bands, grids, constant maps).
            loaded = load_node_config(
                normalised_ref, base_dir=base, node_type=node_type, validate=False
            )
        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            # The file is missing, unreadable, or not valid JSON — the headline
            # points at the path/parse problem. (``json.JSONDecodeError`` is a
            # ``ValueError`` subclass, so it must be caught before the content
            # handler below.)
            raise ConfigError(
                "Failed to load node config; check that the path exists and "
                "contains valid JSON, or create the file.",
                original_path=config_ref,
                normalised_path=normalised_ref,
                func_name=func_name,
                base_dir=str(base),
                cause=str(exc),
            ) from exc
        except ValueError as exc:
            # The file loaded and parsed as JSON but its *content* failed
            # schema/sidecar validation. Lead with that precise message instead
            # of masking it under the generic "check the path" headline; still
            # name the config path so the offending file is unambiguous.
            raise ConfigError(
                str(exc),
                original_path=config_ref,
                normalised_path=normalised_ref,
                func_name=func_name,
                base_dir=str(base),
            ) from exc
        # Code lives in the .py function body, not in the JSON file. Only
        # positional parameters are inputs: an External File hook's keyword-only
        # obj is never the input its generated binding names.
        config = _attach_code_from_body(
            loaded,
            node_type,
            body,
            list(edge_param_names if edge_param_names is not None else param_names),
        )
        if node_type in STEPPED_NODE_TYPES:
            # Its code is the only copy of its steps (READ-44); its sidecar holds none. A
            # declaration has none yet; a hook with no code is the unfinished placeholder.
            config = _with_lifted_steps(
                config, func_name, node_type, declaration=is_declaration_body(body)
            )
    elif has_config_folder(node_type):
        raise _sidecar_required_error(node_type, func_name)
    else:
        config = _build_node_config(node_type, decorator_kwargs, body, param_names)
        if node_type == NodeType.POLARS:
            config = _with_lifted_steps(config, func_name)

    if node_type == NodeType.LIVE_SWITCH:
        config["inputs"] = list(edge_param_names if edge_param_names is not None else param_names)

    if node_type == NodeType.EDGE_JOIN:
        # The order has no incomplete form (a new join carries its type's), so
        # a file without one predates the rule: the editor opens it for
        # recovery, and Recover settings gives the join its type's order.
        try:
            require_edge_join_order(config)
        except ConfigError as exc:
            orders = ", ".join(repr(order) for order in JOIN_MAINTAIN_ORDER)
            raise ConfigError(
                f"Edge Join {func_name!r} does not state its joined rows' order: set "
                f"maintain_order on its decorator to one of {orders}.",
                func_name=func_name,
                node_type=node_type.value,
                maintain_order=exc.context["maintain_order"],
            ) from exc

    # Cross-check a user-declared contract against the builder's.  A
    # mismatch raises ``ContractMismatchError`` — a typo in the
    # decorator should surface at parse time, not at runtime.
    _validate_user_contract(node_type, config, user_contract, func_name)

    # Carry over the user's declared contract onto the config so the
    # executor can enforce it at node boundaries (the declared form may
    # be *more* specific than the builder's derivation — e.g. a polars
    # node whose contract the user declares concretely even though the
    # builder defaults to opaque).
    if user_contract is not None:
        config["contract"] = declared_contract_json(user_contract)

    # The node type's config model, the one validation boundary. Presence of a
    # required value (a Data Input's locator) is completeness, reported by the
    # editor document loader; a malformed value fails here.
    try:
        config = validate_node_config(
            node_type, config, require_complete=False, node_label=func_name
        )
    except HauteError:
        raise
    except ValueError as exc:
        raise ConfigError(str(exc), func_name=func_name, node_type=node_type.value) from exc
    return node_type, config, editor
