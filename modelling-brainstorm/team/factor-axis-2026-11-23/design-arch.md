# Factor axis: the data foundation for cheap, exact regrouping

**Recommendation: the hybrid (c), built on (a).** Each run stores unrounded sums on fine slices. The client merges them into any grouping straight away. Kept predictions (b) come later, only for what sums cannot do. Both paths share one Python kernel and one data shape.

## What today stores (verified)

- `compute_ave_per_feature` (`_metrics.py:459`) saves **averages rounded to 6 decimal places** and exposure rounded to 4. Merging those is close, but not exact. Merging needs sums.
- Every stage snapshot carries `ave_per_feature` and `ave_left_out` (`_DIAGNOSTICS`, `_staged_runtime.py:71`). `compact_response` keeps both, so they reach the client once per stage, per model and per state.
- These also read `ave_per_feature`: MLflow (`_mlflow_log.py:157`), the model card (`_model_card.py:280`), `AveTab`, `PathCandidateSummary` and `ModellingPreview`.
- Faults found on toy data (`flag` 0/1, `ncd` 0–9 with 65% at 9, sum insured, area A–F):
  - a 0/1 column comes out as **one bin, "0–1"**;
  - `ncd` hides 9 inside "7–9";
  - sum insured is labelled "1.072e+05–1.263e+05";
  - areas come out C, A, F, E, D, B.
- `np.digitize` as called is **left-closed**: 2 falls in the upper bin. t-boost cells and the Banding node's default (`right_closed=True`) are right-closed.
- `chart_left_out`, a display setting, is in `_JOB_FIELDS`. `fit_config_digest` drops only `stages`, so it appears to change the fit digest. That is a warning: grouping must never enter the job.

## The three options

| | (a) fine sums at training | (b) recompute from Kept predictions | (c) hybrid |
|---|---|---|---|
| Speed of a change | instant, in the client | a server round trip (group-by over the diagnostics rows) | instant, with (b) on request |
| Exact for | groupings built from whole slices | any grouping | both |
| Cannot do | split by a 2nd factor, ranges on differences, cuts inside a slice | — | — |
| Storage | about 1 kB per factor per run | one float per row, per stage, per run (1M rows × 4 stages × 4 B = 16 MB a model) | both |
| Needs | nothing new | Kept predictions; `finalize` packs `source.parquet` and `plan.json` today, but no predictions | — |

(b) cannot be the base: Kept predictions is a later phase in `plan.md`, and training sets vary a lot in size.

## The slice store

**How fine.** One rule for every column, and a slice never splits a value:
- A number with ≤ 200 distinct values gets one slice per value. Driver age, vehicle group, NCD and flags all fit, so any grouping of them is exact.
- Above 200 values: about 100 slices of equal exposure, with ties kept whole. A heavy value is a slice by itself.
- Categories: every level up to 500 by exposure, then "Other (n levels)".
- Missing is always its own slice.

On the demo's frequency data, every number factor except `Density` (1,607 values) gets one slice per value:

| Factor | VehPower | VehAge | DrivAge | BonusMalus | Density | Region |
|---|---|---|---|---|---|---|
| Slices | 12 | 63 | 81 | 96 | 100 | 21 |

**Which rows the grid comes from.**
- The grid is a deterministic function of the node's source, its split plan and the column. It is built from the plan's non-test rows, so it does not depend on which diagnostics rows a run uses (a partition, pooled out-of-fold rows or holdout).
- It is cached per (source sha, plan sha) and carries a digest. Every model and stage on the node therefore gets the same slices. The axis never moves with the rows (`ui.md`).

**Shape.** Stored in columns, in three layers, each its own result field:

```
ave_grid       per factor, per node:       kind, lo[], hi[] (observed min/max) | levels[], has_missing
ave_actual     per row set:                rows[], mass[], sum_y[], sum_y2[]
ave_predicted  per run or stage:           sum_pred[]
```

- The grid and the actual layer are the same for every model with the same digest and `evaluation_identity`.
- Each run or stage adds only `sum_pred`.
- `sum_y` and `sum_y2` may also allow ranges on actual as `analytics.md` defines them: from the claim count and spread, with a dispersion per node.

**Payload (measured with `json.dumps`).** On 135k rows (20% of the demo) and 10 factors:

| Payload | Size |
|---|---|
| Today's `ave_per_feature` | 7.7 kB |
| Slices stored whole in each snapshot | 29 kB |
| Three layers | grid 4.6 kB + actual 16.7 kB once, then 7.6 kB per run or stage |

A worst case of 100 factors × 100 slices is 1.09 MB per snapshot stored whole. In layers it is about 0.9 MB once, then 172 kB per run or stage. Building the slices took 0.25 s.

**One kernel.** `slice_codes(column, grid) -> codes` and `ave_sums(codes, y, ŷ, w, e) -> sums` work for numbers, categories and Missing alike. They use one `bincount` per field, in place of today's full mask per bin, which would cost 100 masks per column.

## Merging, snapping and comparing

- **Merge.** One pure client function, `groupSlices(grid, actual, predicted, grouping) -> {groups[], snapped[]}`. A group's sums are the totals of its slices, and its averages are sum/mass, so it is exact for the groups it labels.
- **Snap.** A right-closed cut at *c* splits a slice when `lo ≤ c < hi`; for `right_closed: false` the test is `lo < c ≤ hi`.
  - On grids of one slice per value, no slice is ever split, so every cut is exact.
  - Otherwise the cut moves to the nearest slice boundary. Labels show the real edges, and a note says "Cut 30 → 29.6 (nearest slice)".
  - Snapping changes which edges are used, never whether the numbers are right.
  - When Kept predictions exist, "Make exact" sends the grouping to (b).
- **Comparability.** Series are drawn together when they share the grid digest and `evaluation_identity`. Every series is merged with the same grouping, so its groups match by construction.
  - This replaces `sameLevels` in `ModelsAveChart.tsx`, which compares labels and exposures.
  - "Binned on other rows, not drawn" stays for a mismatch.

## The grouping type, and reuse of existing code

```ts
type AxisGrouping =
  | { kind: "auto" }
  | { kind: "equal_exposure"; groups: number }
  | { kind: "every_value" }
  | { kind: "breakpoints"; rules: BreakpointRule[]; right_closed: boolean } // types/banding.ts
  | { kind: "levels"; rules: CategoricalRule[] }                            // types/banding.ts
  | { kind: "model_bands"; model_id: string; stage: ModelStage }  // that snapshot's tboost_tables
```

- **Banding node.** The rule types are `BandingFactor` without `output_column`. Path (b) assigns rows with `banding_rule_claim_expr` (`_rating.py`), the same rule matching the Banding node runs. "Copy as Banding rules" is the one explicit bridge to feature engineering.
- **t-boost bands.** `_report_axis` (`_tboost.py:91`) already gives the right-closed float32 `cuts` and the `levels[].members`. `model_bands` maps these to `breakpoints` or `levels`, then snaps them.
- **Persistence.** At first, keep the grouping per node and factor in a persisted view store, as `useStagedViews` is.
  - Order, scale and a hidden Missing level stay there for good.
  - Move the grouping into `modelling.json` (`chart_axes`, in the shared settings beside target and models) only once two things are checked. First, a config write must not mark runs stale ("saved settings"). Second, it must not change `documentExecutionKey`, which keys the saved views. It is never passed to the job or added to the digest.

## What the user sees

| Level | Experience | Cost |
|---|---|---|
| Default (no clicks) | Numbers with ≤ 20 values show one point per value. Other numbers show 10 groups of equal exposure, labelled "17–20" or "£12k–15k". Categories are in natural order, Missing last. | none |
| One click | An axis chip ("10 groups ▾") offers 5 / 10 / 20 groups, Every value and Model bands. | one chip |
| Deeper | The chip's "Custom…" opens an edge list or a level-merge list, in Banding-editor rows. | one popover |

**Leave out:**
- dragging edges on the chart, because the chart is read-only;
- an axis per model, because the axis must not move with the models;
- grouping that changes the model, because that is feature engineering;
- free-text expressions, because they belong in pre-processing.

## Build order

1. **Quick fixes**, listed below. No change to the data shape.
2. **Fine sums inline (the smallest useful cut).**
   - Add `ave_grid`, `ave_actual` and `ave_predicted` for interactive builds, as `ave_left_out` is today, built on each run's diagnostics rows.
   - Add `groupSlices` and the axis chip.
   - Derive `ave_per_feature` from the slices with one Python `group_slices`, so MLflow, the model card and `AveTab` are unchanged. One JSON fixture tests both languages.
3. **Shared layers.**
   - Build the grid from the plan's non-test rows, cached with a digest.
   - Add the three fields to `_DENSE` in `_stage_diagnostics.py`, with an `ave` kind in `_NAME`, so identical grids and actuals are stored once. Build on the approved diagnostics-reuse patch, which edits this file.
   - Compare by digest, and fetch slices per factor.
4. **Custom and Model bands.** Breakpoints and levels with snapping, and Copy as Banding rules.
5. **Kept predictions.** `POST …/ave {factor, grouping, runs}` uses the same kernel for exact cuts, splits by a 2nd factor and ranges on differences.

## Quick fixes in today's code

1. **Never split a value.** Give one bin per value when a number has ≤ 20 distinct values (`VehPower` has 12). Otherwise drop tied edges before `digitize`. This fixes "0–1" and "7–9".
2. **Equal exposure.** Cut the quantiles on cumulative mass rather than with `np.percentile` over rows, as `analytics.md` states.
3. **Right-closed bins** (`np.digitize(..., right=True)`), to match t-boost and Banding.
4. **Add `lo`/`hi` numbers to each bin** (an addition only). The client formats them, which ends the scientific labels.
5. **Natural order for the kept categories:** the Enum order or a sort aware of numbers, with "Other" and "Missing" last. The top 15 by mass are still the ones kept.
6. **Round to significant figures, not decimals.** At 6 decimal places a frequency of 0.0005 can be off by up to 0.1%.

## Concern (Settled friction)

Settled: "The node is only the connection point. It holds no config". `modelling.json` is config-backed for the node (`_gen_modelling`). If that line covers `modelling.json`, the grouping stays in the view store for good. It is then not shared with others and does not travel with the project.
