# Team brief: how the factor chart's x-axis is grouped, and how users control it

## The question (from Ralph, the product owner)

"When display factors in the modelling workbench, how is the x-axis grouped up. What options can we
offer users to really customise this." Then: "This is for modelling so we want users to have great
control to explore the data freely, but also without overwhelming and overcomplicating the UI and
workflow."

So the goal is twofold and in tension: **real freedom to explore** the one-way (actual against
expected by factor level), and **a UI that stays calm** — good defaults that need no touching, with
control there when wanted. Every proposal must say what the user sees by default, how many clicks
a change takes, and what it costs in screen space and concepts.

Users are pricing analysts and actuaries (UK personal lines insurance: motor, home), building
frequency and severity models (t-boost, a boosted model that ends in rating tables, today; GLM, EBM,
LightGBM, XGBoost, CatBoost later). They know one-way charts well from desktop pricing tools.
Describe conventions at the genre level; do not copy any one product.

## Where things live

- haute repo: `/home/ralph/suite/haute` (read-only for you). Design folder:
  `/home/ralph/suite/haute/modelling-brainstorm/` — `overview.md` (Settled / Open / Concerns /
  Proposed lists at the top), `ui.md` (see its **Charts** section, which already lists some "More"
  options: scale, split by a second factor, order of levels), `analytics.md` (measures; says "A
  number factor is cut into bands once per node, of equal exposure, so every run is charted on the
  same bands"), `plan.md` (build phases, including **Kept predictions**: one prediction per row kept
  per run).
- The **Settled** list is in `settled.md` beside this brief. It is Ralph's decisions in his words.
  Never contradict or reword it. If a settled line causes friction, say so as a concern with the
  smallest change, separately from your proposals.
- Hand edits (overview.md "Hand edits"): "An edit changes values, not the data. Merging levels or
  re-banding a factor is feature engineering, which stays in the pre-processing nodes." So
  display grouping must never be confused with changing the model's own bands/levels.
- The factor chart is read-only (ui.md): no dragging points to edit.

## How it works today (verified in code)

- Bins are computed **once, server-side, at training time** by `compute_ave_per_feature` in
  `src/haute/modelling/_metrics.py:459`, called from `src/haute/modelling/_training_job.py:3221`
  with defaults. Left-out factors also chart (`ave_left_out`, same function). Results are stored in
  the run result as `ave_per_feature: [{feature, type, bins: [{label, exposure, avg_actual,
  avg_predicted}]}]`. The UI cannot regroup.
- **Numeric** (`_ave_numeric_bins`): `min(10, max(2, valid_rows // 3))` bins; edges from
  `np.percentile` on raw values, **unweighted** (equal row count, not equal exposure — disagrees
  with analytics.md); `np.unique` on edges silently collapses tied cut points; labels are
  `min–max` of values in the bin at 4 significant figures; bars evenly spaced (ordinal axis);
  NaN gets a "Missing" bin at the end.
- **Categorical** (`_ave_categorical_bins`): top 15 levels by mass (weight × exposure), rest lumped
  into "Other"; **sorted by mass descending**, so ordinal categories (area A–F, vehicle group 1–50)
  come out scrambled. Missing (null) is a level named "Missing".
- Averages are ratios of sums (Σw·y / Σmass, Σw·ŷ / Σmass), so **merging bins from stored sums is
  exact**.
- Frontend: `frontend/src/modelling/ModellingView.tsx` (`FactorChart`, ~line 660) gathers series
  from each ticked model's run/stages; `frontend/src/modelling/ModelsAveChart.tsx` draws them and
  only overlays a model whose bins match the reference's labels and exposure — otherwise "Binned on
  other rows, not drawn".
- Elsewhere in haute: a Banding node with breakpoint and categorical rules
  (`frontend/src/types/banding.ts`, `src/haute/_banding_config.py`); t-boost has its own model
  bands (the "banded" stage). Features pane is for selection only — no outputs (importance etc.)
  go in the Features/FactorList pane.

## Rules for every agent

- Read the files you need yourself; verify any claim about haute's code before relying on it.
- Write in plain, short English. Mark what haute already has.
- Be concrete: defaults, numbers, where a control sits, what text it shows.
- Separate: (1) the default experience, (2) one-click controls, (3) deeper controls, (4) things to
  leave out, with reasons.
- Do not edit anything in the haute repo. Write only where your prompt says.
