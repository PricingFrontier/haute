"""Shared config-driven node-apply helpers — the single code path.

Every stateful/behavioural :class:`~haute._types.NodeType` transform lives
here as one ``apply_*_from_config`` / ``expand_*`` / ``select_*`` function so
that BOTH sides of the system run identical logic:

- the canvas graph executor (:mod:`haute._builders`) delegates to it, and
- the standalone ``.py`` file emitted by :mod:`haute.codegen` imports and
  calls it (via the :mod:`haute.graph_utils` facade).

This makes the README "it is just Python" promise real: a saved pipeline's
``pipeline.run()`` / ``pipeline.score()`` executes the SAME function the
GUI executor calls, instead of a silent passthrough.  These are the
optimiser / optimiserApply / scenarioExpander / liveSwitch twins that sit
beside the banding / rating twins in :mod:`haute._rating`.

Module-level imports are deliberately minimal (polars + the frame alias);
executor-side and I/O collaborators are imported lazily inside each
function so this module never forms an import cycle with ``_builders``.
"""

from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import Any

import polars as pl

from haute._types import _Frame

# ── Scenario-expander defaults ────────────────────────────────────────────
# Canonical home; re-exported from ``_builders`` for the chunking /
# optimiser-service call sites that import them from there.
_DEFAULT_SCENARIO_MIN = 0.8  # scenario expander lower bound
_DEFAULT_SCENARIO_MAX = 1.2  # scenario expander upper bound
_DEFAULT_SCENARIO_STEPS = 21  # number of steps in scenario grid


def _resolve_node_config(
    config: dict[str, Any] | str | PathLike[str],
    base_dir: str | Path | None,
) -> dict[str, Any]:
    """Resolve a config argument that may be an inline dict or a sidecar path.

    A ``dict`` is returned as-is; a path is loaded through
    :func:`haute._config_io.load_node_config` relative to *base_dir* (which
    generated code sets to ``Path(__file__).parent``).
    """
    if isinstance(config, dict):
        return config
    from haute._config_io import load_node_config

    config_path = config if isinstance(config, str) else Path(config)
    return load_node_config(config_path, base_dir=Path(base_dir) if base_dir else None)


# ---------------------------------------------------------------------------
# liveSwitch
# ---------------------------------------------------------------------------


def select_live_switch_input(
    input_scenario_map: dict[str, str],
    source: str,
    frames: dict[str, _Frame],
    input_order: list[str],
) -> _Frame:
    """Select the liveSwitch input mapped to the active runtime *source*.

    Shared by the executor's ``_build_live_switch`` and the generated
    ``@pipeline.live_switch`` body so the canvas and a standalone
    ``pipeline.run()`` route the SAME branch instead of the generated file
    hard-wiring the ``"live"`` input.

    *frames* maps each input's function/parameter name to its frame;
    *input_order* is the declared input order used for the
    unmapped-scenario fallback (the first declared input).
    """
    for inp, scn in input_scenario_map.items():
        if scn == source and inp in frames:
            return frames[inp]
    if input_scenario_map:
        from haute._logging import get_logger

        get_logger(component="executor").warning(
            "live_switch_unmapped_scenario",
            source=source,
            mapped_scenarios=list(input_scenario_map.values()),
            falling_back_to=input_order[0] if input_order else "<none>",
        )
    if not frames:
        raise ValueError("live_switch received no input DataFrames")
    if input_order and input_order[0] in frames:
        return frames[input_order[0]]
    return next(iter(frames.values()))


# ---------------------------------------------------------------------------
# scenarioExpander
# ---------------------------------------------------------------------------


def expand_scenarios_from_config(
    lf: _Frame,
    config: dict[str, Any] | str | PathLike[str],
    *,
    base_dir: str | Path | None = None,
) -> _Frame:
    """Expand each row into a scenario grid, per the expander config.

    The generated-code twin of the executor's scenario-expander builder:
    saved pipeline files embed
    ``expand_scenarios_from_config(df, "config/expander/<name>.json",
    base_dir=...)`` so a standalone ``pipeline.run()`` expands exactly like
    the GUI executor instead of passing the frame straight through.
    """
    cfg = _resolve_node_config(config, base_dir)

    col_name = (cfg.get("column_name") or "").strip()
    raw_min = cfg.get("min_value")
    min_val = float(raw_min) if raw_min is not None else _DEFAULT_SCENARIO_MIN
    raw_max = cfg.get("max_value")
    max_val = float(raw_max) if raw_max is not None else _DEFAULT_SCENARIO_MAX
    raw_steps = cfg.get("steps")
    steps = int(raw_steps) if raw_steps is not None else _DEFAULT_SCENARIO_STEPS
    if steps < 1:
        raise ValueError(f"Scenario expander requires steps >= 1, got {steps}")
    step_col = cfg.get("step_column") or "scenario_index"

    scenario_exprs = [pl.lit(list(range(steps))).alias(step_col)]
    explode_cols = [step_col]
    if col_name:
        import numpy as np

        vals = np.linspace(min_val, max_val, steps)
        # Float32 to match Rust QuoteGrid schema (price-contour ingests f32).
        scenario_exprs.append(pl.lit(vals.astype("float32").tolist()).alias(col_name))
        explode_cols.append(col_name)
    cast_exprs = [pl.col(step_col).cast(pl.Int32)]
    if col_name:
        cast_exprs.append(pl.col(col_name).cast(pl.Float32))
    return lf.with_columns(scenario_exprs).explode(explode_cols).with_columns(cast_exprs)


# ---------------------------------------------------------------------------
# optimiserApply
# ---------------------------------------------------------------------------


def _resolve_artifact_path(path: str, base_dir: str | Path | None) -> str:
    """Anchor a relative optimiser artifact path to *base_dir* when given.

    Called from generated code with ``base_dir=Path(__file__).parent`` so a
    saved pipeline finds a project-relative artifact regardless of the
    working directory.  Executor callers pass ``base_dir=None`` — leaving the
    path untouched, exactly as the in-process executor resolved it before.
    """
    if base_dir and path and not Path(path).is_absolute():
        return str(Path(base_dir) / path)
    return path


def apply_optimiser_apply_from_config(
    *dfs: _Frame,
    config: dict[str, Any] | str | PathLike[str],
    base_dir: str | Path | None = None,
    source_names: list[str] | None = None,
    source_ids: list[str] | None = None,
) -> _Frame:
    """Apply a saved optimiser artifact to the selected input frame.

    The generated-code twin of the executor's ``_build_optimiser_apply``.
    Loads the artifact (file or MLflow) named by *config*, selects the
    ratebook input (via ``ratebook_input`` matched against *source_ids*),
    and dispatches to the online / ratebook apply.  When no source is
    configured the node is a passthrough (first frame), mirroring the
    executor's unconfigured branch.

    *dfs* are the incoming frames in declared order; *source_names* /
    *source_ids* are aligned identifier lists.  In generated standalone
    code the sidecar's ``ratebook_input`` is remapped to the source
    function name, so *source_ids* there are the parameter names; in the
    executor they are the graph node ids.  Either way selection is
    positional, so both agree.
    """
    cfg = _resolve_node_config(config, base_dir)

    source_type = cfg.get("sourceType", "")
    artifact_path = cfg.get("artifact_path", "")
    if artifact_path and not source_type:
        from haute.errors import ConfigError

        raise ConfigError(
            "optimiserApply node with artifact_path requires sourceType='file'",
            missing_field="sourceType",
        )
    run_id = cfg.get("run_id", "")
    registered_model = cfg.get("registered_model", "")
    has_file = bool(artifact_path) and source_type == "file"
    has_mlflow = source_type in ("run", "registered") and (
        (source_type == "run" and run_id) or (source_type == "registered" and registered_model)
    )
    if not has_file and not has_mlflow:
        return dfs[0] if dfs else pl.LazyFrame()

    version_col = cfg.get("version_column", "__optimiser_version__")
    optimised_value_col = cfg.get("optimised_value_column", "")
    ratebook_input = cfg.get("ratebook_input", "")
    names = list(source_names) if source_names is not None else []
    ids = list(source_ids) if source_ids is not None else []

    if source_type in ("run", "registered"):
        from haute._optimiser_io import load_mlflow_optimiser_artifact

        artifact = load_mlflow_optimiser_artifact(
            source_type=source_type,
            run_id=run_id,
            registered_model=registered_model,
            version=cfg.get("version", "latest"),
        )
    else:
        from haute._optimiser_io import load_optimiser_artifact

        artifact = load_optimiser_artifact(_resolve_artifact_path(artifact_path, base_dir))

    # Selection + dispatch live in _builders (widely re-exported); imported
    # lazily to keep this module free of an executor import cycle.
    from haute._builders import _dispatch_apply, _select_optimiser_apply_input

    input_lf = _select_optimiser_apply_input(dfs, artifact, ratebook_input, names, ids)
    return _dispatch_apply(input_lf, artifact, version_col, optimised_value_col)
