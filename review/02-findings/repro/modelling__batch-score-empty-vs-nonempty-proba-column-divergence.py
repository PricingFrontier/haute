"""Adversarial repro for claim:
'batch-score-empty-vs-nonempty-proba-column-divergence'

Claim (a): For task='classification' with a model whose predict_proba ATTRIBUTE
exists but RETURNS None at runtime, the NON-EMPTY batch path writes NO *_proba
column, while the ZERO-ROW path writes an empty Float64 '<col>_proba' column
(because can_predict_proba is fixed from attribute presence). => schema diverges
with row count.

We test the claim's OWN repro setup: a real haute ScoringModel carrier wrapping a
stub whose .predict_proba is a callable returning None.

What we assert:
  * Build the input via tempfile only (no real project files).
  * Run _batch_score_to_parquet on a NON-EMPTY parquet -> capture schema/raise.
  * Run _batch_score_to_parquet on a ZERO-ROW parquet -> capture schema.
  * Decide whether the claimed *silent column divergence* (non-empty: no proba,
    zero-row: empty proba) actually happens.

If the non-empty path RAISES (loud) instead of silently dropping the proba column,
the claim's mechanism (a) is REFUTED for the ScoringModel carrier.
"""

import os
import tempfile
import traceback

import numpy as np
import polars as pl
import pyarrow.parquet as pq

from haute._mlflow_io import ScoringModel
from haute._model_scorer import _batch_score_to_parquet, _raw_model_supports_predict_proba


class _StubReturnsNoneProba:
    """Stub model: predict works, predict_proba ATTRIBUTE exists but returns None."""

    cat_feature_names = frozenset()

    def predict(self, x_data):
        # x_data is a pandas/polars frame produced by _prepare_predict_frame.
        n = len(x_data)
        return np.zeros(n, dtype=float)

    def predict_proba(self, x_data):
        # Attribute present and callable, but returns None at runtime.
        return None


def _make_input_parquet(n_rows: int) -> str:
    fd, path = tempfile.mkstemp(suffix=".parquet", prefix="repro_score_in_")
    os.close(fd)
    df = pl.DataFrame({"f0": pl.Series([float(i) for i in range(n_rows)], dtype=pl.Float64)})
    pq.write_table(df.to_arrow(), path)
    return path


def _run_path(carrier, n_rows: int):
    """Return ('schema', columns) on success or ('raise', exc_text)."""
    in_path = _make_input_parquet(n_rows)
    try:
        out_path = _batch_score_to_parquet(
            carrier,
            in_path,
            features=["f0"],
            output_col="prediction",
            task="classification",
        )
    except Exception as exc:  # noqa: BLE001 - we are classifying behaviour
        return ("raise", f"{type(exc).__name__}: {exc}")
    finally:
        try:
            os.unlink(in_path)
        except FileNotFoundError:
            pass
    try:
        cols = list(pq.ParquetFile(out_path).schema_arrow.names)
    finally:
        try:
            os.unlink(out_path)
        except FileNotFoundError:
            pass
    return ("schema", cols)


def main() -> None:
    carrier = ScoringModel(
        model=_StubReturnsNoneProba(),
        feature_names=["f0"],
        cat_feature_names=frozenset(),
        flavor="pyfunc",
    )

    can = _raw_model_supports_predict_proba(carrier)
    print(f"[fact] _raw_model_supports_predict_proba(carrier) = {can}")
    assert can is True, "Precondition: attribute presence must make can_predict_proba True"

    # Sanity: what does the carrier's predict_proba actually return?
    pp = carrier.predict_proba(pl.DataFrame({"f0": [1.0]}).to_pandas())
    print(f"[fact] carrier.predict_proba(...) -> repr={pp!r}, is None={pp is None}")

    nonempty = _run_path(carrier, n_rows=3)
    print(f"[non-empty path] outcome={nonempty[0]} detail={nonempty[1]}")

    zero = _run_path(carrier, n_rows=0)
    print(f"[zero-row  path] outcome={zero[0]} detail={zero[1]}")

    proba_col = "prediction_proba"

    # --- Evaluate the claim ---------------------------------------------------
    # Claim (a) requires BOTH:
    #   non-empty -> SUCCESS schema WITHOUT proba_col
    #   zero-row  -> SUCCESS schema WITH proba_col
    claim_a_nonempty_silent_drop = (
        nonempty[0] == "schema" and proba_col not in nonempty[1]
    )
    claim_a_zero_has_proba = zero[0] == "schema" and proba_col in zero[1]
    claim_a_divergence = claim_a_nonempty_silent_drop and claim_a_zero_has_proba

    print("\n=== VERDICT EVALUATION ===")
    print(f"claim(a) non-empty silently drops proba : {claim_a_nonempty_silent_drop}")
    print(f"claim(a) zero-row emits empty proba col  : {claim_a_zero_has_proba}")
    print(f"claim(a) SCHEMA DIVERGENCE reproduced    : {claim_a_divergence}")

    if nonempty[0] == "raise":
        print(
            "\n[CONCLUSION] Non-empty path RAISED loudly instead of silently dropping "
            "the proba column. With a real ScoringModel, predict_proba() returning None "
            "is impossible without the attribute being absent; a callable returning None "
            "is wrapped as np.asarray(None) (0-d object array) and rejected by "
            "_positive_class_proba_vector. The silent divergence in claim (a) does NOT occur."
        )

    # The repro ASSERTS the bug does NOT manifest as described.
    # If the claim were real, claim_a_divergence would be True and this assertion would FAIL.
    assert not claim_a_divergence, (
        "CLAIM REPRODUCED: non-empty path silently produced no proba column while "
        "zero-row path produced an empty proba column (schema diverges with cardinality)."
    )
    print("\n[REPRO RESULT] Claim (a) NOT reproduced: no silent schema divergence. "
          "Behaviour is consistent (both consistent, or non-empty fails loud).")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("\n!!! ASSERTION FAILED (claim would be REAL) !!!")
        traceback.print_exc()
        raise
