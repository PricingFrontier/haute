# Grouping controls in the Factor pane: a progressive-disclosure design

## What the code allows today (checked)

- A bin carries `label`, `exposure`, `avg_actual` and `avg_predicted` (`frontend/src/api/types.ts:379`). It has no edges and no claim count.
- `exposure` is the mass the averages are taken over (`_metrics.py`, `_ave_numeric_bins`). So **merging neighbouring bins in the browser is exact**: sum the masses, then weight the averages by mass.
- A numeric label is the min–max of the values in the bin. Bins have about equal *row counts*, up to 10. A categorical factor keeps its top 15 levels plus Other.
- So on existing runs the UI can only **coarsen, merge and reorder**. Anything finer needs each run to store finer sums: fine bands with their edges, and every level up to a cap. I mark each control **[now]** or **[needs finer sums]**.

## The concept: one word, one home

- Call it **Grouping**. "Bands" already means a t-boost stage and a node type, so here "bands" only ever means the model's bands.
- **One grouping per factor per node.** Every model, every stage series and left-out factors are all drawn on it. A per-model grouping would make `sameLevels` drop series, and that breaks the settled "every trained model, so they can be compared".
- **Its home is the chart's description line.** Today that line reads `numeric · actual vs expected` (`ModelsAveChart.tsx`, `validation-chart-description`). It is always there, so the line *is* the control and costs no screen space.
- **Not in the tab bar:** a settled line fixes that bar's height and typography.
- **Not in a banner:** settled, "must not push the panes down".
- **Not between the heading and the legend:** staged series names make the legend long.
- **Not in the Features row:** its icon opens fitting options, and the Features pane is for selection only.

## 1. Zero clicks: the default

```
 FACTOR  LEARNING CURVE  TABLES  PATHS  EXPLAIN  FIT
 validation rows · actual and predicted outcomes
 veh_age                                  ● Actual  ● t-boost · Full  ● t-boost · Banded
 10 groups, about equal rows · Missing last  ▾
 Average outcome
   |  o  .                                    (chart unchanged)
   |      o--o  .
   +--------------------------------------
   0–2  3–4  5–6  7–8  ...  15–24  Missing
 Exposure  ▂ ▅ ▇ ▆ ...
```

- **The caption is built from the bins actually drawn, not from the setting.** Today it must say "about equal rows", because the server cuts on row count and `np.unique` can leave fewer than 10. Once analytics.md's equal-exposure bands exist, it says "of equal exposure".
- **Default rules, applied by the tool.** These need finer sums unless marked [now].
  - A number with at most 20 distinct values gets one group per value: "Each value (11) · Missing last". ncd_yrs and vehicle group then read naturally.
  - Any other number gets 20 equal-exposure groups, with rounded edges.
  - A category shows its top 15 levels by exposure, in **natural order**: numeric-aware A–Z, so area A–F and vehicle group 1–50 come out in sequence. The rest go in Other. Ordering is [now]: today's order by mass scrambles ordinal codes, and a client-side sort fixes that.
- **Missing and Other are pinned last.** They can never be merged into a real level: Missing must stay visible, and Other is already a merge.

## 2. One click: the popover

A click on the caption opens a small popover anchored under it. It holds no fitting options.

Number factor:

```
+- Grouping · veh_age -------------------------------+
| Groups    ( 5 ) ( 10 )•( 20 ) ( 50 ) ( Each value ) |
| Cut by    (•Equal exposure ) ( Equal width )         |
|           [x] Round edges                            |
| Use       (•Default ) ( t-boost's bands )            |
|----------------------------------------------------- |
| Custom groups…                               Reset   |
| Display only. The model's bands do not change.       |
+------------------------------------------------------+
```

Category factor:

```
| Order     (•Natural ) ( Exposure ) ( Actual )        |
| Show      (•Top 15 ) ( Top 30 ) ( All 48 )  rest in Other |
| Use       (•Default ) ( t-boost's groups )           |
| Custom groups…                               Reset   |
```

- **[now]:** Groups 5 and 10 merge today's bins, and so does any count that divides the stored count. Order works on every option. Top 15 and smaller also work.
- **[needs finer sums]:** 20, 50, Each value, Equal width, Top 30 and All, and the model's bands.
- **An option the run cannot honour** is greyed out, with a tooltip: "Needs finer detail: train again to store it." It is never hidden, so the option stays easy to find.
- **"t-boost's bands"** shows when a model has a Banded stage. Two t-boosts are listed by name. With no banded model the row is absent.
- **Order sits here, not in `More ▾`.** ui.md (proposed) lists "the order of levels" under More. That would put two controls on the axis in two places. More keeps the scale and the split, which change what is drawn, not how the axis is cut.
- A change applies at once; there is no Apply button. Esc or a click outside closes the popover.

## 3. Deeper: custom groups (inside the popover, no dialog)

Number factor, after a click on "Custom groups…":

```
+- Custom groups · veh_age ----------------------------+
| Edges  [ 2, 5, 10, 15                    ]  5 groups  |
|   0–2  18%   2–5  24%   5–10  31%   10–15  17%  15+  10% |
|   ! 5 moved to 4.9, the nearest stored cut            |
| Start from ( Current ) ( t-boost's bands )            |
|                                   [Cancel] [Apply]    |
+-------------------------------------------------------+
```

Category factor: a checklist of the levels. Tick a few, then **Group**. A group is named after its levels ("A–B", or "A, C, F"), and **Ungroup** sits beside it.

- **Typed edges snap** to the nearest stored cut, and the popover says so on a line beginning "!". The share of exposure in each group shows live, so a thin group is visible before Apply. [now] for edges that fall on today's bin boundaries; free edges need finer sums.
- **Apply** is used here, because typing edges one at a time would redraw the chart on every keystroke.
- **No "apply to model" action.** Changing the model's own groups is feature engineering, which stays in the pre-processing nodes (see Concerns).

## How the chart says which grouping it shows

1. **The caption always names the grouping**, from the drawn bins. It is never blank.
2. **A non-default grouping shows at zero clicks.** The caption turns to the accent colour, and Reset sits at its end:

```
 veh_age                                  ● Actual  ● t-boost · Full
 Custom: 5 groups you set · display only · Reset
```

   A model's bands read "t-boost's bands (Banded stage) · display only · Reset".
3. **The same text carries everywhere the grouping matters.**
   - The **hover** detail names the group's range and what it was merged from ("5–10 · merged from 3 groups").
   - The **View bin values** table gets a first line with the caption.
   - The SVG **aria-label** gets the caption.
   - **Copied values** start with the caption.
4. **A custom grouping stays put.** It persists per factor, so when the analyst returns to that factor the custom caption is waiting. The tool never switches a grouping silently.
5. **Rows do not move the axis.** Edges are fixed values, so the axis stays put when the rows change (the V key) and a misfit's movement stays readable. ui.md says "The axis moves only with the factor or the scale". It needs "or the grouping you set" added.
6. **The model's own levels are untouched.** The Tables pane always shows them, whatever the chart's grouping.

## The five most common changes

| Change | Clicks | Today |
|---|---|---|
| Coarser groups (20 to 10, or to 5) | 2: caption, count | [now] when it divides the stored bins |
| Order a category naturally or by exposure | 2: caption, order | [now] |
| See the model's own bands against actual | 2: caption, "t-boost's bands" | needs finer sums |
| Merge thin levels, or set custom edges | 3 and typing: caption, Custom groups…, edits, Apply | [now] for adjacent merges |
| Back to the default | 1: Reset in the caption | [now] |

A finer count (50, or Each value) is also 2 clicks, once the sums exist.

## What it costs

- **Screen space: nothing.** The caption line exists today.
- **New concepts: one, Grouping.** It has three dials (count, cut, order) and Custom behind them.
- **Model settings: none.** Retraining is unaffected, and changing a grouping never puts a model out of date.

## Leave out, and why

- **Dragging edges on the chart.** The factor chart is read-only, and a drag invites confusion with a hand edit.
- **Grouping per model.** It breaks the shared comparison, and series would drop out of the chart.
- **Anything in the Features pane**, even a "custom" mark. The pane is for selection only.
- **A slider for the group count.** Four presets are calmer and give stable, comparable views.
- **A proportional x axis** (spacing by value). The exposure strip already shows mass. A second axis mode doubles what the chart has to explain.
- **"Apply to every factor".** A good default does that job. It can come later as one tick box if users ask.
- **Keys for grouping.** ui.md keeps the set of bare keys small.
- **Formula bins.** That is feature engineering.
- **Thin-group warnings by claim count.** Bins carry no claim count today. Exposure share stands in until they do.

## Concerns (separate from the proposal)

- **Where the grouping persists.** Settled: "The node is only the connection point. It holds no config". So the grouping should live in the Modelling UI's view state, keyed by node and factor, never in a model's settings. The storage design needs to say where that state is saved in the project. Smallest change: name it as view state there.
- **A bridge to pre-processing.** An analyst who likes a custom grouping may want it as the model's bands. The smallest step that keeps "Feature engineering stays in the pre-processing nodes" intact is a **Copy as Banding rules** button in Custom groups. It copies text for pasting into a Banding node and changes nothing. It is for Ralph to decide, so it is not part of the proposal.
- **Server defaults disagree with analytics.md.** The server cuts on row count, not exposure, and makes 10 bins, not 20. The caption above stays honest either way, but the default experience shown in section 1 depends on that being fixed, along with storing finer sums.
