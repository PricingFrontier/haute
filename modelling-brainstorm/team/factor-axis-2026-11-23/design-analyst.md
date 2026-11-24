# How a pricing actuary works the one-way x-axis

Written as a senior UK personal lines pricing actuary (motor and home, frequency and severity).
The code claims were checked in `src/haute/modelling/_metrics.py:459-670`.

## How I actually work

**First pass, every factor, every model (about 60-150 factors).** I step through them with the
arrow keys and spend 3-10 seconds on each. I am looking at four things. Do actual and expected
track? Is there a slope the model has missed? Is there a tail where the two split apart? Is any bar
too thin to trust? I don't regroup on this pass, so the default must be right for about 80% of
factors.

**Deep dive, 10-20 factors per model.** These are the ones that look wrong or matter commercially:
driver age, NCD, vehicle group, vehicle value, area or postcode, occupation, mileage. I regroup most
of these: perhaps 1-3 changes on each, so 20-40 grouping changes per model build. I go finer far
more often than coarser. Equal-exposure bands hide the tails, and the tails are where the money and
the mispricing are: ages 17-21, 80 and over, high-value vehicles, vehicle groups 45-50.

**Weekly or at sign-off.** I set up the time split: by accident year or by quarter. I export a
values table for the pricing committee pack.

**Never.** I never drag points. I never want a display grouping to change the model quietly. I don't
pick colours.

## By factor type

**Driver age (numeric, integer).** By default I want one bar per year from 17 to about 25, then
5-year bands, then a fat 80+. Ten equal-exposure bands put 17-24 into one or two bars. That one bar
hides the most important shape on the motor book. My most used control here is a band width I can
type (1, 2, 5 years). Next is "cap the top at 80", so the long tail goes into one bar.

**NCD years (numeric, discrete, 0-9+).** This is one bar per value, always. Today it is quantile-cut
on raw rows (`np.percentile`, unweighted), and `np.unique` drops tied cut points. Most of the book
sits at the maximum NCD, so values get merged into bands like "0–3" or "4–8". That is wrong, and I
would notice in the first second. **Rule: a numeric with 20 or fewer distinct values gets one bar
per value by default.** That also covers vehicle group stored as a number, number of drivers and
convictions.

**Vehicle value and mileage (numeric, skewed).** Equal-exposure bands are fine as a starting point.
But the edges must be rounded to numbers I would say aloud (5,000 and 10,000, not 4,873.2). Labels
are given to 4 significant figures today, and they are lo–hi of the observed values, not the band
edges. Mileage has spikes at declared round values (5k, 8k, 10k, 12k). They should be visible, not
hidden in a band. Controls: the number of bands (5 / 10 / 20), and "round edges" on by default.

**Postcode density (numeric, continuous).** Ten equal-exposure bands is right. At most I try 20.

**Vehicle group 1-50 and area A-F (ordinal).** Natural order is the default, not a setting. Today
both are sorted by mass descending (`np.lexsort((labels, -mass))`). That scrambles the order, so a
slope can't be read at all. This is the single worst default in the current chart. Vehicle group
also wants an optional "group in fives" (1-5, 6-10, ...), because the thin top groups are noisy.

**Occupation, about 500 levels, and vehicle make (nominal).** Show the top N by exposure, with
"Other" as a visible bar with its exposure share in the hover. N is 15 today. I want to step to
30 and 50. Sorting matters more than anything else here. By exposure is the default. By actual over
expected shows me where the model is wrong. By actual shows the risk ranking. Search is essential:
I need to find "Taxi driver" or "Student" in one move. The "Other" bar has to be honest. If it
holds 40% of exposure, that is itself a finding.

**Dates and periods (inception or accident date).** Calendar buckets in time order: month, quarter
or year, chosen in one click. Never quantiles of a date. The latest period must be marked as not
yet mature (IBNR, late-reported claims), or I'll misread a falling frequency. Haute has nothing
for dates here yet. ui.md lists "split by period" under More, and that is the right home.

**Missing values.** Their own bar, at the end, always shown even when small, with its exposure.
Haute does this already for numeric NaN and categorical null. What tools miss: sentinel codes
(0, -1, 9999, 99) sitting inside a numeric band. Vehicle value 0 or age 99 skews the edge band
silently. I want a "treat as missing" list for a factor's display. A flag on any single value holding a big share
of exposure would catch most of them.

## What frustrates me in desktop pricing tools

- Regrouping means a dialog away from the chart and a recalculation, and the grouping is forgotten
  next visit.
- I can't tell whether a grouping is for display or goes into the model. I have shipped a band
  I only meant for viewing.
- Models compared on different bands can't be overlaid. Haute shows "Binned on other rows, not
  drawn" today. One grouping per factor, shared by every model, removes that.
- Labels nobody can read aloud ("17.00–23.47"), and "1, 10, 11, 2" sort orders.
- No exposure on the chart, so a dramatic bar turns out to be 30 policies.

## Ranked controls

| Rank | Control | How often | Haute today |
|---|---|---|---|
| 1 | Correct defaults: natural order for ordinals, one bar per value for discrete numerics, equal-exposure bands with round edges for continuous ones | Every factor, never touched | Mass order for categoricals; unweighted quantiles for numerics |
| 2 | Sort levels: natural / exposure / actual over expected / actual | Every nominal I dive into | Listed in ui.md More ("natural, by exposure or by actual") |
| 3 | Number of bands, or top N: a step of 5 / 10 / 20 / 50 | Most deep-dive factors | No; n_bins=10, max_categories=15 fixed |
| 4 | Custom cut points typed as a list ("17,18,19,20,21,25,30,40,50,60,70,80") | 3-6 factors per model | No |
| 5 | Search levels on a high-cardinality factor | Every nominal dive | Listed in ui.md |
| 6 | Calendar period: month / quarter / year | Weekly | No |
| 7 | Cap the tails: "below x" and "x and over" | A few factors | No |
| 8 | Group consecutive ordinal levels in steps (fives) | Vehicle group, rarely others | No |
| 9 | Treat values as missing (sentinels) | Rare, but it saves an embarrassment | No |
| 10 | Merge chosen nominal levels by hand for display | Rare; mostly a feature-engineering job | No |

## The four buckets

**1. Default (no clicks).** Every point in rank 1, plus a "Missing" bar at the end, an exposure
strip, and "Other" shown with its share. The grouping is shared by every model drawn, and it is
remembered per factor per node.

**2. One click (a compact control in the chart header).** One "Groups" control, which reads for
example "10 bands" or "Top 15". It is a stepper with fewer and more. Beside it is a sort toggle.
These are the only two controls that need to be always visible.

**3. Deeper (inside More, a small popover).** Cut points typed as a list, tail caps, ordinal
step, calendar period, sentinel values and a reset to default. The chart header shows a small
"custom" mark when any of these is set, so I always know I'm not looking at the default.

**4. Leave out.**
- Dragging band edges on the chart: fiddly, and the chart is read-only by design.
- Log or linear x-axis choices: bars are evenly spaced, so they mean nothing.
- Per-model groupings: they break the overlay.
- Saving named grouping schemes: overkill for a display aid.

## Concerns (kept apart from the proposals)

- Regrouping needs finer data than the stored 10 bins. Merging stored sums is exact. **Splitting
  is not.** Ranks 3, 4 and 7 need either finer stored bins (for example 100 equal-exposure bins
  plus one per distinct value up to about 200) that the UI merges, or a recompute from the kept
  predictions (plan.md).
- analytics.md says numeric bands are of equal exposure. The code uses equal row count. They
  should be brought into line.
- "Use these bands in the model" will be asked for. The smallest change is a "copy as banding rules" action that hands the cut points to a Banding node
  in pre-processing (`src/haute/_banding_config.py`). The model is never changed from the chart.
- Nothing here goes in the Features pane. All of it sits on the chart.

## The 90% set

1. **Right defaults by type**: natural order for ordinals, one bar per value up to 20 distinct
   values, equal-exposure bands with round edges otherwise, and Missing and Other visible.
2. **A Groups stepper** (fewer or more bands, or top N).
3. **A sort toggle** (natural, exposure, actual over expected).
4. **Typed cut points** in More.
5. **A period picker** for dates in More.

Search, which is already planned, completes it. Everything else is in the last 10%.
