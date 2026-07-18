# Modelling-node review — CatBoost training from a data scientist's seat

**Date:** 2026-07-06 · **Base:** branch `code-fixes`, commit `2caa4134` · **CatBoost:** 1.2.10
**Question asked:** is the modelling node as efficient, performant, elegant and robust as it
could be — and does it give a data scientist all the tools to train the best CatBoost models
they can?
**Method:** five specialist review passes (capability gaps, statistical correctness,
performance, robustness/lifecycle, DS workflow/UX) over the full training path
(`src/haute/modelling/*`, `routes/modelling.py` + `_train_service.py`, `_model_scorer.py` /
`_mlflow_io.py` / `deploy/_scorer.py`, `frontend/src/panels/modelling/*`), with every
CatBoost behavioural claim **verified empirically against the installed package** — no
from-memory API claims. Findings below all carry file:line evidence and worked examples in
their M-docs.

---

## Verdict

The engineering foundation is genuinely strong — memory discipline, fail-loud contracts,
early-stopping semantics, and a diagnostics suite (SHAP, PDP, AvE, double-lift, Lorenz)
broader than most commercial pricing tools (see `STRENGTHS.md`). But measured against
"train the best CatBoost model an insurance DS can":

1. **The flagship actuarial workflow is silently wrong.** Train a Poisson frequency model
   with `offset = log(exposure)` — the textbook setup the UI itself suggests — and every
   reported metric and chart drops the offset: AvE reads 0.60 instead of 1.00, Gini is
   understated, deviance inflated ~3×, and the exposure never reaches scoring or deployment
   either. Confirmed by direct experiment; not caught by the June audit. (M01)
2. **The loss layer is the weakest link.** The Tweedie slider crashes at both of its
   labelled endpoints; an unset loss silently trains RMSE on skewed pricing targets; there
   is no Gamma story and no documented severity recipe; Quantile is blocked. (M02)
3. **"Best model" tooling stops at one fit on one split.** No CV, no tuning, a dead
   `fold_column`, no run comparison in-app, no eval-metric control in the UI. (M04, M09, M07)
4. **Robustness is one boundary away from its own standard.** Exceptions raised inside the
   CatBoost callback come back as `CatBoostError`, so memory-limit aborts — the laptop
   safety feature — surface as cryptic generic errors; an MLflow hiccup after a successful
   fit throws away the trained result; a running train cannot be cancelled from the UI at
   all. (M06, M07)
5. **Performance is well-engineered at the memory level, wasteful at the I/O level**: ~2
   full writes + ~5 full reads of the dataset per run (unsorted split file defeats row-group
   pruning), and PDP issues thousands of serial predict calls. (M05)

Nothing here is unfixable; most fixes are localized, and the M-docs specify each one with a
failing-test-first plan. Fix M01 + M02 and the tool is trustworthy; add Wave 3–5 and it is
genuinely competitive.

## Scorecard

| Dimension | Grade | One-liner |
|---|---|---|
| Statistical correctness | **C** | Excellent Gini/deviance core, but the offset lifecycle poisons the headline workflow (M01) |
| Loss/objective coverage | **C−** | 4 losses, crashing Tweedie endpoints, silent RMSE default (M02) |
| Efficiency / performance | **B−** | Great memory discipline; redundant full-file scans + PDP explosion (M05) |
| Robustness / lifecycle | **B−** | Strong temp/contract discipline; callback-boundary + MLflow-failure + Windows unlink gaps (M06) |
| DS tooling completeness | **C+** | Rich diagnostics, real escape hatch; no CV/tuning/compare/cancel/export UI (M04/M07/M09) |
| Elegance / architecture | **B+** | SSOT config builder, algorithm abstraction, contract subsystem — drift only at route-level assembly (M08) |

## Read in this order

| Doc | Contents | Top severity |
|---|---|---|
| `M01-offset-lifecycle.md` | Offset dropped by evaluation, scoring, contract; worked numbers | **CRITICAL ×2** |
| `M02-loss-functions.md` | Tweedie slider crash, silent RMSE, Gamma gap, allowlist | HIGH ×3 |
| `M03-evaluation-correctness.md` | Double-lift weighting, deviance clamps, PDP int grids, classification guards | MEDIUM |
| `M04-splits-and-cv.md` | Temporal/group split semantics (2 audit-tracked), dead fold_column, missing CV/tuning | HIGH |
| `M05-performance.md` | I/O accounting, PDP batching, partition pruning, mem-log hygiene | HIGH ×2 |
| `M06-robustness-lifecycle.md` | Callback exception wrapping, MLflow result loss, Windows files, contract propagation | HIGH ×2 |
| `M07-ux-workflow.md` | No cancel/export/live-loss UI, false GPU copy, run history, field gaps | HIGH ×3 |
| `M08-tracking-and-export-drift.md` | MLflow-button vs auto-log params, export config assembly | MEDIUM |
| `M09-capability-levers.md` | Monotone validation, eval_metric, imbalance, init_model, params passthrough | MEDIUM |
| `STRENGTHS.md` | Verified good parts — regression-protection list | — |
| `IMPLEMENTATION-PLAN.md` | Wave-ordered plan for the implementing agent (TDD, pairing rules) | — |

## Relationship to the June 2026 audit (`review/`)

This review is orthogonal to the audit: the audit hunted general bugs; this pass asks the
DS-capability question. Overlaps are explicitly marked "already tracked" in the M-docs (fix
under the audit IDs): classification proba-vs-hard-label divergence, temporal-mask holdout
sweep, group-mask empty-train fallback, MLflow Date-dtype abort, Databricks registration
path, pyfunc loader symbol, train-status timeout arithmetic, `test_rows` naming, GPU-VRAM
frontend denominator. The headline findings here — the offset lifecycle (M01), Tweedie
bounds (M02-1), silent RMSE (M02-2), dead fold_column, callback exception wrapping (M06-1),
MLflow result loss (M06-2), missing cancel/export UI (M07-1/2) — are **new**.

## Verification notes for the implementer

Key claims were established by running the installed CatBoost 1.2.10 directly (baseline vs
no-baseline predictions match exposure exactly; Exponent prediction space; open-interval
Tweedie bounds; `use_best_model` shrinkage through save/reload; multiclass proba shapes;
callback-exception wrapping at `helpers.cpp:58`; callback `return False` clean-stop). If your
CatBoost version differs, re-run the probes before relying on the fix designs — the M-docs
state which behaviour each fix assumes.
