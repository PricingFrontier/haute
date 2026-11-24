**My independent take**

Build around two analyst questions: “What shape am I missing?” and “What is hidden inside this group?” A catalogue of binning algorithms is a poor substitute for answering those questions quickly.

The default should need no configuration:

- Numeric factors with at most 20 distinct values: one point per value, ordered numerically.
- Other numeric factors: 20 approximately equal-exposure groups, using fixed boundaries across compared models and row views. Keep tied values together; show the actual group count.
- Categories: top 15 by exposure, with Other showing its exposure share. Respect explicit ordinal metadata; otherwise default to exposure order and offer natural order. Alphabetical order does not establish that a factor is ordinal.
- Missing stays separate and last. Empty groups retain their position but have no fabricated rate.
- Exposure remains visible below the chart. Tooltips show exact interval boundaries and inclusion rules.

Twenty groups is a reasonable starting point, consistent with the brainstorm’s default; it is not a statistical guarantee. Do not quietly change the grouping when the analyst switches models or rows. Readable labels matter, but rounding a label must never misrepresent which observations belong to it.

Use one visible, button-like caption: **“Grouping: 20 · Equal exposure ▾”**. Opening it and choosing a setting takes two clicks. For numbers, offer count presets, **Each value**, **Equal width**, and **Custom cuts…**. Equal width reveals a width field, so “five years” is directly expressible. For categories, offer the display limit, ordering and search. Custom cuts require Apply; presets apply immediately. Reset takes one click when customised.

That costs one existing caption line and one popover. No permanent sort toolbar, no dedicated settings pane, no controls in feature-fitting settings. Explain “Display only” inside the popover and in custom-view exports; repeating it beside every control creates noise.

Remember choices per factor in workbench view state. Associate the resolved grouping with the data version so an upstream change cannot silently give an old view a new meaning. A shared grouping does not make different evaluation populations comparable: incompatible runs still need an explicit explanation.

**Two ideas the team has not developed enough**

First, **local refinement**. Increasing every numeric factor from 10 to 50 groups wastes attention when the question concerns ages 17–25. A “Refine this group…” action in the hover/detail panel should open custom grouping with that interval selected. The analyst adds boundaries there while everything else stays fixed. This respects the read-only chart: no dragging, no editing the model. One action opens the editor; typing and Apply complete the change.

Second, **search that extracts and pins a level**. Finding “Taxi driver” should add it beside the top categories and subtract its contribution from Other. Merely highlighting Other or searching only the displayed categories is useless. Pinning a few levels lets an analyst compare commercially important minorities without drawing 500 bars. The totals must still reconcile, with each observation represented once.

These capabilities determine the data architecture. Unrounded fine-bin sums are a useful cache, but arbitrary cuts, arbitrary category retrieval and second-factor splits require retained detail or recomputation. Bring forward the necessary part of kept predictions if those are launch requirements. Otherwise state the limitation plainly; do not present snapping as equivalent to exact custom cuts.

**Where the team over-builds**

The stats proposal hides an entire configuration product inside one menu: adaptive counts, spike detection, tail-width rules, rounding tolerances, thin-level merging, three axis modes, three grouping methods and selectable confidence levels. That is many interacting concepts, regardless of how few buttons open them. Its automatic tail splitting also conflicts with its promise of stable, factor-and-mass-based boundaries when splitting depends on claims.

Keep statistical uncertainty work, but separate it from this grouping decision. “Exposure share stands in” for claim-count credibility, as UI suggests, is not an acceptable substitution. Label exposure as exposure; leave unsupported uncertainty unavailable.

Drop the initial controls for confidence level, thinness threshold, equal-row grouping and proportional/logarithmic x spacing. Exact cuts and equal-width grouping provide more immediate exploratory value. The analyst is right to prioritise tails, but bespoke age defaults and automatic sentinel detection require domain knowledge the software does not possess. Do not infer that 99 is missing.

UI’s “one new concept” claim is accounting fiction. Count, method, rounding, model-derived boundaries, custom merges and ordering remain separate choices. Its caption location is good; its popover needs pruning. Permanently displaying every unsupported option disabled would also make older runs feel like a broken product.

**Where the team under-builds**

The architect’s permanent top-500 category cap—and stats’ top-1,000 cap—undermines search. Once discarded levels are pooled, no client can recover a requested occupation’s prediction. Cap the initial response, not the retained information, or provide an exact retrieval path.

Snapping creates the same problem for numeric exploration. If someone enters 25, rendering 24.9 with a warning does not answer their question. Offer exact recomputation when available. Otherwise ask them explicitly to accept the available boundary. A control called “Use model bands” must reproduce those bands exactly.

The proposed shared fine grid also has a lifecycle hole: adding a later model’s boundaries cannot split aggregates already stored for earlier models. “Include every model edge at training time” does not solve future training. Either retain enough detail to rebuild earlier aggregates or keep the shared grid immutable and acknowledge which operations it cannot support.

Likewise, “Not in training” is model-specific. Different models can have different training populations. Do not combine such levels into one universally shared bin without defining whose training set governs it.

**Where they disagree—and who is right**

The analyst wins on prioritising meaningful exploration over a maximal settings menu. UI wins on placing grouping beside the chart description. Stats and architecture win on requiring unrounded sums and stable boundaries.

On category order, the analyst/stats distinction between ordinal and nominal factors beats UI/architecture’s blanket natural ordering. However, inferred ordinal meaning needs an override; A–F may simply be identifiers.

On exactness, architecture’s restricted claim—exact when merging whole stored slices—is correct. Its suggestion that kept predictions necessarily come later is a delivery preference, not a technical conclusion.

The architect’s build order is backwards on shared boundaries: it first creates grids from each run’s diagnostic rows, then makes them shared later. Shared boundaries and evaluation identity are foundational to the comparison experience and belong in the first functional implementation.

**Code claims that need correction**

I checked the relevant source directly:

- **Browser merges are not exact today.** The analyst, UI report and brief overlook rounding. Both averages and exposure are rounded before storage in [`_metrics.py`](/home/ralph/suite/haute/src/haute/modelling/_metrics.py:596). Stats and architecture correctly identify this.
- **Categorical Missing is not protected.** It competes in the top-N ranking and can enter Other. The analyst overstates existing support. Moreover, nulls are replaced with the literal `__MISSING__`, which can collide with a real category. Use typed group identities, not display strings, in the new design. See [`_metrics.py`](/home/ralph/suite/haute/src/haute/modelling/_metrics.py:636).
- **Duplicate numeric edges are already removed.** Architecture proposes dropping tied edges as a quick fix, but `np.unique` already does that. The rare-flag collapse needs a distinct-value path or different construction, not another deduplication. See [`_metrics.py`](/home/ralph/suite/haute/src/haute/modelling/_metrics.py:573).
- **`chart_left_out` does not change the fit digest.** Architecture stopped tracing too early. `fit_config_digest` delegates to `training_identity_sha256`, whose allowlist excludes that field. See [`_candidate_run.py`](/home/ralph/suite/haute/src/haute/modelling/_candidate_run.py:29).
- **Comparison already checks evaluation identity.** It checks this alongside labels and exposure, contrary to descriptions suggesting only `sameLevels` matters. Preserve that protection. See [`ModelsAveChart.tsx`](/home/ralph/suite/haute/frontend/src/modelling/ModelsAveChart.tsx:64).
- **The t-boost categorical report does not export member arrays.** `_report_axis` consumes members but emits comma-joined labels. Reusing model groups requires a structured export; parsing labels would be unsafe. See [`_tboost.py`](/home/ralph/suite/haute/src/haute/modelling/_tboost.py:109).

Finally, both the brief and UI report call Features “selection only.” That is stale against the settled decisions: its settings icon opens fitting options. Keep grouping beside the chart because it controls the shared display, without using an obsolete description of Features to justify the choice.