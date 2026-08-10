"""Node-config dict construction for the pipeline parser.

These helpers translate the raw decorator ``kwargs`` of a ``@pipeline.<type>``
decorated function into the structured *config dict* that the frontend and
executor consume.  They also handle resolution of external JSON config
files via ``config="path/to/file.json"``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from haute._code_extraction import (
    _extract_explore_user_code,
    _extract_external_user_code,
    _extract_model_score_user_code,
    _extract_rating_step_user_code,
    _extract_scenario_expander_user_code,
    _extract_source_user_code,
    _extract_user_code,
)
from haute._config_io import NODE_TYPE_TO_FOLDER, has_config_folder, load_node_config
from haute._config_validation import validate_node_config, warn_unrecognized_config_keys
from haute._contracts import Contract, get_column_contract
from haute._edge_join import normalise_edge_join_decorator_kwargs
from haute._explore_charts import validate_explore_charts
from haute._explore_overview import validate_explore_overview
from haute._explore_pivots import validate_explore_pivots
from haute._logging import get_logger
from haute._types import (
    MODEL_SCORE_CONFIG_KEYS,
    MODELLING_CONFIG_KEYS,
    OPTIMISER_APPLY_CONFIG_KEYS,
    OPTIMISER_CONFIG_KEYS,
    SCENARIO_EXPANDER_CONFIG_KEYS,
    NodeType,
)
from haute.errors import ConfigError, ContractMismatchError

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
        # Only extract user post-processing code after the scoring call, not the
        # auto-generated scoring scaffolding that codegen produces.
        config["code"] = _extract_model_score_user_code(body) if body else ""
    elif node_type == NodeType.BANDING:
        if "factors" in decorator_kwargs:
            # Multi-factor format: factors=[{...}, {...}]
            raw_factors = decorator_kwargs["factors"]
            config["factors"] = [
                {
                    "banding": f.get("banding", "continuous"),
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
                    "banding": decorator_kwargs.get("banding", "continuous"),
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
        config["code"] = _extract_rating_step_user_code(body, param_names) if body else ""
    elif node_type == NodeType.SCENARIO_EXPANDER:
        _copy_config_keys(config, decorator_kwargs, SCENARIO_EXPANDER_CONFIG_KEYS)
        config["code"] = _extract_scenario_expander_user_code(body, param_names) if body else ""
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
        code = _extract_explore_user_code(body, param_names) if body else ""
        if code:
            config["code"] = code
        if "overview" in decorator_kwargs:
            overview = validate_explore_overview(
                decorator_kwargs["overview"],
                context="explore decorator",
            )
            if overview:
                config["overview"] = dict(overview)
        if "pivots" in decorator_kwargs:
            pivots = validate_explore_pivots(
                decorator_kwargs["pivots"],
                context="explore decorator",
            )
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
        config["code"] = _extract_user_code(body, param_names) if body else ""
        if "selected_columns" in decorator_kwargs:
            config["selected_columns"] = decorator_kwargs["selected_columns"]
        if "categorical_levels" in decorator_kwargs:
            config["categorical_levels"] = decorator_kwargs["categorical_levels"]
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
    if node_type == NodeType.MODEL_SCORE:
        config["code"] = _extract_model_score_user_code(body) if body else ""
    elif node_type == NodeType.EXTERNAL_FILE:
        config["code"] = _extract_external_user_code(body, param_names) if body else ""
    elif node_type == NodeType.POLARS:
        config["code"] = _extract_user_code(body, param_names) if body else ""
    elif node_type == NodeType.DATA_INPUT:
        config["code"] = _extract_source_user_code(body) if body else ""
    elif node_type == NodeType.SCENARIO_EXPANDER:
        config["code"] = _extract_scenario_expander_user_code(body, param_names) if body else ""
    elif node_type == NodeType.RATING_STEP:
        config["code"] = _extract_rating_step_user_code(body, param_names) if body else ""
    return config


def _is_contract_resolve_fallback_exception(exc: BaseException) -> bool:
    """Return whether *exc* should fall back to an opaque parse-time contract.

    Matches ``_execute_lazy`` while avoiding an eager module import of
    MLflow just to populate an ``except`` tuple at import time.
    """
    if isinstance(exc, (ConfigError, OSError, ImportError, RuntimeError)):
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

    try:
        derived = _derive_parse_time_contract(node_type, config)
    except Exception as exc:
        if not _is_contract_resolve_fallback_exception(exc):
            raise
        # If the builder contract cannot be resolved right now (for
        # example a missing artifact file or temporarily unavailable
        # external dependency), treat the builder as fully opaque for
        # this call. The check re-runs at execution time when runtime
        # resources are actually loaded, so a drifted annotation still
        # surfaces - just not at offline parse-time.
        # Programmer errors (AttributeError / TypeError / KeyError)
        # propagate so they aren't masked as a "harmless parse-time
        # fallback to opaque".
        derived = Contract.opaque()

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
    ignored, and points at ``haute init`` as a starter-sidecar generator. Shared
    by the healthy parse path (:func:`_resolve_node_config`) and the syntax-error
    recovery path (``_parser_regex``) so both surface the same guidance.
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
        # Code lives in the .py function body, not in the JSON file.
        config = _attach_code_from_body(loaded, node_type, body, param_names)
    elif has_config_folder(node_type):
        raise _sidecar_required_error(node_type, func_name)
    else:
        config = _build_node_config(node_type, decorator_kwargs, body, param_names)

    if node_type == NodeType.LIVE_SWITCH:
        config["inputs"] = list(edge_param_names if edge_param_names is not None else param_names)

    if node_type in {NodeType.DATA_INPUT, NodeType.DATA_OUTPUT}:
        try:
            config = validate_node_config(node_type, config)
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

    warn_unrecognized_config_keys(node_type, config)
    return node_type, config
