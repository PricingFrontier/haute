"""Adversarial repro for claim:
  signature-build-aborts-mlflow-run-on-temporal-decimal-features

Claim: a model whose feature/target dtype is TEMPORAL/DECIMAL
(pl.Date, pl.Datetime, pl.Time, pl.Decimal, pl.Duration) produces a
dtype *string* (via _polars_dtype_name's str(dtype) fall-through) that
build_signature -> _map_dtype does not understand, raising ValueError.
That call happens inside mlflow.start_run() with NO try/except around
build_signature in _build_signature_for_log / _log_model_with_signature,
so it aborts the MLflow logging run (after the native model + per-model
feature_contract.json were already written by _save_artifacts).

This repro substantiates the *core mechanism* (the only part that does
not require real model training):

  1. _polars_dtype_name(<temporal/decimal dtype>) falls through to
     str(dtype) and returns a string OUTSIDE the 4 dtypes
     build_signature understands.
  2. build_signature(... that string ...) raises ValueError with the
     specific "Unknown polars dtype" message.
  3. _build_signature_for_log(...) — the in-mlflow-run call site — does
     NOT catch that ValueError; it propagates (proving there is no
     guard at the logging boundary).  (Its only try/except is around the
     .cbm feature-name inspection, which is not exercised here.)

ISOLATION: pure in-memory. No disk I/O, no project root, never touches
src/ tests/ rating/. Uses a non-existent NON-.cbm model path so
_build_signature_for_log stays entirely in memory.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from haute.modelling._signature import _map_dtype, build_signature
from haute.modelling._training_job import _polars_dtype_name
from haute.modelling._mlflow_log import _build_signature_for_log

# Dtypes the signature builder claims to support.
SUPPORTED = {"Int64", "Float64", "String", "Boolean"}

# Temporal / decimal Polars dtypes that the claim says fall through.
TEMPORAL_DECIMAL_DTYPES = [
    ("Date", pl.Date),
    ("Datetime", pl.Datetime("us")),
    ("Time", pl.Time),
    ("Duration", pl.Duration("us")),
    ("Decimal", pl.Decimal(precision=18, scale=2)),
]


def main() -> None:
    # ---- Part 1: _polars_dtype_name fall-through produces an unsupported
    #              dtype name for every temporal/decimal dtype. -------------
    produced_names: dict[str, str] = {}
    for label, dtype in TEMPORAL_DECIMAL_DTYPES:
        # Build a tiny 1-row frame so we exercise the *actual* dtype object
        # the training-job snapshot would see (schema_df[f].dtype).
        s = pl.Series("d", [None], dtype=dtype)
        produced = _polars_dtype_name(s.dtype)
        produced_names[label] = produced
        assert produced not in SUPPORTED, (
            f"EXPECTED {label} to fall through to an UNSUPPORTED dtype name, "
            f"but _polars_dtype_name returned {produced!r} which IS supported "
            f"-> claim's premise refuted for {label}."
        )
    print("Part 1 OK: _polars_dtype_name fall-through names:", produced_names)

    # Sanity: the supported numeric/string/bool dtypes are NOT affected
    # (collapse correctly) — proves the bug is specific to temporal/decimal.
    assert _polars_dtype_name(pl.Int32) == "Int64"
    assert _polars_dtype_name(pl.Float32) == "Float64"
    assert _polars_dtype_name(pl.Categorical) == "String"
    assert _polars_dtype_name(pl.Boolean) == "Boolean"

    # ---- Part 2: build_signature raises ValueError for the Date feature. --
    date_name = produced_names["Date"]  # 'Date'
    raised_value = None
    try:
        build_signature(
            features=["d"],
            feature_types={"d": date_name},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
            task="regression",
        )
    except ValueError as exc:  # noqa: PERF203
        raised_value = exc
    assert raised_value is not None, (
        "EXPECTED build_signature to raise ValueError for a Date feature dtype, "
        "but it returned a signature -> claim refuted."
    )
    msg = str(raised_value)
    assert "Unknown polars dtype" in msg and date_name in msg, (
        f"ValueError raised but message unexpected: {msg!r}"
    )
    print("Part 2 OK: build_signature raised:", msg)

    # Direct _map_dtype check for every temporal/decimal name.
    for label, name in produced_names.items():
        try:
            _map_dtype(name)
        except ValueError:
            continue
        raise AssertionError(
            f"EXPECTED _map_dtype({name!r}) to raise for {label}, but it did not."
        )
    print("Part 3 OK: _map_dtype raises for all temporal/decimal names.")

    # ---- Part 4: the in-mlflow-run call site does NOT catch it. -----------
    # Use a NON-.cbm, non-existent path so _build_signature_for_log never
    # touches disk and goes straight to build_signature with our types.
    fake_model = Path("nonexistent_model.rsglm")
    propagated = None
    try:
        _build_signature_for_log(
            model_file=fake_model,
            task="regression",
            features=["d"],
            feature_types={"d": "Date"},
            categorical_features=[],
            target_name="y",
            target_type="Float64",
        )
    except ValueError as exc:
        propagated = exc
    assert propagated is not None, (
        "EXPECTED ValueError to PROPAGATE out of _build_signature_for_log "
        "(no try/except guard at the logging boundary), but it was swallowed "
        "or a signature was returned -> claim refuted (a guard exists)."
    )
    assert "Unknown polars dtype" in str(propagated)
    print("Part 4 OK: _build_signature_for_log PROPAGATED:", str(propagated))

    # ---- Part 5: a Datetime *target* (numeric features) also aborts -------
    # demonstrates target_type path, not just feature_types.
    dt_name = produced_names["Datetime"]
    aborted = None
    try:
        _build_signature_for_log(
            model_file=fake_model,
            task="regression",
            features=["x"],
            feature_types={"x": "Float64"},
            categorical_features=[],
            target_name="y",
            target_type=dt_name,
        )
    except ValueError as exc:
        aborted = exc
    assert aborted is not None, (
        "EXPECTED Datetime target_type to abort signature build, but it did not."
    )
    print("Part 5 OK: Datetime target aborts:", str(aborted))

    print("\nREPRO CONFIRMED: temporal/decimal feature/target dtypes abort "
          "the MLflow signature build with ValueError and the logging-boundary "
          "call site does not guard against it.")


if __name__ == "__main__":
    main()
