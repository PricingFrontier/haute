# t-boost roadmap

## Scope

What Haute can do with a model that is exactly a set of rating tables. The
`tboost` family trains, scores, deploys and explains t-boost models like every
other family; its behaviour is specified in
[the modelling specification](../modelling/high-level.md) ("Model families")
and its [low-level specification](../modelling/low-level.md), with the Tables
result tab in
[the modelling UI specification](../frontend-modelling-optimiser-ui/high-level.md).
These packages use the fact that a t-boost model's prediction is an intercept
plus one lookup per main-effect or interaction table, with no approximation:
every relativity an analyst reads is the model, every cell has a known
training exposure, and the score of any row is a sum the analyst can rebuild.
Training mechanics, the evaluation plan and the other families are out of
scope.

Every package keeps three rules. The tables shown, exported or edited score
exactly as the model does, and a check proves it on real rows rather than
assuming it. Nothing here changes a prediction silently: re-expressing
tables (a new base level, a coarser band) preserves the score, and any change
that alters scores is an explicit analyst action that is measured and
recorded. Choices an analyst makes from evidence (an edit kept, a table dropped) are
measured on rows the edited model was not fitted on and that are not the final-test
partition, and the final-test partition is scored only once those choices are final.
A table Haute cannot represent exactly (a factored effect, an order
above what the consumer supports) is refused by name, never approximated.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| TBOOST-02 | Decision | P2 | Holdout actual-versus-expected is shown cell by cell for every table, with exposure and a credibility flag. |
| TBOOST-03 | Decision | P2 | A t-boost model unfolds into Banding and Rating Step nodes that score exactly as the model. |
| TBOOST-04 | Decision | P2 | A new model's tables are compared with another model's, and each policy's premium change is attributed to tables. |
| TBOOST-05 | Decision | P2 | An analyst adjusts relativities in the Tables tab and sees the holdout cost of each adjustment before saving it. |
| TBOOST-06 | Decision | P3 | Each table's validation value is measured by removing it, so analysts can simplify the model with evidence. |
| TBOOST-07 | Decision | P3 | Relativities are compared across validation folds and time windows to show which cells are stable. |
| TBOOST-08 | Decision | P3 | The rating tables download as a workbook, rebased to the analyst's chosen base levels. |
| TBOOST-09 | Decision | P3 | Training and scoring report how much exposure falls in cells, or carries categorical values, the model never saw in training. |
| TBOOST-10 | Decision | P3 | t-boost's surviving interactions are offered as GLM interaction candidates beside the GLM's own relativities. |

## Planned improvements

The packages are independent unless a dependency names another. `TBOOST-02`
is the cheapest and adds the most validation evidence; `TBOOST-03` and
`TBOOST-04` are the ones that change how a pricing team works.

### TBOOST-02 — Holdout actual-versus-expected cell by cell
**Why:** The existing AvE pane bands one feature at a time on Haute's own
bins, which are not the model's cells. A t-boost model is a set of tables, so
the useful question is whether each table's cells are right on held-out rows:
a cell whose actual-to-expected ratio sits away from 1 on the final test
partition points at one table and one cell to revisit. Interaction tables in
particular cannot be checked by one-way AvE at all.

**Plan:** After the final fit, assign every diagnostics row to its cell in
each table (the cell index t-boost scores it with) and aggregate actual,
expected and exposure per cell on the final-test partition, development rows
being labelled as such exactly as the other panes do. Expected is the model's
full prediction, so a cell's ratio measures the whole model in that cell, not
the table alone. Show it in the Tables tab beside the table: a main effect as
actual and expected per cell with an exposure strip, an interaction as an A/E
heatmap over the same axes the table uses. For a pair of features the model
has no table for, the analyst can request the same A/E heatmap over the two
features' main-effect cells, which is how a missing interaction shows. Each
cell carries a credibility
flag from its mass (weight times exposure) and the Poisson (or the family's
variance) standard error of its ratio, so a thin cell is shown as thin rather
than alarming.

**Acceptance:** On a synthetic book with a known age-by-vehicle interaction,
a model limited to main effects (`max_interaction_order` 1) shows, in the
requested age-by-vehicle cross-tab over its main-effect cells, the interacting
cells away from 1 beyond their standard error on the final-test partition; a
model that fits the age-by-vehicle table shows that table's cells within their
standard error. Cell exposure in the
pane sums to the partition's exposure. A development-only result labels the
pane as development diagnostics.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_tboost.py::TBoostModel.table_report`;
t-boost 0.6.2 `TBoostRegressor.actual_vs_expected` (one-way, by factor level)
and `_TableModel.cell_indices`; `src/haute/modelling/_evaluation.py`.

### TBOOST-03 — Unfold a t-boost model into rating steps
**Why:** A pricing team deploys rating tables, not boosters. Haute already has
Banding and one-, two- and three-factor Rating Step nodes that run the same
in the preview, a standalone run and a deployed bundle. If a t-boost model
unfolds into those nodes, the deployed pipeline needs no model runtime, every
lookup appears in the trace like any rating step, and the tables can be
reviewed and versioned as pipeline configuration.

**Plan:** An action on a completed t-boost result writes, into the pipeline
beside the Model Training node, one Banding node per feature axis (numeric
borders as breakpoints with the border closure t-boost uses, categorical
levels grouped by the cell they share, a null to the missing cell) and one
Rating Step node holding every table, combined with `multiply` from the base
`exp(f0)` under a log link or `add` from `f0` under the identity link, with
the offset multiplying the result when the model has one. A logit model ends
in an Expression node applying the inverse link. The action refuses, naming
the table, a model with a factored effect, a table above three factors, or a
categorical level the banding cannot express. After writing, Haute scores the
diagnostics rows through both the new nodes and the model and refuses to keep
the nodes if any prediction differs beyond float tolerance. t-boost compares
features as float32, so a value just above a border in Float64 can still fall
in the lower cell: the unfolded pipeline casts each numeric feature to Float32
before banding (or the banding compares Float32 values), and the parity check
adds probe rows immediately either side of every cut, a null for every feature,
and every categorical level, beyond the diagnostics rows. A categorical value
outside the fitted levels still fails in the unfolded pipeline, through the
rating step's loud miss policy.

**Acceptance:** A Poisson model with an offset and two-way interactions
unfolds into nodes whose output matches the model on every final-test row
and every border probe within `prediction_tolerance`; a quote with an unseen
vehicle class fails in the unfolded pipeline as it does in the model; the trace of one quote lists each table lookup
with its cell and relativity; a model with an order-4 table is refused with
the table named and nothing written.

**Dependencies:** None. The parity check reuses the model scorer.

**Evidence:** `src/haute/_rating.py` (one- to three-factor lookups and
`_combine_rating_columns`); `src/haute/_banding_config.py`;
`specs/rating/high-level.md`; t-boost 0.6.2 `tables()` axes (`borders`,
`levels`, `default_cell`, `rare_pooled`, `unseen_cell`) and `factored`.

### TBOOST-04 — Model-to-model table comparison and premium attribution
**Why:** A rate review compares a proposed model with the one in force. When
both are tables, the comparison is exact: each policy's premium ratio is the
product of its per-table relativity ratios (the sum of link-scale
differences), so the dislocation of every policy is attributed to the tables
that caused it, with no Shapley approximation.

**Plan:** Compare two t-boost models, or a t-boost model with the Rating Step
tables already in the pipeline, on a chosen book. Per table, show the two
relativity curves or surfaces aligned on a common grid (the union of both
models' borders) and the exposure-weighted change. Per policy, decompose the
log premium ratio into per-table terms, plus the base and any table present in
only one model; show the distribution of premium change and, for any band of
it, which tables drive it. The decomposition sums exactly to each policy's
change.

**Acceptance:** On two models fitted to different years of the same book, the
per-policy attributions sum to the log premium ratio within float tolerance
for every row, and a table added in the newer model appears as its own
driver.

**Dependencies:** None; `TBOOST-03` makes the Rating Step comparison exact
for unfolded models.

**Evidence:** t-boost 0.6.2 `predict_contributions` (exact per-table link
contributions); `src/haute/modelling/_tboost.py::TBoostModel.contributions`.

### TBOOST-05 — Analyst adjustments with measured cost
**Why:** Analysts smooth, cap, monotonise and merge relativities before a
model is filed. Today that happens outside the model, in a spreadsheet, and
its cost in predictive accuracy is never measured. Because a t-boost score is
a sum of table values, re-scoring after an edit is cheap and exact.

**Plan:** Adjustment is a training mode, chosen before training, with a
single validation partition and a final-test partition in the evaluation plan.
In it, the job stops after the selection fit, which saw only the training
partition, and computes no final-test diagnostics. In the Tables tab the
analyst edits that model's tables: sets a cell, caps a range, merges adjacent
cells, or applies t-boost's own banding or monotone graduation to one table.
Each edit re-scores the validation partition immediately and shows the change
in deviance and Gini against the unedited model. When the analyst finishes,
the edited selection fit is the deployable model (a development refit would
fit different cells than the ones edited, so none runs), and the final-test
partition is scored once, labelled as the evaluation of the adjusted model. Saving writes a new `.tboost` whose tables carry the edits and records
an adjustment ledger (table, cell, before, after, holdout cost, author, time)
in the model and its MLflow run and model card. The original fit stays
available for comparison.

**Acceptance:** In adjustment mode the edited model's training rows are
exactly the training partition and no final-test row is scored before editing
ends (asserted on the partitions each scoring call receives); capping a
relativity re-scores the validation partition and reports the deviance change
within a second on 1,000,000 rows; the saved
model scores the edited value; the ledger lists the edit with its measured
cost; reverting it restores the original predictions exactly.

**Dependencies:** `TBOOST-02` (the same per-cell holdout aggregation).

**Evidence:** t-boost 0.6.2 `_TableModel.band`, `apply_graduation` and
`_TableBank.score_cells`; the `band_tolerance` parameter.

### TBOOST-06 — Table ablation on holdout
**Why:** t-boost prunes tables on its own internal evidence. An analyst
deciding what to file wants Haute's holdout evidence too: how much deviance
and Gini each table earns on rows the fit never saw, like a GLM's term tests.

**Plan:** In `TBOOST-05`'s adjustment mode, for every table of the selection
fit, score the validation partition without it (its contribution removed, the
intercept re-centred so the total matches the book) and report the change in
deviance and Gini, ranked. The selection fit never saw those rows, and the
final-test partition is scored only after the analyst's choices, as in
`TBOOST-05`. A table whose
removal costs nothing on holdout is marked as a simplification candidate,
which `TBOOST-05` can drop.

**Acceptance:** On a synthetic book with one noise feature, the noise
feature's table shows a holdout cost within its standard error of zero and
the true effects show material costs.

**Dependencies:** `TBOOST-05` (the adjustment mode).

**Evidence:** t-boost 0.6.2 `predict_contributions`; `pruning_report_`
(`table_scores`).

### TBOOST-07 — Relativity stability across folds and time
**Why:** A relativity that moves between validation folds or between policy
years is not one to file. Tables make the comparison direct, cell by cell.

**Plan:** When the evaluation plan has several folds, or a temporal plan has
several windows, keep each fold's model. Folds differ in borders, level
groupings, surviving tables and the mass their tables are centred on, so a
raw cell-by-cell comparison would confuse redistribution between tables with
instability. Compare on one basis instead: evaluate every fold's model on a
common grid (the union of the folds' borders per feature, and the fitted
levels), re-express each fold's effect for the same feature set against one
reference measure (the final model's training mass), and treat a table absent
from a fold as zero there, flagged as absent rather than stable. Show each
cell's range across folds beside the final model's value, flagging cells whose
range crosses 1 or exceeds a chosen width.

**Acceptance:** On a synthetic book with one fixed main effect and one effect
whose sign alternates by year, a temporal t-boost result marks every cell of
the fixed effect stable (range within the chosen width) and the alternating
effect unstable; a table that only some folds kept is shown as absent in the
others.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_tuning.py` (fold fits);
`src/haute/modelling/_evaluation.py`.

### TBOOST-08 — Rebased rating-table workbook
**Why:** Rating engines and filings take tables with a base level of 1.00 per
factor, usually the most exposed level. t-boost centres tables on training
exposure instead. Re-expressing them against a different base changes which
table holds the shared mass but not any prediction, so it can be done
exactly.

**Plan:** Download the tables as a workbook: one sheet per table with its
cells, relativity and training mass, plus a base sheet. The analyst picks a
base cell per feature (default: the most massive). Rebasing is an anchoring
transformation Haute computes, not one of t-boost's reference measures: each
table's values at the chosen base slice move into the lower-order tables on
its remaining features and finally into the intercept (for a two-way table,
its values along the base row move into the other feature's main effect, its
values along the base column into this feature's, and the corner into the
intercept), which can create a lower-order table that pruning had removed.
Afterwards every table reads 0 on the link scale, relativity 1.00, at its base
cells, and the sum of all tables is unchanged on every cell. Rates are
reconstructed without the offset, and the workbook states that a total
multiplies by the exposure. t-boost's standard-error bands describe the
original purification, so a rebased workbook omits them rather than mislabel
them. The workbook records the check that rebased tables reproduce the model's
link score on the diagnostics rows.

**Acceptance:** On a fixture with non-constant exposure, a rebased workbook's
base times its relativities reproduces every final-test row's rate, and that
product times the row's exposure reproduces Haute's served prediction, both
within float tolerance; every chosen base cell reads 1.00.

**Dependencies:** None.

**Evidence:** t-boost 0.6.2 `tables()` dense tensors and `f0`;
`frontend/src/panels/modelling/modelExport.ts`.

### TBOOST-09 — Out-of-support exposure at scoring time
**Why:** Every cell records the training exposure behind it, and many
interaction cells have none. A quote landing in an empty cell is priced by
extrapolation. Model Scoring can say so precisely.

**Plan:** When a Model Scoring node scores a t-boost model, report per table
the share of scored rows (and exposure, when the offset is present) whose cell
had zero training support, and a population stability index of the scored
rows over each table's cells against training. Report too, per categorical
feature, the rows and exposure holding values the fit never saw (scored in
the rare cell; t-boost's `unseen_values` counts them), in the training result
for the validation and final-test rows and in Model Scoring; today these are
only logged. Show it
in the preview and the trace of an individual quote.

**Acceptance:** Scoring a book shifted towards young drivers reports a higher
out-of-support share for the age interactions and a raised stability index
for the age table; a quote in an empty cell is flagged in its trace.

**Dependencies:** None.

**Evidence:** t-boost 0.6.2 table `support`; `src/haute/_model_scorer.py`.

### TBOOST-10 — Interactions as GLM candidates
**Why:** Teams that file GLMs want to know which interactions are worth
adding. A t-boost fit on the same data finds them and measures their share of
variance; its main effects are also a check on the GLM's relativities.

**Plan:** On a t-boost result, list the surviving interaction tables with
their variance shares and offer them as interaction entries for a GLM node on
the same data, in the GLM configuration's own terms format. Beside a GLM
result on the same features, show t-boost's main-effect relativities against
the GLM's.

**Acceptance:** Choosing a t-boost interaction adds the corresponding
interaction to the GLM node's configuration, which then trains.

**Dependencies:** None.

**Evidence:** `src/haute/modelling/_glm_terms.py`; t-boost 0.6.2 table
`sobol` shares.
