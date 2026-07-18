# Implementation plan — modelling-node remediation

**For the implementing agent (Opus). Read `README.md` in this folder first, then the M-doc
for each item you pick up. Every M-doc carries evidence (file:line), a fix design, and a
failing-test-first TDD plan — do not re-derive them, but DO re-verify the cited lines against
HEAD before editing (this review was cut at commit `2caa4134`, branch `code-fixes`).**

## House rules (from CLAUDE.md + project memory — binding)
1. **TDD**: for each item, write the failing test(s) named in the M-doc first, watch them
   fail for the right reason, then implement. Edge cases enumerated in the M-docs are the
   minimum, not the target.
2. **Agent pairing**: one developer + one reviewer agent per item for the silent-wrongness
   classes (Waves 1–2 below). Mechanical/UI-copy items (marked ⚙) may be batch-reviewed.
3. **Fail loud**: no fallbacks that mask wrongness. Where an M-doc proposes a warning
   (deviance clamps, tiny-dataset splits, mem-log), the warning must be *surfaced* (job
   warning / TrainResult / metrics key), not just logged.
4. **No merging**: accumulate on the working branch/PR for Ralph's independent review.
5. Preserve everything in `STRENGTHS.md` — several fixes touch the same lines.
6. Cross-check `review/MASTER/INDEX.md` before fixing anything marked "already tracked" —
   fix under the audit's ID and keep its bookkeeping consistent.

## Wave 0 — cheap guards & honest copy (all ⚙ unless marked; ~1 day)
| Item | Doc | Test-first summary |
|---|---|---|
| Tweedie variance_power validated in `resolve_loss_function` + fast 400 pre-pipeline | M02-1 | vp=1.0/2.0 raise with actionable message; 1.99 passes |
| Slider bounds 1.05–1.95 + relabel | M02-1 ⚙ | UI cannot emit 1.0/2.0 |
| Monotone/feature_weights key validation (dev+reviewer — silent-wrongness) | M09-1 | unknown key raises naming it; categorical key raises |
| GPU banner copy matches 507 reality | M07-3 ⚙ | string pinned both sides |
| Mem-log opt-in + guarded open (no default `~` file) | M05 PERF-05 + M06-4 | unset env → no file; unwritable env → run completes + one warning |
| README "probes a sample" → metadata-based wording | M05 ⚙ | docs-only |
| Classification target cardinality + string-target pre-checks | M03-2/M03-5 | 3-class → pre-fit error; "Y"/"N" → recipe message |
| Offset+classification / negative-weight fast rejection | M06-10 | 400 / prep-time error |

## Wave 1 — the offset lifecycle (CRITICAL; dev+reviewer per item; sequence matters)
Read **M01** end-to-end first; the five steps are one design.
1. `BaseAlgorithm.predict(..., baseline=None)` + CatBoost Pool-with-baseline path (M01 step 1).
2. Offset-aware `_compute_metrics` (diag + validation re-read + PDP/SHAP consistency) —
   the balance/Gini/AvE failing tests from M01 drive this (M01 step 2).
3. Contract records offset (optional field, legacy contracts still load) (M01 step 3;
   coordinates with M06-8).
4. Score-node offset mapping + fail-loud enforcement + deploy validator (M01 step 4).
5. UI guidance + un-logged-offset heuristic (M01 step 5 + M07-4) ⚙ after 1–4.

## Wave 2 — correctness & lifecycle (dev+reviewer per item)
| Item | Doc |
|---|---|
| Callback exception capture-and-reraise (memory-limit/cancel typing) | M06-1/M06-3 |
| MLflow-failure preserves TrainResult | M06-2 |
| Effective-loss recorded + RMSE-default surfaced | M02-2 |
| Tiny-dataset empty-validation warning | M06-7 |
| Temporal split sizing semantics + val=0 holdout sweep (audit ID) + group empty-train guard (audit ID) | M04-3/M04-4 |
| Double-lift exposure-weighted + tie-canonical | M03-1 |
| Deviance clamp surfacing; PDP integer grids | M03-3/M03-4 ⚙ |
| MLflow params unification (button vs auto) | M08-1 |
| Export config assembly parity (categorical levels merge, output_dir) + row-limit NOTE | M08-2/M08-3 |
| Contract → MLflow artifact + bundler per-model name | M06-8 |
| Atomic model/contract saves | M06-9 |
| Success-path unlinks via `_remove_temp_parquet`; startup temp sweep | M06-5/M06-6 |

## Wave 3 — performance (benchmark before/after using the harness in M05)
1. PDP: batch per feature + top-N cap (M05 PERF-01) — equivalence test ≤1e-9 first.
2. Partitioned/sorted split sink + pruned partition reads (PERF-02/03) — keep cleanup
   correct for directories; verify `bytes_read` per stage drops.
3. Validation eval-array reuse (PERF-04, only after 2).
4. GPU poll byte-offset seek (PERF-06) ⚙; Windows `HeapCompact` measurement (PERF-08) ⚙;
   configurable scratch dir (PERF-09) ⚙.

## Wave 4 — capability & workflow (mostly independent; ⚙ where marked)
| Item | Doc |
|---|---|
| Cancel button | M07-1 |
| Export-script button (after Wave-2 export parity) | M07-2 |
| Live loss chart during training | M07-5 |
| Typed hyperparameter inputs + eval_metric select + imbalance control | M07-6, M09-2, M09-3 |
| Quantile (+MAPE/Huber) losses + predict-shape guard | M02-4 |
| Gamma/severity recipes in docs + Tweedie hint | M02-3 ⚙ |
| Run-history ring buffer + compare strip; lock message names the blocking node | M07-7 |
| Temporal split UI sizes + partition preview | M07-8 (with M04-3) |
| Local registered-model honesty | M07-9 |
| Downstream handoff affordance | M07-10 ⚙ |
| MLflow button config threading | M07-11 ⚙ |
| Feature allowlist mode; id_columns; group seed; output_dir field; docs drift | M07-12..16 ⚙ |
| `init_model` warm start | M09-4 |
| feature_weights editor or docs | M09-5 ⚙ |

## Wave 5 — cross-validation & tuning (design-first; largest new surface)
M04-1 phases 1→3 (k-fold CV strategy → `fold_column` as user folds (M04-2) → bounded sweep).
Do not start until Waves 1–2 are green: CV multiplies whatever the fit path does, including
its bugs.

## Verification gate (after each wave)
- Full pytest suite + frontend tests green; ruff/mypy clean.
- The M01 balance test and the M06 lifecycle tests stay green from Wave 1 onward.
- After Wave 3: benchmark table (per-stage elapsed/bytes from `ExecutionContext`) committed
  alongside the change.
- Cross-wave holistic review before starting the next wave (project memory: phase recap
  before proceeding).
