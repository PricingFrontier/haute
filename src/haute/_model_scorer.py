"""Encapsulates MODEL_SCORE node logic: model loading, prediction, post-processing.

Extracted from executor.py to reduce the size and nesting of ``_build_node_fn``
while keeping behaviour identical.
"""

from __future__ import annotations

import contextvars
import os
import threading
from typing import Any

import polars as pl

from haute._logging import get_logger
from haute._types import HauteError, _Frame

logger = get_logger(component="model_scorer")


# ---------------------------------------------------------------------------
# Feature mismatch error
# ---------------------------------------------------------------------------


class FeatureMismatchError(HauteError):
    """Raised when input columns don't match model feature expectations.

    Provides a human-readable message listing missing features, type
    mismatches, and actionable guidance — replacing cryptic library-internal
    errors like CatBoost's ``"Invalid cat_features[2] = 10 value: index
    must be < 9"``.
    """

    def __init__(
        self,
        *,
        expected: list[str],
        available: list[str],
        missing: list[str],
        type_mismatches: list[tuple[str, str, str]] | None = None,
    ) -> None:
        self.expected = expected
        self.available = available
        self.missing = missing
        self.type_mismatches = type_mismatches or []
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        n_expected = len(self.expected)
        n_available = len(self.available)
        n_missing = len(self.missing)

        lines: list[str] = [
            f"Feature mismatch: model expects {n_expected} feature(s) "
            f"but the input data has {n_available} column(s).",
            "",
        ]

        if self.missing:
            lines.append(f"Missing feature(s) ({n_missing}):")
            for name in self.missing[:20]:
                lines.append(f"  - {name}")
            if n_missing > 20:
                lines.append(f"  ... and {n_missing - 20} more")
            lines.append("")

        if self.type_mismatches:
            lines.append("Type mismatch(es):")
            for col, expected_type, actual_type in self.type_mismatches[:10]:
                lines.append(f"  - '{col}': model expects {expected_type}, got {actual_type}")
            lines.append("")

        lines.append(
            "These features were expected by the model but are not in the current input data."
        )
        return "\n".join(lines)


def _validate_features(
    scoring_model: Any,
    schema: pl.Schema,
) -> tuple[list[str], list[str]]:
    """Compare model features against available schema columns.

    Returns ``(usable_features, missing_features)``.
    Raises :class:`FeatureMismatchError` when any features are missing.

    Cost: O(n) set operations on column names — no data materialisation.
    """
    available = set(schema.names())
    expected = scoring_model.feature_names

    missing = [f for f in expected if f not in available]
    usable = [f for f in expected if f in available]

    # Cheap dtype checks for categorical expectations
    type_mismatches: list[tuple[str, str, str]] = []
    if scoring_model.cat_feature_names:
        for col in usable:
            if col in scoring_model.cat_feature_names:
                actual_dtype = schema[col]
                if actual_dtype.is_numeric():
                    type_mismatches.append((col, "categorical (String)", str(actual_dtype)))

    if not usable:
        raise FeatureMismatchError(
            expected=expected,
            available=sorted(available),
            missing=missing,
            type_mismatches=type_mismatches,
        )

    if missing:
        raise FeatureMismatchError(
            expected=expected,
            available=sorted(available),
            missing=missing,
            type_mismatches=type_mismatches,
        )

    if type_mismatches:
        logger.warning(
            "Feature type mismatch(es) detected — scoring will proceed but "
            "results may be affected: %s",
            type_mismatches,
        )

    return usable, missing


# Runtime scenario context — set by Pipeline.run() / Pipeline.score()
# so that score_from_config (codegen path) can pick the right strategy.
# "live" = eager in-memory scoring, anything else = disk-batched.
_scenario_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "haute_scenario",
    default="batch",
)

_SCORE_BATCH_SIZE = 500_000

# Module-level temp file cleanup — avoids accumulating atexit handlers
_temp_files_to_clean: set[str] = set()
_atexit_registered = False
_temp_cleanup_lock = threading.Lock()


def _register_temp_cleanup(path: str) -> None:
    global _atexit_registered
    with _temp_cleanup_lock:
        _temp_files_to_clean.add(path)
        if not _atexit_registered:
            import atexit

            def _cleanup_all() -> None:
                for p in _temp_files_to_clean:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

            atexit.register(_cleanup_all)
            _atexit_registered = True


def _run_score_pipeline(
    scoring_model: Any,
    lf: pl.LazyFrame,
    *,
    task: str,
    output_col: str,
    code: str = "",
    source_names: list[str] | None = None,
    extra_dfs: tuple[_Frame, ...] = (),
    source: str = "live",
    row_limit: int | None = None,
) -> _Frame:
    """Core scoring logic shared by ``ModelScorer.score()`` and deploy scorer.

    1. Intersect model features with available columns.
    2. Run eager or batched prediction.
    3. Optionally execute user post-processing code.

    Parameters
    ----------
    scoring_model
        A pre-loaded ``ScoringModel`` (from MLflow or local disk).
    lf
        The input LazyFrame to score.
    task, output_col, code, source_names
        Scoring configuration (same semantics as ``ModelScorer`` attributes).
    extra_dfs
        Additional upstream LazyFrames passed through to user code.
    source
        ``"live"`` → eager path; anything else → batched path.
    row_limit
        When set, forces the eager path regardless of source.
    """
    from haute._mlflow_io import _score_eager as score_eager_

    schema = lf.collect_schema()
    features, _missing = _validate_features(scoring_model, schema)

    try:
        if source == "live" or row_limit:
            result_lf = score_eager_(scoring_model, lf, features, output_col, task)
        else:
            result_lf = _score_batched_standalone(scoring_model, lf, features, output_col, task)
    except FeatureMismatchError:
        raise
    except Exception as exc:
        raise FeatureMismatchError(
            expected=scoring_model.feature_names,
            available=sorted(schema.names()),
            missing=[f for f in scoring_model.feature_names if f not in set(schema.names())],
        ) from exc

    if code:
        from haute.executor import _exec_user_code

        all_dfs = (result_lf,) + extra_dfs
        result_lf = _exec_user_code(
            code,
            source_names or [],
            all_dfs,
            extra_ns={"model": scoring_model},
        )
    return result_lf


def _score_batched_standalone(
    scoring_model: Any,
    lf: pl.LazyFrame,
    features: list[str],
    output_col: str,
    task: str,
) -> pl.LazyFrame:
    """Sink → batch score → lazy scan (low-memory path)."""
    input_path = _sink_to_temp(lf)
    scored_path = _batch_score_to_parquet(
        scoring_model,
        input_path,
        features,
        output_col,
        task,
    )

    _register_temp_cleanup(scored_path)
    os.unlink(input_path)
    return pl.scan_parquet(scored_path)


class ModelScorer:
    """Load an MLflow model and score a LazyFrame.

    Encapsulates the full MODEL_SCORE lifecycle:
    1. Model loading (from MLflow run or registered model).
    2. Feature intersection (skip features absent from input).
    3. Prediction (eager in-memory or batched via parquet).
    4. Optional post-processing user code.

    Parameters
    ----------
    source_type : str
        ``"run"`` or ``"registered"`` — how to locate the model in MLflow.
    run_id : str
        MLflow run ID (used when *source_type* is ``"run"``).
    artifact_path : str
        Artifact path within the run (e.g. ``"model.cbm"``).
    registered_model : str
        Registered model name (used when *source_type* is ``"registered"``).
    version : str
        Model version string (``"1"``, ``"2"``, or ``"latest"``).
    task : str
        ``"regression"`` or ``"classification"``.
    output_col : str
        Name of the column that receives predictions.
    code : str
        Optional user post-processing code applied after scoring.
    source_names : list[str]
        Sanitised upstream node names (variable names for user code).
    source : str
        Active execution source — ``"live"`` uses eager scoring, anything
        else uses the batched parquet path.
    row_limit : int | None
        When set (preview/trace), forces the eager path regardless of source.
    """

    def __init__(
        self,
        *,
        source_type: str,
        run_id: str = "",
        artifact_path: str = "",
        registered_model: str = "",
        version: str = "latest",
        task: str = "regression",
        output_col: str = "prediction",
        code: str = "",
        source_names: list[str] | None = None,
        source: str = "live",
        row_limit: int | None = None,
    ) -> None:
        self.source_type = source_type
        self.run_id = run_id
        self.artifact_path = artifact_path
        self.registered_model = registered_model
        self.version = version
        self.task = task
        self.output_col = output_col
        self.code = code
        self.source_names = list(source_names) if source_names else []
        self.source = source
        self.row_limit = row_limit

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def score(self, *dfs: _Frame) -> _Frame:
        """Load the model, predict, and optionally post-process.

        Accepts one or more upstream LazyFrames (first is the scoring input).
        Returns a LazyFrame with prediction column(s) appended.
        """
        from haute._mlflow_io import load_mlflow_model

        scoring_model = load_mlflow_model(
            source_type=self.source_type,
            run_id=self.run_id,
            artifact_path=self.artifact_path,
            registered_model=self.registered_model,
            version=self.version,
            task=self.task,
        )

        lf = dfs[0] if dfs else pl.LazyFrame()
        return _run_score_pipeline(
            scoring_model,
            lf,
            task=self.task,
            output_col=self.output_col,
            code=self.code,
            source_names=self.source_names,
            extra_dfs=dfs[1:],
            source=self.source,
            row_limit=self.row_limit,
        )

    # ------------------------------------------------------------------
    # Scoring strategies
    # ------------------------------------------------------------------

    def _score_eager(
        self,
        scoring_model: Any,
        lf: pl.LazyFrame,
        features: list[str],
    ) -> pl.LazyFrame:
        """Collect and score in-memory -- delegates to shared helper."""
        from haute._mlflow_io import _score_eager as score_eager_

        return score_eager_(scoring_model, lf, features, self.output_col, self.task)

    def _score_batched(
        self,
        scoring_model: Any,
        lf: pl.LazyFrame,
        features: list[str],
    ) -> pl.LazyFrame:
        """Sink -> batch score -> lazy scan -- low-memory path."""
        input_path = _sink_to_temp(lf)
        scored_path = _batch_score_to_parquet(
            scoring_model,
            input_path,
            features,
            self.output_col,
            self.task,
        )

        _register_temp_cleanup(scored_path)
        os.unlink(input_path)
        return pl.scan_parquet(scored_path)


# ----------------------------------------------------------------------
# score_from_config — thin delegation target for codegen
# ----------------------------------------------------------------------


def score_from_config(
    *dfs: pl.LazyFrame,
    config: str,
    base_dir: str | None = None,
) -> pl.LazyFrame:
    """Score using model parameters from a JSON config file.

    Reads the config, loads the model from MLflow (auto-detecting
    CatBoost vs pyfunc flavor), and returns predictions appended to
    the input DataFrame.

    This is the delegation target generated by codegen for MODEL_SCORE
    nodes — it keeps the ``.py`` file clean while the library handles
    the heavy lifting.

    Args:
        *dfs: Upstream LazyFrame(s) — the first is used as scoring input.
        config: Path to the JSON config file (e.g.
            ``"config/model_scoring/competitor_scoring.json"``).
        base_dir: Directory to resolve *config* against.  When ``None``
            the path is resolved relative to ``Path.cwd()``.  Codegen
            templates pass ``Path(__file__).parent`` so the config is
            always found regardless of the working directory at runtime.
    """
    import json
    from pathlib import Path

    from haute._io import read_user_text

    config_path = Path(config)
    if base_dir is not None and not config_path.is_absolute():
        config_path = Path(base_dir) / config_path
    # Validate path stays within project directory
    resolved = config_path.resolve()
    root = (Path(base_dir) if base_dir else Path.cwd()).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Config path {config!r} resolves outside project root")
    cfg = json.loads(read_user_text(resolved))
    scorer = ModelScorer(
        source_type=cfg.get("sourceType", "run"),
        run_id=cfg.get("run_id", ""),
        artifact_path=cfg.get("artifact_path", ""),
        registered_model=cfg.get("registered_model", ""),
        version=cfg.get("version", "latest"),
        task=cfg.get("task", "regression"),
        output_col=cfg.get("output_column", "prediction"),
        source=_scenario_ctx.get(),
    )
    return scorer.score(*dfs)


# ----------------------------------------------------------------------
# Module-level helpers (shared by the class, kept out of the class body
# because they are pure functions with no dependency on instance state).
# ----------------------------------------------------------------------


def _sink_to_temp(lf: pl.LazyFrame) -> str:
    """Sink a LazyFrame to a temp parquet file via streaming.

    Uses ``fast_checkpoint=True`` for lz4 compression — these temp
    files are read back immediately for batch scoring and then deleted,
    so speed matters more than compression ratio.
    """
    import os
    import tempfile

    from haute._polars_utils import safe_sink

    fd, path = tempfile.mkstemp(
        suffix=".parquet",
        prefix="haute_score_in_",
    )
    os.close(fd)
    safe_sink(lf, path, fast_checkpoint=True)
    return path


def _batch_score_to_parquet(
    scoring_model: Any,
    input_path: str,
    features: list[str],
    output_col: str,
    task: str,
) -> str:
    """Score a parquet file in batches, return path to scored output."""
    import os
    import tempfile

    import pyarrow.parquet as pq

    from haute._mlflow_io import _append_classification_proba, _prepare_predict_frame

    fd, out_path = tempfile.mkstemp(
        suffix=".parquet",
        prefix="haute_score_out_",
    )
    os.close(fd)

    pf = pq.ParquetFile(input_path)
    writer = None
    want_proba = task == "classification"

    try:
        for batch in pf.iter_batches(
            batch_size=_SCORE_BATCH_SIZE,
        ):
            chunk_raw = pl.from_arrow(batch)
            if isinstance(chunk_raw, pl.Series):
                chunk = chunk_raw.to_frame()
            else:
                chunk = chunk_raw
            x_data = _prepare_predict_frame(
                chunk,
                features,
                cat_feature_names=scoring_model.cat_feature_names,
                flavor=scoring_model.flavor,
            )
            preds = scoring_model.predict(x_data)
            chunk = chunk.with_columns(
                pl.Series(output_col, preds),
            )
            if want_proba:
                chunk = _append_classification_proba(
                    chunk,
                    scoring_model,
                    x_data,
                    output_col,
                )
            table = chunk.to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(
                    out_path,
                    table.schema,
                )
            writer.write_table(table)
            del chunk, x_data, table
    finally:
        if writer is not None:
            writer.close()
        else:
            # Zero-row input: write an empty parquet preserving correct dtypes
            input_schema = pl.read_parquet_schema(input_path)
            empty = pl.DataFrame(
                {c: pl.Series([], dtype=input_schema.get(c, pl.Float64)) for c in features}
            ).with_columns(pl.Series(output_col, [], dtype=pl.Float64))
            if want_proba:
                empty = empty.with_columns(pl.Series(f"{output_col}_proba", [], dtype=pl.Float64))
            pq.write_table(empty.to_arrow(), out_path)
    return out_path
