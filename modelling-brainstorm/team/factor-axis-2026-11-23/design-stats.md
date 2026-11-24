# Factor chart x-axis: a statistician's view

## 1. What the code does that the brief does not say (verified)

I read `src/haute/modelling/_metrics.py:459-676` and the caller at `_training_job.py:3221`, and ran the numeric binning on synthetic data.

- **Edges move with the run.** They are cut from each run's own diagnostics rows (`diag_df`). That conflicts with ui.md ("the axis moves only with the factor or the scale") and with analytics.md ("cut into bands once per node").
- **Heavy ties swallow their neighbours.** `np.digitize(..., edges[1:-1])` puts a value that sits on an edge into the bin above it. Test: no-claims discount (NCD) 0 to 9 with 45% of rows at 9. Asking for 10 bins gave 6, and the last one, "8–9", held 5,132 of 10,000 rows. The biggest level was merged with its neighbour.
- **Rare flags collapse to one bin.** If 90% or more of the rows hold one value, the percentiles from 0 to 90 are all equal. A 0/1 column with 5% ones, or conviction points that are 0 for 92% of drivers, then becomes one bar labelled "0–1". This hits left-out booleans in particular, which are cast to Int8 and binned as numbers.
- **Tails get lumped.** Driver age (normal, mean 45, clipped to 17–99) gave "17–25" and "64–99" as single bins. Those are the two places a motor analyst looks first.
- **Missing sits in the wrong place for categoricals.** For a numeric factor, "Missing" comes at the end. For a categorical, it is ranked by mass like any other level, so it can land mid-chart or disappear into "Other".
- **Empty bins vanish.** A bin or level with zero mass is skipped, so the label list changes between runs, and the overlay then refuses to draw ("Binned on other rows").
- **The payload is too thin.** It stores only the label, the exposure and two averages rounded to 6 places. That is not enough to merge bins exactly, or to draw whiskers.

## 2. Principles

1. **Edges belong to the node, not the run.** Cut them once, from every non-test row, using factor values and mass only. Store them with the node, and bin every run on them. Mass means weight × exposure: exposure for frequency, claim count for severity. For severity, "equal exposure" really means equal claims, and the UI should say so.
2. **Store fine cells as sums, and display coarser groupings.** Each run keeps a table of sums per factor on a fine grid. Every display grouping is then an exact merge done in the browser, with no retraining. The sums for each cell:
   - Σmass, Σw, the row count, Σw·y and Σw·ŷ
   - the claim count
   - Σw·y², for the severity spread
   - the largest single claim (merged by max), for the hover's "largest claim share"
   - the minimum and maximum value seen
3. **Display grouping is display only.** Every regrouping control carries the caption "Display only. The model's bands are set in pre-processing." This follows the hand-edits rule that merging levels or re-banding a factor is feature engineering.

## 3. Default experience (no clicks)

| Choice | Default | Why |
|---|---|---|
| Numeric with ≤ 30 distinct values | One bar per value | Analysts expect NCD 0–9, vehicle age and conviction points by value. This also removes the tie and rare-flag bugs |
| Other numeric | Equal mass, **20 bands, or claims / 50 if fewer, at least 5** (analytics.md) | Each band gets about 50 claims, so ±28% at 95%. The count adapts to the size of the data |
| Tied spike | A value holding more than one band's mass gets its own band | Fixes the NCD 8–9 merge |
| Tail split | A band more than 4× the median band width is split at a round number, if each half keeps ≥ 40 claims | Brings back 17–20 / 21–25 on large books, and stays quiet on small ones |
| Edges | Snapped to round numbers (whole years; 1-2-5 × 10ᵏ for amounts), provided each band stays within ±25% of its target mass | "25–29" reads better than "24.87–29.13" |
| Labels | `[lo, hi)` from the edges. The last band is "70+", and its observed maximum is shown on hover | Labels tile with no gaps. An outlier never renames a band |
| Thin level | Under **20 claims** (analytics.md): a hollow marker with whiskers, never hidden, never merged automatically | Hiding thin levels hides the very levels the analyst needs to question |
| Whiskers on actual | Always, 95%. Frequency: an exact Poisson range from the claim count. Severity: from Σw·y² | 20 claims gives about ±44%. The analyst should see that without being told |
| Categorical levels | Up to **25** by mass, the rest as "Other (n levels)", with a search box when there are more | 15 is too few for vehicle group or region. More than 25 bars will not read |
| Categorical order | Natural for ordinal-like levels (a Polars Enum's order, numbers in natural sort so 2 comes before 10, single letters, band labels like "17–25"). Otherwise by exposure, largest first | Area A–F and vehicle group 1–50 come out in order. Today they are scrambled by mass |
| Missing, unseen | Always the last two bars: "Missing", then "Not in training". Never inside Other | ui.md already says this. The categorical code does not yet do it |
| Empty band on some rows | Kept as an empty slot | Labels stay stable, so models always overlay |
| Axis spacing | Ordinal (equal spacing per band), with the exposure strip below | Equal-mass bands become equal-width bars, and the dense middle stays readable |

## 4. One-click controls (one "Bands" menu above the chart)

- **Bands:** Auto · Fewer · More · One per value.
  - Fewer and More halve or double the band count, as exact merges or splits of the fine grid.
  - One per value is offered only when the factor has ≤ 200 distinct values.
  - The current choice shows in the menu's label, for example "Bands: 12 auto".
- **Order** (categoricals): Natural · By exposure · By actual. The last one carries the caption "sorted by the data, so it will look like a trend".
- **Axis** (numerics): Ordinal · To scale · Log. Log appears only when every value is above 0 and the values span more than two decades (sums insured, vehicle value).
- **Merge thin levels:** off by default. When on, adjacent numeric bands (or Other, for categoricals) are pooled until each holds 20 claims, and the bars are marked "merged for display".

These are four concepts in one menu. Each change takes one click and no retraining, since it merges stored sums.

## 5. Deeper controls (the same menu, under "Custom…")

- **Typed edges:** `17, 21, 25, 30, 40, 50, 60, 70`. Each typed edge snaps to the nearest fine-grid edge, and the label then reads "snapped to 24.9". Exact edges anywhere need the kept predictions (plan.md) and are computed on request.
- **Methods:** Equal mass (default) · Equal rows · Equal width. Equal width warns when any band is under the thin threshold.
- **Use a Banding node's bands** (read-only, when the node is upstream).
- **Use a model's bands** (read-only, t-boost's banded stage). These cannot be the default: each model has its own factors and its own bands, and a left-out factor has none. When one model's bands are chosen, the other models are still drawn on them, because they come from the same fine sums. The band edges must fall on fine edges, so the fine grid includes every model band edge at training time.
- **Other cutoff** (N levels, or a minimum mass), the thin threshold in claims, and the width of the range (80 / 90 / 95%).
- The choices are remembered per factor, per node, in the browser.

**Fine grid:** up to 100 equal-mass cells, plus every distinct value when ≤ 200, plus any Banding-node and model band edges; categoricals keep every level up to 1,000. About 8 sums × 100 cells × 100 factors per run.

## 6. How each choice misleads, and the guard

- **Edge artefacts** at round-number clusters (age 25, a 10,000 sum insured). Guard: tied spikes get their own band, and hover shows edges and the values inside.
- **A trend hidden in a wide band** ("17–25"). Guard: tail split, claims / 50 (more bands on more data), One per value.
- **Noise that looks like a curve** from many thin bands. Guard: whiskers always on, hollow thin levels, "By actual" labelled.
- **Simpson-type reversal** (vehicle age looks protective because old cars go to old drivers). A/E partly guards, since expected already holds the other factors. The full guard is ui.md's "Split by", on the same node edges.
- **"Other" hides a mix.** Guard: level count in the label, and hover lists the three largest with their own A/E.
- **Models compared on different rows or edges.** Guard: node edges, the same rows (validation by default), and the existing "Binned on other rows, not drawn" check, which should then fire only after the node's data changes.
- **One large claim makes a level** (severity). Guard: largest-claim share on hover, whiskers from Σw·y².
- **To-scale axis** crushes the middle and lets one outlier set the width. Guard: ordinal default, Log only where it fits.

## 7. Leave out

- **Supervised or "optimal" binning** on the target: it snoops the actuals and flatters the model.
- **Dragging edges on the chart:** the chart is read-only, and typed edges do the job.
- **Per-model or per-run edges:** they break comparison, the chart's purpose.
- **Smoothed lines by default:** they suggest data where there is none.
- **Automatic thin-level merging:** it silently changes what is seen, so it stays a toggle.
- **Jenks or k-means edges:** unfamiliar here, and no better than equal mass with round edges.

## 8. Concerns with the Settled list (smallest change)

- **"A factor's chart shows for every factor, even the ones a model did not fit."** Left-out and fitted factors can only share an axis if the edges are cut per node. The smallest change is for the node to own the edges and for runs to read them. Runs made before the node's data changed should be marked "from earlier data", rather than rebinned.
- **"Feature engineering stays in the pre-processing nodes."** Typed edges look like banding. The caption "Display only", together with a link "Make these the model's bands → Banding node" that copies the edges, keeps the line clear without blocking the workflow.
