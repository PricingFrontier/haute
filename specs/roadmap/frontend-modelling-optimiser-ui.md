# Frontend modelling and optimiser UI roadmap

## Scope

The modelling panel (training configuration, the results workspace with its
evaluation, lift, residual, feature, term, SHAP and loss tabs, export and MLflow
logging) and the optimiser workspace that shares its results scaffolding.
Current behaviour is specified in
[the frontend modelling and optimiser UI specification](../frontend-modelling-optimiser-ui/high-level.md).
These packages come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FMOU-R01 | Planned | P2 | Every number the modelling panel shows is the server's: the VRAM figure is sized at the rows training will use, the Lorenz chart carries the server's Gini, percent fields round, and the summary's elapsed time is the completed job's. |
| FMOU-R02 | Planned | P2 | The training configuration offers the metrics the backend defaults to and refuses what the server refuses, with each guard message naming its cause. |
| FMOU-R03 | Planned | P3 | The frontend modelling specification describes the editor that exists; the dead sidebar component, the duplicated GLM conflict derivations and view switches, the message-prefix matching and the family-blind tune copy are gone. |

## Planned improvements

### FMOU-R01 — The server's numbers, shown as the server computed them
**Why:** The Train pane rescales the GPU VRAM figure assuming the server sized
it at `safe_row_limit ?? total_rows` rows, but the server sizes it at the full
source count, so with any row limit the figure and the "Select CPU and retry"
warning it drives are wrong by `total_rows / safe_row_limit` (ten times in the
worked case). The Lorenz chart prints a Gini recomputed in the browser from the
200-point downsampled curve, which differs from the server's `gini` on the
Summary tab at the fourth decimal both print (probed at three sample sizes).
Percent fields and the run summary render `fraction * 100` unrounded, so a
stored 0.29 displays as `28.999999999999996`. The Summary's elapsed time reads
the running job's progress, which the store removes at completion, so it never
shows for a completed result and shows a re-train's clock beside the previous
result. Detail lines and values tables print raw floats where the axes format.

**Plan:** Size VRAM on the server at the rows training will use and return the
basis; label the chart with the server's `gini` and delete `computeGini`; round
the percent displays; pass the cached result's terminal elapsed time; format
every displayed statistic through one helper. Add tests with a row limit below
the total, a pinned Lorenz label and a 0.29 fraction.

**Acceptance:** The VRAM figure with a row limit of 100,000 over 1,000,000 rows
is the 100,000-row figure; the Lorenz label equals the Summary's Gini; 0.29
displays as 29; a completed result shows its own elapsed time.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/modelling/TrainingActionsAndResults.tsx`;
`src/haute/routes/modelling.py`; `frontend/src/panels/modelling/LiftTab.tsx`;
`frontend/src/panels/modelling/SplitAndMetricsConfig.tsx`;
`frontend/src/panels/modelling/TrainingRunSummary.tsx`;
`frontend/src/panels/modelling/SummaryTab.tsx`;
`frontend/src/panels/ModellingPreview.tsx`.

### FMOU-R02 — A training configuration the server accepts
**Why:** Choosing the Gamma GLM family writes `metrics: ["gini", "rmse"]`,
overriding the backend's Gamma default of Gamma deviance, and the GLM metric
picker offers neither `gamma_deviance` nor `mse`, so a severity GLM is headlined
by squared error and the analyst cannot choose the deviance that matches its
objective. Two configurations pass the frontend guard and are refused by the
server (a text-target classification without a positive class, refused inside
the running job; a GLM theta of zero or below, refused with a 400), and tuning
with validation off is caught but reported with a message folding ten
conditions into one sentence that never says validation must be enabled.

**Plan:** Give the Gamma family the backend default and derive both metric
lists from one table; add `positive-class` and a positive-theta check to the
guard; split the tuning message by cause.

**Acceptance:** A Gamma GLM headlines Gamma deviance by default and the picker
offers it; a text-target classification without a positive class and a theta of
0 are refused in the editor with their reason; tuning without validation names
validation.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/modelling/GLMTargetConfig.tsx`;
`src/haute/modelling/_train_config.py`; `frontend/src/utils/trainingObjective.ts`;
`frontend/src/panels/modelling/TargetAndTaskConfig.tsx`.

### FMOU-R03 — The specification, the dead copy and the duplicates
**Why:** The frontend modelling specification describes an editor the code and
the modelling specification no longer have: five GLM panes with regularisation
on Target, no tab badges, Include/Exclude pills, no tune-mode explainer, a test
set seeded on "Tune parameters"; tests pin the code's behaviour. The sidebar
`FeatureImportance` component is imported only by its test, and
`refitCapability`, `fixedBudgetCapability`, `GLM_REGULARIZATIONS` and
`GLM_CV_SELECTIONS` have no reference outside their definitions. The GLM
smooth-spline and robust-standard-error conflict derivations, text included, are
implemented twice; six hand-rolled view switches exist, two with the generic
accent; two jscpd clones remain. An estimate failure is classified as an
evaluation-preview failure by matching the server's message prefix, and the
fraction-sum issue is picked out of the issue list by a regex over its own text.
The Params pane's tune copy labels every family's search space "CatBoost" and
says "+ 1 final fit" for t-boost, which is never refit.

**Plan:** Rewrite the frontend modelling passages to the code; delete the dead
component and exports; export the conflict derivations from one module; reuse
one view switch; structured codes for the preview failure and the fraction-sum
issue; the family's label and the Train pane's wording in the tune copy.

**Acceptance:** The two specifications agree with each other and the code; knip
and the quoted greps report none of the symbols; jscpd none of the pairs; no
message-prefix match remains; the search-space label names the selected family.

**Dependencies:** `FSH-R09` covers the same message-matching pattern for
previews.

**Evidence:** `specs/frontend-modelling-optimiser-ui/high-level.md`;
`frontend/src/panels/modelling/FeatureImportance.tsx`;
`frontend/src/panels/modelling/algorithmCapabilities.ts`;
`frontend/src/panels/modelling/glmFamilies.ts`;
`frontend/src/panels/modelling/GLMRegularizationConfig.tsx`;
`frontend/src/panels/modelling/TermTableView.tsx`;
`frontend/src/panels/ModellingConfig.tsx`;
`frontend/src/panels/modelling/HyperparametersConfig.tsx`.
