# Critique (Fable): x-axis grouping on the factor chart

Code claims below were checked in `/home/ralph/suite/haute`.

## 1. My own take

**The design in one line:** one display grouping per factor per node, shared by every model, cut by a type-aware default that is right for most factors, with one small control on the chart's caption line and one popover behind it.

**Default (no clicks).** The tool picks by what the column looks like.

| Column | Default |
|---|---|
| Number, up to about 30 distinct values | One bar per value |
| Other number | Equal-exposure bands, round edges, count as analytics.md already states (20, or claims / 50 if fewer, at least 5) |
| Category whose levels sort naturally (A–F, 1–50, an Enum) | **Every** level, in natural order, up to about 60 |
| Other category | Top 25 by exposure, then "Other (n levels)" |
| Missing | Always its own bar, last |

**One click.** The caption line that exists today (`ModelsAveChart.tsx:74`) becomes `20 groups  −  +  ▾`. The `−` and `+` halve and double the count (or step the top N). Finer is the change the analyst makes most, so it gets the one-click slot. Cost: no new screen space, one concept ("groups").

**Two clicks (the popover under ▾).** Four things only: Each value, Order (natural / exposure / actual over expected), Edges (a typed list), Reset. A non-default caption changes colour and says "display only".

**Leave out of the first build:** cut methods, axis scales, thin-level merging, hand-merging nominal levels, tail caps, ordinal steps, named schemes. Typed edges already cover caps and steps.

**Foundation:** arch's fine sums per slice, merged in the browser. Every other report depends on it.

### Ideas the team missed

1. **Split one bar, not the whole axis.** The analyst's real need is local: "17–25 is one bar, open it up." All four reports answer with a global change (more bands everywhere) or a typed edge list. Put **Split** and **Merge with next** in the bar's hover detail. Split divides that one group at its exposure midpoint, on slice edges, so it is exact. Two clicks turn "17–25" into "17–20, 21–25" with nothing typed. This is not dragging and edits nothing, so the read-only rule holds. It also makes stats' tail-split heuristic unnecessary.
2. **Seed the fine grid with round numbers.** Three reports snap a typed cut to the nearest slice and then apologise ("5 moved to 4.9", "Cut 30 → 29.6", a "Make exact" button). Instead, build the slices for a continuous factor from the equal-exposure quantiles **plus** the 1-2-5 round values in range. Then the edges people actually type (5,000, 10,000, 30) are slice boundaries, the answer is exact, and the snapping note almost never shows.
3. **"Use for all number factors".** UI rejects this on the ground that a good default does the job. It does not: an analyst who prefers 10 bands will otherwise say so 100 times on the first pass. One line in the popover.

## 2. Challenge to the team

### Over-built

- **Stats is the worst offender.** Its "one menu, four concepts" is in fact: band count, order, three axis scales, a merge-thin toggle, three cut methods, two borrowed band sources, an Other cutoff, a thin threshold and a range width. Whiskers, hollow markers and largest-claim share are about uncertainty, not grouping. That is a separate decision. The tail-split rule (4× median width, 40 claims a half) and the ±25% rounding rule are heuristics an analyst cannot predict, so the default would feel random.
- **UI's popover has too many rows.** "Cut by equal exposure / equal width" is a choice nobody asked for. "Round edges" is a tick box for something that should simply be true. "Start from" is a third way to get to the same place.
- **UI's [now] / [needs finer sums] tier, with greyed-out options.** This builds a second, worse product for old runs. Settled says runs are local and training again overwrites the run. Show old runs as they are, with no control, and move on.
- **Analyst's deeper list** (tail caps, ordinal step, sentinel list, top N to 50). Typed edges do the first two. A heavy value getting its own slice (arch's rule) shows a sentinel spike as its own bar with no setting at all.
- **Arch's six-kind `AxisGrouping`, the `right_closed` flag and the three-layer store.** Pick one closing rule and drop the flag. The layered store with digests is step 3 for a reason: the worst case measured is about 1 MB a snapshot, so build it only when that hurts. Hand-merging nominal levels (`levels` rules) is ranked last by the analyst and called feature engineering. Cut it.

### Under-built

- **Ordered categories with more than 15 levels.** UI's "natural order, [now], a client-side sort" and arch's quick fix 5 both sort only the top 15 by mass. Vehicle group 1–50 then shows 15 groups with gaps and an "Other" made of scattered groups. That reads as a trend and is not one. Ordered categories must show every level.
- **Dates.** Only the analyst covers them. Today a date column is refused outright (`_training_job.py`, `_ave_left_out`: "a {dtype} column is not charted") and the split's date column is excluded. Calendar buckets are one more grid kind in arch's kernel, and should be planned now even if built later.
- **Order by actual over expected.** The analyst asks for it ("shows me where the model is wrong"). UI and stats offer only "by actual". It needs one rule: use the chosen model's expected.
- **The first pass.** 60–150 factors at a few seconds each. Nobody offered a way to change the count without leaving the keyboard. Two keys for fewer and more would fit the arrow-key walk. ui.md's "no key changes a setting" is not broken, because a grouping is a view, like `V`.

### Disagreements, and who is right

| Question | Positions | Verdict |
|---|---|---|
| Default band count | 10 (analyst, arch), 20 (UI), adaptive (stats) | **Stats.** It is what analytics.md already says, and a one-click `−` makes it cheap to be wrong |
| The word | Grouping (UI), Groups (analyst, arch), Bands (stats) | **UI.** "Bands" already means a t-boost stage and a node |
| Where it sits | Caption line (UI), header chip (analyst, arch), a menu (stats) | **UI**, with the analyst's stepper added. The popover alone makes the commonest change two clicks |
| Order | In the grouping popover (UI) or in More (ui.md) | **UI.** One place for everything about the axis |
| To-scale and log axes | In (stats), out (UI, analyst) | **Out.** The exposure strip does the job |
| Interval closing | `[lo, hi)` (stats), right-closed (arch) | **Arch.** Banding (`_rating.py:313`, `right_closed=True`) and t-boost are right-closed |
| Top levels | 15 (analyst, UI), 25 (stats) | **Stats**, for nominal factors only |
| Where it is saved | Browser first (arch, stats), project (UI) | **Arch.** `useStagedViews` is a persisted zustand store already. Move it later if sharing matters |
| Model's own bands as an axis | In (UI, stats, arch), unranked (analyst) | Keep, but in the second build. `_report_axis` (`_tboost.py:91`) makes it cheap |

### Claims about the code

- **Wrong (UI, and the brief): "merging in the browser is exact" today.** The run stores averages rounded to 6 places and exposure to 4 (`_metrics.py`, both bin functions), not sums. A merge is close, not exact. There are also no edges, so a merged label means parsing strings like "1.072e+05–1.263e+05". Arch and stats have this right.
- **Wrong (analyst): "Haute does this already" for a categorical Missing at the end.** Null becomes `__MISSING__`, is ranked by mass with every other level (`np.lexsort((labels, -mass))`), and can fall into Other. Stats is right.
- **Wrong today (UI): "edges are fixed values, so the axis stays put when the rows change."** Edges are cut from each run's own `diag_df` (`_training_job.py:3221`). It becomes true only with a node-owned grid.
- **Caveat (stats, arch): Enum order.** The categorical path casts to `Utf8` first, so the Enum's order is gone before any sort. It needs a server change, not a client sort.
- **Confirmed (arch): `chart_left_out` is in the fit digest.** It is in `_JOB_FIELDS` (`_staged_runtime.py:69`), `_kwargs` copies every field, and `fit_config_digest` drops only `stages`. It is harmless today because it is always set from `interactive_build`, but it proves the warning: a grouping must never be a job field.
- **Confirmed (stats, arch): a rare 0/1 flag becomes one bar "0–1"**, and a value on an edge goes to the upper bin (`np.digitize` without `right=True`).

## What I would build, in order

1. **Arch's six quick fixes**, plus "every level for ordered categories" and "Missing last for categories". No new control. This fixes most of what the analyst called wrong.
2. **Fine sums inline, the caption stepper, Order and Each value.**
3. **Typed edges with the round-number grid, and Split on a bar.**
4. Model bands, dates, the shared store.
