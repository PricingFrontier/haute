"""Node-config dict construction for the pipeline parser.

These helpers translate the raw decorator ``kwargs`` of a ``@pipeline.<type>``
decorated function into the structured *config dict* that the frontend and
executor consume.  They also handle resolution of external JSON config
files via ``config="path/to/file.json"``.
"""

from __future__ import annotations

import ast
import io
import json
import tokenize
from pathlib import Path
from typing import Any

from haute._code_extraction import (
    extract_user_code,
    is_declaration_body,
    normalise_user_code,
)
from haute._config_io import NODE_TYPE_TO_FOLDER, has_config_folder, load_node_config
from haute._config_validation import (
    reject_removed_config_keys,
    reject_unrecognized_config_keys,
    validate_node_config,
)
from haute._contracts import Contract, get_column_contract
from haute._edge_join import normalise_edge_join_decorator_kwargs
from haute._explore_charts import validate_explore_charts
from haute._explore_overview import validate_explore_overview
from haute._explore_pivots import validate_explore_pivot_state
from haute._logging import get_logger
from haute._polars_steps import (
    STEPPED_NODE_TYPES,
    STEPPED_TRANSFORM_INPUT_MAPPING_MESSAGE,
    PolarsStepError,
    render_polars_steps,
    step_input_names,
    stepped_surface_for,
)
from haute._standalone_nodes import CODE_NODE_TYPES
from haute._types import (
    COLUMN_CONFIG_KEYS,
    MODEL_SCORE_CONFIG_KEYS,
    MODELLING_CONFIG_KEYS,
    OPTIMISER_APPLY_CONFIG_KEYS,
    OPTIMISER_CONFIG_KEYS,
    SCENARIO_EXPANDER_CONFIG_KEYS,
    NodeType,
)
from haute.errors import ConfigError, ContractMismatchError, ParseError

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
    """Build the config dict for a node given its type and decorator kwargs."""
    reject_removed_config_keys(node_type, decorator_kwargs)
    config: dict[str, Any] = {}
    if node_type == NodeType.API_INPUT:
        config["path"] = decorator_kwargs.get("path", "")
        # `tables[]` is the schema mapping, typically loaded from the sidecar
        # by ``_resolve_node_config``; `row_id_column` belongs to each table.
        if isinstance(decorator_kwargs.get("tables"), list):
            config["tables"] = decorator_kwargs["tables"]
        if isinstance(decorator_kwargs.get("contract"), str):
            config["contract"] = decorator_kwargs["contract"]
    elif node_type == NodeType.LIVE_SWITCH:
        config["input_scenario_map"] = decorator_kwargs.get("input_scenario_map", {})
        config["inputs"] = param_names
    elif node_type == NodeType.EDGE_JOIN:
        config.update(normalise_edge_join_decorator_kwargs(decorator_kwargs))
    elif node_type == NodeType.MODEL_SCORE:
        for key in MODEL_SCORE_CONFIG_KEYS:
            # Decorator uses snake_case "source_type"; config uses camelCase "sourceType"
            decorator_key = "source_type" if key == "sourceType" else key
            if decorator_key in decorator_kwargs:
                config[key] = decorator_kwargs[decorator_key]
        config["code"] = extract_user_code(body, kind="hook") if body else ""
    elif node_type == NodeType.BANDING:
        if "factors" in decorator_kwargs:
            # Multi-factor format: factors=[{...}, {...}]
            raw_factors = decorator_kwargs["factors"]
            config["factors"] = [
                {
                    "banding": f.get("banding", ""),
                    "column": f.get("column", ""),
                    "outputColumn": f.get("output_column", ""),
                    "rules": f.get("rules", []),
                    "default": f.get("default"),
                }
                for f in (raw_factors if isinstance(raw_factors, list) else [])
            ]
        else:
            # Single-factor format → wrap into factors array
            config["factors"] = [
                {
                    "banding": decorator_kwargs.get("banding", ""),
                    "column": decorator_kwargs.get("column", ""),
                    "outputColumn": decorator_kwargs.get("output_column", ""),
                    "rules": decorator_kwargs.get("rules", []),
                    "default": decorator_kwargs.get("default"),
                }
            ]
    elif node_type == NodeType.RATING_STEP:
        config["tables"] = [
            {
                "factors": table.get("factors", []),
                "outputColumn": table.get("output_column", ""),
                "defaultValue": table.get("default_value"),
                "entries": table.get("entries", []),
            }
            for table in decorator_kwargs.get("tables", [])
        ]

        combined_outputs = decorator_kwargs.get("combined_outputs")
        if combined_outputs is not None:
            config["combinedOutputs"] = [
                {
                    "outputColumn": output.get("output_column", ""),
                    "operation": output.get("operation", "multiply"),
                    "baseValue": output.get("base_value"),
                }
                for output in combined_outputs
            ]
        config["code"] = extract_user_code(body, kind="hook") if body else ""
    elif node_type == NodeType.SCENARIO_EXPANDER:
        _copy_config_keys(config, decorator_kwargs, SCENARIO_EXPANDER_CONFIG_KEYS)
        config["code"] = extract_user_code(body, kind="hook") if body else ""
    elif node_type == NodeType.OPTIMISER_APPLY:
        for key in OPTIMISER_APPLY_CONFIG_KEYS:
            decorator_key = "source_type" if key == "sourceType" else key
            if decorator_key in decorator_kwargs:
                config[key] = decorator_kwargs[decorator_key]
    elif node_type == NodeType.OPTIMISER:
        _copy_config_keys(config, decorator_kwargs, OPTIMISER_CONFIG_KEYS)
    elif node_type == NodeType.MODELLING:
        _copy_config_keys(config, decorator_kwargs, MODELLING_CONFIG_KEYS)
    elif node_type == NodeType.CONSTANT:
        raw_values = decorator_kwargs.get("values", [])
        config["values"] = [
            {"name": v.get("name", ""), "value": str(v.get("value", ""))}
            for v in (raw_values if isinstance(raw_values, list) else [])
        ]
    elif node_type in (
        NodeType.DATA_INPUT,
        NodeType.DATA_OUTPUT,
        NodeType.EXTERNAL_FILE,
        NodeType.OUTPUT,
    ):
        # Config-folder nodes: format/mode/source fields/arguments live in the
        # JSON sidecar loaded via config= *before* this builder runs, so this
        # branch is unreachable on the healthy path (the caller raises
        # ConfigError when the sidecar is absent). Kept
        # explicit so a stray inline decorator can't fall to the transform
        # branch and pick up a `code` config.
        pass
    elif node_type == NodeType.EXPLORE:
        code = extract_user_code(body, kind="hook") if body else ""
        if code:
            config["code"] = code
        # Explore has no sidecar: its steps travel as a decorator argument
        # beside its pivots and charts, and are reconciled with the body below.
        if "steps" in decorator_kwargs:
            config["steps"] = decorator_kwargs["steps"]
        if "overview" in decorator_kwargs:
            overview = validate_explore_overview(
                decorator_kwargs["overview"],
                context="explore decorator",
            )
            if overview:
                config["overview"] = dict(overview)
        if "pivots" in decorator_kwargs or "pivot_formulas" in decorator_kwargs:
            shared_formulas, pivots = validate_explore_pivot_state(
                decorator_kwargs.get("pivot_formulas"),
                decorator_kwargs.get("pivots", []),
                context="explore decorator",
            )
            if shared_formulas:
                config["pivot_formulas"] = shared_formulas
            if pivots:
                config["pivots"] = pivots
        if "charts" in decorator_kwargs:
            charts = validate_explore_charts(
                decorator_kwargs["charts"],
                context="explore decorator",
            )
            if charts:
                config["charts"] = charts
    else:
        # transform
        config["code"] = (
            extract_user_code(body, kind="polars", param_names=param_names) if body else ""
        )
    _copy_config_keys(config, decorator_kwargs, COLUMN_CONFIG_KEYS)
    # Instance reference (works for any node type)
    if "of" in decorator_kwargs:
        config["instanceOf"] = decorator_kwargs["of"]
    # ``inputMapping`` is also used by ordinary Polars transforms to retain a
    # stable logical input name across topology rewrites.  Keep the decorator
    # metadata on parse so graph -> source -> graph remains a fixpoint.
    if "inputMapping" in decorator_kwargs:
        config["inputMapping"] = decorator_kwargs["inputMapping"]
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
    return Contract.from_tuple(get_column_contract(node_type, config))


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
    declared = Contract.from_user_declared(user_declared)
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
    ``<type>`` placeholder), states that any inline keyword arguments were
    ignored, and points at ``haute init`` as a starter-sidecar generator. Raised
    from the parse path (:func:`_resolve_node_config`), which editor recovery
    shares, so both surface the same guidance.
    """
    folder = NODE_TYPE_TO_FOLDER[node_type]
    return ConfigError(
        f"Node type {node_type.value!r} stores its config in a JSON sidecar; "
        f'reference it with config="config/{folder}/<name>.json" '
        f"(inline keyword arguments are ignored for this node type). "
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


def _comments(code: str) -> list[str]:
    return [
        token.string
        for token in tokenize.generate_tokens(io.StringIO(code).readline)
        if token.type == tokenize.COMMENT
    ]


def _same_program(rendered: str, body: str) -> bool:
    """Whether a body is a rendering of the steps, whatever its layout.

    The same syntax tree and the same comments make the same program: a body
    saved under an earlier layout or quoting of the same steps, or reformatted
    by ``ruff format``, still describes them, while any other change, a
    comment in authored free code included, is a hand edit.
    """
    if rendered == body:
        return True
    try:
        same_tree = ast.dump(ast.parse(rendered)) == ast.dump(ast.parse(body))
        return same_tree and _comments(rendered) == _comments(body)
    except (SyntaxError, tokenize.TokenError):
        return False


def validate_step_container(
    config: dict[str, Any],
    node_type: NodeType,
    config_ref: str | None,
    func_name: str,
) -> None:
    """Refuse a ``steps`` value no stepped node can use, whatever its body.

    It must be a list, and an edges surface takes its inputs from the edges,
    so it cannot sit beside an ``inputMapping``.
    """
    if not isinstance(config["steps"], list):
        raise ConfigError(
            f"{node_type.value} 'steps' must be a list.",
            func_name=func_name,
            config_path=config_ref,
        )
    if (
        stepped_surface_for(node_type).inputs == "edges"
        and config.get("inputMapping") is not None
        and not config.get("instanceOf")
    ):
        raise ConfigError(
            STEPPED_TRANSFORM_INPUT_MAPPING_MESSAGE,
            func_name=func_name,
            config_path=config_ref,
        )


def _reconcile_steps(
    config: dict[str, Any],
    node_type: NodeType,
    param_names: list[str],
    config_ref: str | None,
    func_name: str,
) -> dict[str, Any]:
    """Keep a stepped sidecar's ``steps`` only while they still render the body.

    The ``.py`` body is the runtime truth. A body that differs from the
    rendering of the persisted steps was edited by hand, so the steps are
    discarded and the node becomes code-only: the config is marked with an
    editor-state ``_steps_discarded`` reason and, for a transform (the one
    optional sidecar), the sidecar path is kept in ``_discarded_sidecar`` so
    the next save retires the file. An empty body with unrenderable steps is
    how an incomplete step list is saved, so it keeps its steps.

    The comparison is made on equal terms: the rendering is passed through
    the same extraction the body received (``normalise_user_code``), because
    a finaliser may normalise a rendering (a lone ``df = (df.head(2))``
    free-code step loses its brackets) without anyone having edited it; and
    it compares programs, not text (``_same_program``), so a body saved in an
    earlier layout or quoting of the same steps keeps them.
    """
    if "steps" not in config:
        return config
    validate_step_container(config, node_type, config_ref, func_name)
    surface = stepped_surface_for(node_type)
    steps = config["steps"]
    body_code = str(config.get("code") or "")
    try:
        rendered = render_polars_steps(
            steps, step_input_names(node_type, param_names), start=surface.start
        ).code
    except PolarsStepError as exc:
        if not body_code:
            return config
        reason = f"the steps cannot be rendered ({exc})"
    else:
        kind = _EXTRACTION_KIND_BY_CODE_TYPE[node_type]
        # The renderer's earlier call spelling is accepted too, so a body it
        # saved before keeps its steps; free code must match either way.
        earlier = render_polars_steps(
            steps,
            step_input_names(node_type, param_names),
            start=surface.start,
            spelling="earlier",
        ).code
        if any(
            _same_program(normalise_user_code(code, kind=kind), body_code)
            for code in (rendered, earlier)
        ):
            return config
        reason = "the function body no longer matches the rendered steps"
    reconciled = {k: v for k, v in config.items() if k != "steps"}
    reconciled["_steps_discarded"] = f"Steps were discarded because {reason}."
    if node_type == NodeType.POLARS and config_ref is not None:
        reconciled["_discarded_sidecar"] = config_ref
    logger.warning(
        "polars_steps_discarded",
        func_name=func_name,
        config_path=config_ref,
        reason=reason,
    )
    return reconciled


def _resolve_node_config(
    decorator_kwargs: dict[str, Any],
    body: str,
    param_names: list[str],
    n_params: int,
    base_dir: Path | None,
    func_name: str = "",
    explicit_node_type: NodeType | None = None,
    edge_param_names: list[str] | None = None,
) -> tuple[NodeType, dict[str, Any]]:
    """Resolve node type and config from decorator kwargs.

    Node types with external JSON config must provide
    ``config="config/…/name.json"``. Node types without a config folder are
    built directly from the decorator kwargs and function body.

    The *explicit_node_type* is provided by the type-specific decorator
    (e.g. ``@pipeline.polars``) and is used directly as the node type.
    *edge_param_names* narrows graph-bound configuration such as Live Switch
    ``inputs`` to positional edge slots while *param_names* remains available
    for function-body extraction.

    Returns ``(node_type, config_dict)``.
    """
    # Work on a copy to avoid mutating the caller's dict.
    decorator_kwargs = dict(decorator_kwargs)
    node_type = explicit_node_type or NodeType.POLARS
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
    if config_ref:
        normalised_ref = config_ref.replace("\\", "/")
        base = base_dir or Path.cwd()
        try:
            loaded = load_node_config(normalised_ref, base_dir=base)
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
            config = _reconcile_steps(config, node_type, param_names, normalised_ref, func_name)
    elif has_config_folder(node_type):
        raise _sidecar_required_error(node_type, func_name)
    else:
        config = _build_node_config(node_type, decorator_kwargs, body, param_names)
        if node_type in STEPPED_NODE_TYPES:
            # Decorator-carried steps (Explore) reconcile with the body the same way.
            config = _reconcile_steps(config, node_type, param_names, None, func_name)

    if node_type == NodeType.LIVE_SWITCH:
        config["inputs"] = list(edge_param_names if edge_param_names is not None else param_names)

    if node_type in {NodeType.DATA_INPUT, NodeType.DATA_OUTPUT}:
        try:
            # Presence of required locators is completeness, reported by the
            # editor document loader; structural violations still fail here.
            config = validate_node_config(node_type, config, require_complete=False)
        except ValueError as exc:
            raise ConfigError(
                str(exc),
                func_name=func_name,
                node_type=node_type.value,
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
        config["contract"] = user_contract

    reject_unrecognized_config_keys(node_type, config, node_label=func_name)
    return node_type, config
