"""Isolated verification for NEWBUG-CARD-NONFINITE-PARAM.

Claim (paraphrased):
  generate_model_card guards metric values with `math.isfinite(v)` (line 105)
  but renders param values with bare `str(v)` (line 235). The filed *production*
  failure path asserts that a metric value can arrive as a NON-FLOAT (e.g. None
  from a degraded GLM stat surfaced into `metrics`), which makes
  `math.isfinite(v)` raise TypeError and abort the whole HTML card, swallowed by
  the try/except in _mlflow_log.py:362-373 -> entire diagnostics artifact lost.

This script separates two questions:
  (A) MECHANISM: does math.isfinite raise on a non-float metric value, and does
      generate_model_card therefore abort, dropping the entire card?
  (B) REACHABILITY: in the real codebase, can the `metrics` dict that reaches
      generate_model_card actually contain a non-float? i.e. can compute_metrics
      (the SOLE producer of that dict) ever yield a non-float value, or do GLM
      fit statistics ever get merged into it?

Run:  uv run python review/03-simplification/repro/modelling__NEWBUG-CARD-NONFINITE-PARAM.py
"""

from __future__ import annotations

import math

import numpy as np

from haute.modelling._metrics import NON_FINITE_FILTERED_KEY, compute_metrics
from haute.modelling._model_card import generate_model_card


# ---------------------------------------------------------------------------
# (A) MECHANISM: a non-float metric value aborts the whole card.
# ---------------------------------------------------------------------------
def test_mechanism_nonfloat_metric_aborts_card() -> None:
    # math.isfinite raises TypeError on a non-float (None / str), which is the
    # crux of the filed failure mode.
    raised_on_none = False
    try:
        math.isfinite(None)  # type: ignore[arg-type]
    except TypeError:
        raised_on_none = True
    assert raised_on_none, "math.isfinite(None) should raise TypeError"

    raised_on_str = False
    try:
        math.isfinite("nan")  # type: ignore[arg-type]
    except TypeError:
        raised_on_str = True
    assert raised_on_str, "math.isfinite('nan') should raise TypeError"

    # If such a value were ever in the metrics dict, the ENTIRE card raises,
    # i.e. every chart/table is lost — not just the one metric row.
    card_raised = False
    try:
        generate_model_card(
            name="m",
            metrics={"gini": 0.5, "broken": None},  # type: ignore[dict-item]
            params={"depth": 6},
        )
    except TypeError:
        card_raised = True
    assert card_raised, (
        "generate_model_card should raise TypeError when a metric value is "
        "non-float (no per-value guard) — confirming the whole-card abort."
    )
    print("(A) MECHANISM confirmed: non-float metric -> math.isfinite TypeError -> whole card aborts")


# ---------------------------------------------------------------------------
# (B) REACHABILITY: can the production `metrics` dict hold a non-float?
# ---------------------------------------------------------------------------
def test_reachability_compute_metrics_only_yields_floats() -> None:
    rng = np.random.default_rng(0)
    y_true = rng.random(200)
    y_pred = rng.random(200)
    weight = rng.random(200) + 0.1

    # Exercise every registered metric plus the non_finite_rows_filtered path
    # (inject some non-finite rows so that surfaced entry is present too).
    y_true_nf = y_true.copy()
    y_true_nf[0] = np.nan  # force a filtered-row count entry
    out = compute_metrics(
        y_true_nf,
        y_pred,
        weight,
        ["gini", "rmse", "mae", "mse", "r2", "poisson_deviance", "tweedie_deviance"],
    )

    assert NON_FINITE_FILTERED_KEY in out, "expected the surfaced filtered-row entry to be present"

    non_float = {k: v for k, v in out.items() if not isinstance(v, float)}
    assert not non_float, (
        f"compute_metrics produced non-float values {non_float!r}; "
        "the production metrics dict is supposed to be dict[str, float]"
    )

    # And every value passes math.isfinite without raising (the guard at
    # _model_card.py:105 is therefore safe for the real production dict).
    for k, v in out.items():
        math.isfinite(v)  # must not raise

    print(
        "(B) REACHABILITY: compute_metrics (sole producer) yields ONLY floats "
        f"({sorted(out)}); the filed non-float production path is NOT reachable."
    )


# ---------------------------------------------------------------------------
# (C) SECONDARY title claim: NaN/Inf *param* renders 'nan'/'inf' via str(v),
#     while NaN/Inf *metric* renders 'N/A' — the cosmetic inconsistency.
#     (Does NOT abort the card; purely display.)
# ---------------------------------------------------------------------------
def test_param_nonfinite_renders_inconsistently() -> None:
    html_doc = generate_model_card(
        name="m",
        metrics={"gini": float("nan"), "rmse": float("inf")},
        params={"learning_rate": float("nan"), "reg": float("inf")},
    )
    # Metrics path: guarded -> 'N/A'
    assert ">N/A<" in html_doc, "non-finite metric should render as N/A"
    # Params path: unguarded -> literal 'nan'/'inf'
    assert ">nan<" in html_doc and ">inf<" in html_doc, (
        "non-finite param renders raw nan/inf (str(v)) — inconsistent with metrics"
    )
    print("(C) SECONDARY: NaN/Inf metric -> 'N/A' but NaN/Inf param -> raw 'nan'/'inf' (cosmetic).")


if __name__ == "__main__":
    test_mechanism_nonfloat_metric_aborts_card()
    test_reachability_compute_metrics_only_yields_floats()
    test_param_nonfinite_renders_inconsistently()
    print("\nALL CHECKS PASSED")
