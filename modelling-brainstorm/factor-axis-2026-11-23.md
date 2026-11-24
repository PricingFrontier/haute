# The factor chart's x-axis: how it is grouped, and the controls to offer

The editor's merge of four team reports (analyst, UI, stats, architecture) and two outside critiques (Claude Fable, OpenAI Codex). I checked every code claim kept here against `/home/ralph/suite/haute`.

## 1. Recommendation in brief

- **One grouping per factor per node, shared by every model**, stage and left-out chart. It is for display only: the model's bands never change.
- **Defaults that are right with no clicks:**
  - a number with up to 20 values gets one bar per value;
  - other numbers get equal-exposure bands with round edges;
  - numbered categories come in their own order;
  - Missing is the last bar.
- **The chart's description line becomes the control**, `20 groups  −  +  ▾`. One click gives fewer or more, and two clicks give anything else. It needs no new screen space.
- **One small popover:** counts, Each value, Order, Custom cuts…, Reset.
- **The second build adds local refinement.** A bar's hover detail gets Split and Merge. In categories, search pins a level.
- **The foundation is unrounded sums on fine slices**, cut once per node and stored with each run. The browser merges them, exactly, for any grouping built from whole slices.
- **Seven small server fixes first.** They remove the worst defaults before any new control is built.
- **Left out:** dragging edges, a grouping per model, log or to-scale axes, a choice of cut methods, and merging thin levels automatically.

## 2. How it works today

- **Bins are cut on the server, once per run** (`compute_ave_per_feature`, `_metrics.py:459`, called at `_training_job.py:3221`), from that run's own diagnostics rows, so the edges move with the rows. The browser cannot regroup.
- **Numbers:** up to 10 bins, with edges from `np.percentile` over raw rows.
- **Categories:** the top 15 by mass, largest first, then "Other".
- **Each bin stores** a label, its exposure (rounded to 4 places) and two averages (rounded to 6 places). It has no sums and no edges.

The faults below were reproduced on 10,000 synthetic rows.

| Fault | What happens |
|---|---|
| Quantiles are unweighted | They cut on row count. analytics.md says "of equal exposure" |
| Tied edges collapse | `np.unique` drops repeated edges. NCD 0–9 with 45% of rows at 9 asked for 10 bins and got 6. "8–9" held 5,140 rows. A 0/1 flag becomes one bar, "0–1" |
| Small-integer factors show as ranges | NCD and vehicle power show "1–2" and "4–5", not one bar per value |
| Tails are lumped | Driver age gives "17–25" and "64–99" |
| Categories are ordered by exposure | Area comes out C, D, E, B, A, F. Vehicle group 1–50 is scrambled |
| Bins are left-closed | `np.digitize` without `right=True`. Banding (`_rating.py:313`) and t-boost are right-closed |
| Missing is ranked like a level | A null becomes `__MISSING__`, which can fall into Other or collide with a real level |
| Enum order is lost | The category path casts to `Utf8` first |
| Labels are hard to read | Observed min–max to 4 significant figures, for example "1.072e+05–1.263e+05" |
| Dates are not charted | Refused: "a {dtype} column is not charted" (`_training_job.py:3539`) |
| Merging is not exact | The bins hold rounded averages |

**The overlay check.** `ModelsAveChart.tsx` overlays a model only if its labels, exposures and evaluation identity all match the first series. Any other model is listed as "Binned on other rows, not drawn".

## 3. The default experience (zero clicks)

| Factor | Default | Demo example |
|---|---|---|
| Number, ≤ 20 distinct values (flags too) | One bar per value | VehPower: 12 bars |
| Other number | Equal exposure: 20 bands, or claims / 50 if fewer, at least 5 (analytics.md). Claims are counted on the node's validation rows, so a band holds about 50 charted claims. Ties stay whole. Edges are round | DrivAge (83 values), Density (1,607): 20 groups such as "1k–2k" |
| Category of numbers, or a Polars Enum | Every level, in order, up to 60. Beyond 60, neighbouring levels are joined into 20 equal-exposure groups, as for a number | Group 1–5. Vehicle group 1–50 |
| Other category, ≤ 15 levels | Every level, in A–Z order with numbers sorted by value | Area A–F. VehBrand B1, B2 … B14 (not B1, B10, B11) |
| Other category, > 15 levels | Top 15 by exposure on the node's non-test rows, then "Other (6 levels)" | Region (21) |
| Missing | Its own bar, always last, never in Other | |

**What every chart also shows:**

- **A caption naming what is drawn**, taken from the bins: "20 groups · equal exposure", "Each value (12)" or "6 levels · A–Z".
- **The exposure strip**, as today.
- **Readable labels, closed on the right** as in Banding and t-boost: "26–30" for (25, 30], "£10k–15k", and "81+" for the last band.
- **The exact rule on hover**, for example "(25, 30]".
- **Empty groups keep their place**, so every series has the same groups.

The analyst's first pass is 60–150 factors at a few seconds each; these defaults should need no touching on about 80% of them.

## 4. Controls in layers

### One click, on the caption line

- **`−` / `+`.** For numbers it steps the count 5, 10, 20, 50, then Each value. For categories it steps Top 15, 30, 50 (the first cut ships up to 50), then All (second build, with search).
  - Default: Auto.
  - For: the analyst (a stepper) and Fable (finer is the commonest change). UI had put every change behind the popover.
- **Reset.** Shown only off the default, when the caption also turns the accent colour: "Custom: 7 groups · display only · Reset". Everyone agreed.

### Two clicks, in the popover under `▾`

- **Groups:** 5 · 10 · 20 · 50 · Each value. Presets apply at once. Each value appears only for numbers with ≤ 200 values.
  - For: all. Stats' "Fewer / More" is folded into the stepper. Each value was asked for by the analyst, stats, arch and Fable.
- **Order** (categories): Natural · Exposure · Actual over expected. Actual over expected uses the chosen model's expected.
  - For: the analyst and Fable. UI and stats had "by actual" only.
  - Codex: an inferred order needs an override, and this is it.
  - It moves here from ui.md's More, so the axis has one home (UI and Fable).
  - It follows the chosen model, so the caption names it: "by actual over expected (t-boost)".
- **t-boost's bands**, by model name; every model is drawn on them, offered only where exact (section 6). For: UI, stats, arch. Codex: exact or absent. Second build.
- **Search** (categories with hidden levels) pins a level beside the top levels, taking its sums out of Other. Codex's idea; the analyst ranks search 5th. Second build.
- **Custom cuts…** and **Reset.**

### Deeper (second build)

- **Custom cuts** (numbers): an edge list such as `17, 21, 25, 30, 40, 50, 60, 70, 80`, read as Banding reads it, closed on the right: 17, 18–21, 22–25 … 81+.
  - For: analyst (ranked 4th), UI, stats, arch and Fable. Codex: only if exact, or asked.
  - A Fill line writes the list: "every [5] up to [80]". One line covers the analyst's band width, tail caps and "group in fives", and Codex's equal width, with no new concept.
  - Exposure shares show live, and the edits need Apply.
  - A cut inside a slice asks first: "25 is inside a slice: use 24.9 or 25.3?" Nothing moves silently (Codex).
- **Split / Merge with next,** in a bar's hover detail (Fable). Split cuts that one group at its exposure midpoint, on slice edges. Both are exact and edit nothing, so the chart stays read-only. Codex's "Refine this group…" is the same need.
- **Copy as Banding rules,** in Custom cuts. It copies the edges as text to paste into a Banding node, and changes nothing. All four team reports raised it. It is the one bridge to feature engineering.

### Left out

| Left out | Why | Who |
|---|---|---|
| Dragging edges | The chart is read-only, and a drag looks like a hand edit | All |
| A grouping per model | It breaks the overlay | All |
| Log and to-scale axes | The exposure strip shows mass. A second axis mode adds explaining | Stats for. Others against |
| A choice of cut methods | Unasked for. Fill covers width | Stats and UI for. Fable and Codex against |
| Auto-merging thin levels, a thin threshold, a range width | Uncertainty is its own decision | Stats for. Fable and Codex against |
| Adaptive tail-split and rounding rules | The analyst cannot predict them. Split does the job | Stats for. Fable and Codex against |
| Lists of sentinel values | The tool cannot know that 99 means missing. A heavy value is its own slice, so it shows as a spike | Analyst for. Codex and Fable against |
| Merging categories by hand | Feature engineering. Pinning covers minorities | Analyst ranks it last. Fable cuts it |
| Greyed options for old runs | They make a second, worse product | UI for. Fable and Codex against |
| Slider, named schemes, keys, supervised or Jenks bins | Noisy, overkill, or they snoop the actuals | All |

## 5. Wireframe

```
 FACTOR  LEARNING CURVE  TABLES  PATHS  EXPLAIN  FIT
 DrivAge                       ● Actual  ● t-boost · Full  ● t-boost · Banded
 20 groups · equal exposure   −  +  ▾                <- caption is the control
 Average outcome
   |  o                                    +-----------------------------+
   |   o .                                 | 17–21   ≤ 21                |
   |     o--o--o--o--.--o--o--o--o--o      | exposure 4.1%  claims 612   |
   |                                  o    | actual 0.142  t-boost 0.118 |
   +-------------------------------------- | [Split]  [Merge with next]  |
    17–21 22–25 26–30 ... 76–80 81+ Missing +-----------------------------+
 Exposure ▂ ▃ ▅ ▇ ▇ ▆ ... ▂ ▁ ▁

 ▾ opens:
 +- Grouping · DrivAge ------------------------------+
 | Groups  ( 5 ) ( 10 ) (•20 ) ( 50 ) ( Each value )  |
 | Use     (•Default ) ( t-boost's bands )            |
 | Custom cuts…                               Reset   |
 | Display only. The model's bands do not change.     |
 +----------------------------------------------------+

 Custom cuts… opens:
 +- Custom cuts · DrivAge ---------------------------+
 | Fill   every [ 5 ] up to [ 80 ]                    |
 | Edges  [ 17, 21, 25, 30, 40, 50, 60, 70, 80 ]      |
 |   17 1%   18–21 4%   22–25 6%   ...   81+ 2%       |
 | Copy as Banding rules             [Cancel] [Apply] |
 +----------------------------------------------------+
```

The popover differs for a category:

- **Show** (•Top 15) (30) (50) (All 21) replaces Groups.
- An **Order** row and a search box are added.
- There is no **Custom cuts…**.

## 6. Data foundation

### The node grid

The grid is cut once per factor, from the node's non-test rows. It uses each factor's values and mass only.

- **Numbers with ≤ 200 values:** one slice per value. That covers driver age, NCD, vehicle group and flags.
- **Other numbers:** about 100 equal-exposure slices, plus the round 1-2-5 values in range (Fable's idea), so typed cuts are usually exact. Ties stay whole.
- **Categories:** every level up to 1,000 by exposure, then "Other (n levels)".
- **Missing:** a typed slice, not a string.
- **A digest:** the grid is a deterministic function of the source and the split plan, and carries a digest.

### What each run stores, per slice

Unrounded Σmass, Σw, row count, Σw·y, Σw·ŷ, claim count, Σw·y², and the observed min and max. The claim count and Σw·y² let ranges and thin-level marks come later without retraining.

### Sizes

These are arch's measurements, on 135k demo rows with 10 factors.

| What is stored | Size |
|---|---|
| Today's results | 7.7 kB |
| Slices stored whole in each run | 29 kB |
| Worst case, 100 factors × 100 slices | About 1.1 MB a snapshot |
| Worst case, in shared layers | 0.9 MB once, then 172 kB per run or stage |

The build took 0.25 s. The default payload carries up to 50 levels a category. The rest are fetched when the analyst searches.

### When a grouping is exact

- **Always exact:** a factor with one slice per value, and any grouping made of whole slices.
- **Not exact until Kept predictions (plan.md) can recompute on request:**
  - a typed cut inside a slice;
  - a category level pooled beyond the 1,000-level cap.
- The UI says so in both cases.

### t-boost's bands

- **Numeric bands** are exact on factors with one slice per value. On continuous factors they are offered only where every cut falls on a slice edge, or once Kept predictions exist. The reason: a later model cannot split slices that earlier runs already summed (Codex).
- **Category groups** need a structured export of their members. `_report_axis` (`_tboost.py:91`) emits comma-joined labels today.

### Comparing models

Series overlay when they share the grid digest and the evaluation identity; merged with one grouping, their groups match by construction. This replaces the label-and-exposure test and keeps the identity test.

### Persistence

- **Where a grouping is saved:** per node and factor in a persisted view store, like `useStagedViews` (zustand `persist`, held in the browser). It is saved with its grid digest. After the data changes, presets carry over, and custom cuts say "set on earlier data".
- **Never in the job:** it is never a job field and never part of the training identity.
- **A correction:** `chart_left_out` is a job field but does not change the digest. `training_identity_sha256` uses an allowlist that leaves it out. Codex is right, and arch and Fable are wrong.

### Readers today

MLflow, the model card, `AveTab`, `PathCandidateSummary` and `ModellingPreview` read `ave_per_feature`. A Python `group_slices` derives it from the slices, so they stay unchanged; Python and TypeScript share one test fixture.

## 7. Build order

### Quick fixes now (no change to the data shape)

1. **One bar per value** for numbers with ≤ 20 distinct values. This fixes NCD and the "0–1" flag. `np.unique` already drops tied edges, so the fix is a path for distinct values, not more de-duplication.
2. **Cut on cumulative mass**, so bands are of equal exposure.
3. **Right-closed bins** (`right=True`).
4. **Numeric `lo` and `hi`** on each bin, with the labels formatted in the client.
5. **Categories:**
   - Missing last, never in Other.
   - Numbered and Enum levels shown in order, all of them up to 60.
   - A–Z order for other categories with ≤ 15 levels, which fixes Area.
   - The Enum's order read before the `Utf8` cast.
6. **Round to significant figures**, not decimals.
7. **A caption that names the grouping drawn.**

These change labels, so kept reference models trained before them show as "Binned on other rows" until trained again.

### First useful cut

- the node grid with its digest, built inline in each run, and the slice sums;
- `groupSlices` in the browser;
- the caption control, with counts, Each value, Order and Reset;
- overlay by digest and evaluation identity;
- the view store;
- analytics.md's band count.

Shared edges come first, as Codex argues. Arch's layered store, which removes duplicates, can wait: whole slices cost at most about 1 MB a snapshot.

### Second

- Custom cuts, with Fill and explicit snapping.
- Split and Merge.
- Search and pin.
- t-boost's bands, where they are exact.
- Copy as Banding rules.

### Later

- The layered store, once payloads hurt.
- Calendar buckets for dates: month, quarter or year, with the latest period marked as immature.
- Ranges and thin-level marks.
- Kept predictions: exact cuts anywhere, and splits by a second factor.
- "Use this count for every number factor", if asked for.

## 8. Where the reviewers disagreed

| Question | Positions | Editor's call |
|---|---|---|
| Default band count | 10 (analyst, arch). 20 (UI, Codex). Adaptive (stats, Fable) | analytics.md's rule. `−` makes it cheap to be wrong |
| One bar per value, up to | 20 (analyst, UI, arch, Codex). 30 (stats, Fable) | 20. Driver age would otherwise start with too many bars |
| Top N levels | 15 (analyst, UI, Codex). 25 (stats, Fable) | 15. Ordered categories now show every level, which removes Fable's vehicle-group case |
| Category order | Natural throughout (UI, arch). Ordinal factors only (analyst, stats). Codex: A–Z order does not prove the levels are ordered | Natural when every level is shown, and the caption says "A–Z". By exposure when levels are cut. Order overrides both |
| Shared grid, first or later | Later (arch). Foundational (Codex) | First. Node edges are about correctness. Removing duplicates is only about size |
| Snapping cuts | Snap with a note (UI, stats, arch). Exact, or ask (Codex). A round-number grid (Fable) | Fable's grid, plus Codex's question |
| Category cap | 500 (arch). 1,000 (stats). Codex: cap the response, not what is kept | Keep 1,000 levels, send the top ones by default, and state the limit |
| Local refinement | Absent (team). Split and Merge (Fable). Refine… (Codex) | Fable's, in the second build |
| Uncertainty | Part of this design (stats). Separate (Fable, Codex) | Separate. Sums stored now; exposure never passed off as credibility |
| Keys for fewer and more | For (Fable). Against (UI) | Asked below |
| Interval closing | `[lo, hi)` (stats). Right-closed (arch, Fable) | Right-closed, as in Banding and t-boost, so Copy as Banding rules and t-boost's bands match exactly |
| Code claims | Merging exact today (brief, UI). Category Missing handled (analyst). Both corrected by Fable and Codex | Codex and Fable are right |
| More code claims | `chart_left_out` in the digest (arch, Fable). The overlay checks labels and exposure only (arch). Dropping tied edges as a fix (arch) | Codex is right on all three (section 2) |
| Why grouping stays out of Features | "Selection only" (brief, UI). Codex: that is stale | Codex is right about the wording. The real reasons: grouping is shared across models while those rows belong to one model, and the pane shows no outputs |

## 9. Questions for Ralph

1. One bar per value for numbers with ≤ 20 distinct values? [Yes, 20]
2. Should analytics.md's band count be the default? [Yes]
3. How many category levels before Other? [15, with `+` to 30, 50 and All]
4. Where is a grouping saved? [The browser's view store. `modelling.json` only if it must travel]
5. Should there be "Copy as Banding rules", copying text only? [Yes]
6. Should keys step fewer and more? [Not in the first cut]
7. Should there be "Use this count for every number factor"? [Later, if asked for]
8. Should categories use A–Z order when every level shows, named in the caption? [Yes]
9. Calendar buckets for dates? [Planned now, built after the first cut]
10. Should Kept predictions come earlier, so that every cut is exact? [No. Ask before snapping until then]
11. Should old reference models show as they are, not overlaid, until trained again? [Yes]

## 10. Draft Proposed items for overview.md

*The factor axis*

62. One grouping per factor per node, shared by every model, stage and left-out chart. Display only: the model's bands do not change. *(ui.md, Charts)*
63. A default by kind of factor, with no setting: one bar per value up to 20 values, otherwise equal-exposure bands with round edges; numbered categories in order, others top 15 then Other; Missing last. *(analytics.md)*
64. The chart's description line names the grouping drawn and is its control: `−` and `+` step the count, and `▾` opens a popover. A grouping that is not the default shows in the accent colour, with Reset. *(ui.md, Charts)*
65. The popover holds counts, Each value, Order, t-boost's bands, Custom cuts… and Reset. The order of levels moves here from More. *(ui.md, Charts)*
66. Custom cuts: an edge list, a Fill line, live exposure shares and Apply. A cut inside a slice asks before it moves. *(ui.md, Charts)*
67. Split and Merge with next in a bar's hover detail. Exact, and they edit nothing. *(ui.md, Charts)*
68. Search pins a category level beside the top levels and takes it out of Other. *(ui.md, Charts)*
69. Each run stores unrounded sums per slice of a node grid. The browser merges them, exactly for whole slices. `ave_per_feature` is derived from them. *(analytics.md; plan.md)*
70. The node grid is cut from the non-test rows by value and mass: one slice per value up to 200 values, else about 100 equal-exposure slices plus round values; categories up to 1,000 levels. It carries a digest. *(analytics.md)*
71. Series overlay when they share the grid digest and the evaluation identity. *(ui.md, Charts)*
72. A grouping is view state, saved per node and factor. It is never a job field and never part of the training identity. *(ui.md, Where each thing lives)*
73. A model's bands are offered as a grouping only where they are exact. *(ui.md, Charts)*
74. Copy as Banding rules is the one bridge from the chart to pre-processing. *(Hand edits)*
75. "The axis moves only with the factor, the scale or the grouping you set." An order by actual over expected follows the chosen model. *(ui.md, Charts)*
76. Left out: dragging edges, a grouping per model, log and to-scale axes, a choice of cut methods, automatic merging of thin levels, lists of sentinel values, and merging levels by hand. *(ui.md, Left out)*

## 11. Concerns about settled lines

- **I. "The node is only the connection point. It holds no config."** A grouping that has to travel with the project would sit in `modelling.json`, the node's config. Smallest change: none while groupings stay in the browser. If they must travel, add "…apart from the Modelling UI's saved views."
- **J. "The actual-against-expected charts show every trained model…"** A model trained on other rows is still named and not drawn. Smallest change: "…every trained model on the same rows; one on other rows is named."
- **K. "Feature engineering stays in the pre-processing nodes."** Typed cuts look like banding. No change is needed while Copy as Banding rules only copies text.
