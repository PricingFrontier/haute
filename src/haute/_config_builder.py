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
    _extract_external_user_code,
    _extract_model_score_user_code,
    _extract_rating_step_user_code,
    _extract_scenario_expander_user_code,
    _extract_source_user_code,
    _extract_user_code,
)
from haute._config_io import has_config_folder, load_node_config
from haute._config_validation import warn_unrecognized_config_keys
from haute._contracts import Contract, get_column_contract
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
    "_resolve_node_config",
]

logger = get_logger(component="parser_helpers.config")

SOURCE_DTYPE_CONFIG_KEYS: tuple[str, ...] = (
    "schema_overrides",
    "dtypes",
    "column_dtypes",
    "schema",
    "categorical_levels",
)


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
        if decorator_kwargs.get("row_id_column"):
            config["row_id_column"] = decorator_kwargs["row_id_column"]
        _copy_config_keys(config, decorator_kwargs, SOURCE_DTYPE_CONFIG_KEYS)
    elif node_type == NodeType.DATA_SOURCE:
        config["path"] = decorator_kwargs.get("path", "")
        if "table" in decorator_kwargs:
            config["sourceType"] = "databricks"
            config["table"] = decorator_kwargs["table"]
            if "http_path" in decorator_kwargs:
                config["http_path"] = decorator_kwargs["http_path"]
            if "query" in decorator_kwargs:
                config["query"] = decorator_kwargs["query"]
        else:
            config["sourceType"] = "flat_file"
        _copy_config_keys(config, decorator_kwargs, SOURCE_DTYPE_CONFIG_KEYS)
    elif node_type == NodeType.LIVE_SWITCH:
        config["input_scenario_map"] = decorator_kwargs.get("input_scenario_map", {})
        config["inputs"] = param_names
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
                    "outputColumn": f.get("output_column", f.get("outputColumn", "")),
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
        if "tables" in decorator_kwargs:
            raw_tables = decorator_kwargs["tables"]
            config["tables"] = [
                {
                    "name": t.get("name", ""),
                    "factors": t.get("factors", []),
                    "outputColumn": t.get("output_column", t.get("outputColumn", "")),
                    "defaultValue": t.get("default_value", t.get("defaultValue")),
                    "entries": t.get("entries", []),
                }
                for t in (raw_tables if isinstance(raw_tables, list) else [])
            ]
        else:
            config["tables"] = []
        for t in config["tables"]:
            if not isinstance(t.get("entries"), list):
                t["entries"] = []
            if not isinstance(t.get("factors"), list):
                t["factors"] = []
        op = decorator_kwargs.get("operation", decorator_kwargs.get("op"))
        if op:
            config["operation"] = str(op)
        combined = decorator_kwargs.get(
            "combined_column",
            decorator_kwargs.get("combinedColumn"),
        )
        if combined:
            config["combinedColumn"] = str(combined)
        combined_outputs = decorator_kwargs.get(
            "combined_outputs",
            decorator_kwargs.get("combinedOutputs"),
        )
        if combined_outputs is not None:
            config["combinedOutputs"] = combined_outputs
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
    elif node_type == NodeType.DATA_SINK:
        config["path"] = decorator_kwargs.get("path", decorator_kwargs.get("sink", ""))
        config["format"] = decorator_kwargs.get("format", "parquet")
    elif node_type == NodeType.EXPLORE:
        code = _extract_user_code(body, param_names) if body else ""
        if code:
            config["code"] = code
    elif node_type == NodeType.EXTERNAL_FILE:
        config["path"] = decorator_kwargs.get("path", decorator_kwargs.get("external", ""))
        config["fileType"] = decorator_kwargs.get("file_type", "pickle")
        if config["fileType"] == "catboost":
            config["modelClass"] = decorator_kwargs.get("model_class", "classifier")
        config["code"] = _extract_external_user_code(body, param_names) if body else ""
    elif node_type == NodeType.OUTPUT:
        config["fields"] = decorator_kwargs.get("fields", [])
    else:
        # transform
        config["code"] = _extract_user_code(body, param_names) if body else ""
        if "selected_columns" in decorator_kwargs:
            config["selected_columns"] = decorator_kwargs["selected_columns"]
        if "categorical_levels" in decorator_kwargs:
            config["categorical_levels"] = decorator_kwargs["categorical_levels"]
    # Instance reference (works for any node type)
    if "instance_of" in decorator_kwargs:
        config["instanceOf"] = decorator_kwargs["instance_of"]
    elif "of" in decorator_kwargs:
        config["instanceOf"] = decorator_kwargs["of"]
    return config


def _compute_contract_resolve_fallback_exceptions() -> tuple[type[BaseException], ...]:
    """Exceptions that mean "can't resolve builder contract right now".

    Matches ``_execute_lazy._BOUNDARY_CHECK_EXCEPTIONS`` — the parse-time
    fallback must not swallow more than the runtime boundary check would.
    Programmer errors (``AttributeError`` / ``TypeError`` / ``KeyError``)
    propagate so they aren't silenced as "harmless parse-time fallback
    to opaque".
    """
    exc_types: list[type[BaseException]] = [ConfigError, OSError, ImportError, RuntimeError]
    try:
        from mlflow.exceptions import MlflowException  # type: ignore[import-untyped]

        exc_types.append(MlflowException)
    except ImportError:
        pass
    return tuple(exc_types)


def _is_contract_resolve_fallback_exception(exc: BaseException) -> bool:
    """Return whether *exc* should fall back to an opaque parse-time contract.

    Matches ``_execute_lazy`` while avoiding an eager module import of
    MLflow just to populate an ``except`` tuple at import time.
    """
    if isinstance(exc, (ConfigError, OSError, ImportError, RuntimeError)):
        return True
    try:
        from mlflow.exceptions import MlflowException  # type: ignore[import-untyped]
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


def _resolve_node_config(
    decorator_kwargs: dict[str, Any],
    body: str,
    param_names: list[str],
    n_params: int,
    base_dir: Path | None,
    func_name: str = "",
    explicit_node_type: NodeType | None = None,
) -> tuple[NodeType, dict[str, Any]]:
    """Resolve node type and config from decorator kwargs.

    Node types with external JSON config must provide
    ``config="config/…/name.json"``. Node types without a config folder are
    built directly from the decorator kwargs and function body.

    The *explicit_node_type* is provided by the type-specific decorator
    (e.g. ``@pipeline.polars``) and is used directly as the node type.

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
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
            raise ConfigError(
                "Failed to load node config; check that the path exists and "
                "contains valid JSON, or create the file.",
                original_path=config_ref,
                normalised_path=normalised_ref,
                func_name=func_name,
                base_dir=str(base),
                cause=str(exc),
            ) from exc
        config = dict(loaded)
        # Code lives in the .py function body, not in the JSON file
        if node_type == NodeType.MODEL_SCORE:
            config["code"] = _extract_model_score_user_code(body) if body else ""
        elif node_type == NodeType.EXTERNAL_FILE:
            config["code"] = _extract_external_user_code(body, param_names) if body else ""
        elif node_type == NodeType.POLARS:
            config["code"] = _extract_user_code(body, param_names) if body else ""
        elif node_type == NodeType.DATA_SOURCE:
            config["code"] = _extract_source_user_code(body) if body else ""
        elif node_type == NodeType.SCENARIO_EXPANDER:
            config["code"] = _extract_scenario_expander_user_code(body, param_names) if body else ""
        elif node_type == NodeType.RATING_STEP:
            config["code"] = _extract_rating_step_user_code(body, param_names) if body else ""
    elif has_config_folder(node_type):
        raise ConfigError(
            "Node config must be stored in a JSON sidecar and referenced with "
            'config="config/<type>/<name>.json".',
            func_name=func_name,
            node_type=node_type.value,
        )
    else:
        config = _build_node_config(node_type, decorator_kwargs, body, param_names)

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
