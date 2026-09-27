# Bugs roadmap

## Scope

Defects found outside a component's own roadmap work, kept here until they
are fixed. These packages came from the 27 September 2026 audit of the
Getting Started and Building Models documentation, which checked every page
against the code: each one is a place where the code, not only the page, is
wrong. Current rating behaviour is specified in
[the rating specification](../rating/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| BUG-01 | Planned | P1 | Editing a Rating Step in the editor keeps each table's `onMissing` setting. |
| BUG-02 | Decision | P1 | A rating table built in the editor no longer prices an unmatched level at 1.0 without saying so. |
| BUG-03 | Planned | P3 | The ratebook optimiser settings nothing reads are gone. |

## Planned improvements

`BUG-02` decides when `onMissing` matters for a table built in the editor, so
`BUG-01`'s control is easiest to place after it; the fix that stops the
setting being lost does not wait for it.

### BUG-01 — Editing a Rating Step keeps each table's `onMissing`
**Why:** A rating table's `onMissing` decides what a miss does when the table
has no usable `defaultValue`: `"error"` (the default) raises
`RatingTableMissError`, and `"neutral"` leaves the output null and logs the
misses. The editor has no control for it, and `normaliseRatingTable` rebuilds
every table from `factors`, `factorDtypes`, `outputColumn`, `defaultValue` and
`entries` only. Any edit of the node in the editor (a factor, an entry, the
default, adding or removing a table) therefore removes an `onMissing` set in
the pipeline, with no message, and a table that was set to `"neutral"` fails
the run on its next miss.

**Plan:** Carry `onMissing` through `normaliseRatingTable`, and show it in the
table editor beside Default, where it applies only while Default is empty.

**Acceptance:** A frontend test edits an entry of a table configured with
`onMissing: "neutral"` and the committed config still carries it; the editor
shows the setting and changes it; the rating specification names the
control.

**Dependencies:** None to stop the loss; the control's placement follows
`BUG-02`.

**Evidence:** `frontend/src/panels/editors/rating/ratingTableUtils.ts::normaliseRatingTable`;
`src/haute/_rating.py::_normalise_on_missing`;
`src/haute/_rating.py::RatingTableMissError`; `specs/rating/high-level.md`
(miss precedence).

### BUG-02 — A rating table built in the editor says when a level is unmatched
**Why:** A miss is filled silently by a usable `defaultValue`, and raises only
when the table has none and `onMissing` is `"error"`. Every table the editor
creates starts with `defaultValue` `"1.0"` (the Rating Step node defaults and
the Add table button), so an unmatched level (a new vehicle group, a key typed
differently from the data) is priced at 1.0 with no error or warning unless
the analyst clears Default. That contradicts fail-loud pricing, and the Rating
Step page's "Misses fail loudly by default".

**Plan:** Decide between: new tables start with an empty Default, so a miss
fails until the analyst sets a default or chooses `"neutral"`; or tables keep
1.0 and the preview and trace report each table's miss count. Then change the
node defaults, the editor, the rating specification and the Rating Step page
together.

**Acceptance:** A new table built in the editor, run on data with an
unmatched level, either fails the run naming the table and key or shows the
table's miss count in the preview; a test covers that miss.

**Dependencies:** None.

**Evidence:** `src/haute/node_defaults.json` (`ratingStep`);
`frontend/src/panels/editors/RatingStepEditor.tsx` (the table fallback and
`addTable`); `src/haute/_rating.py::RatingTableMissError`;
`docs/building-models/nodes/rating-step.md`.

### BUG-03 — Remove the ratebook optimiser settings nothing reads
**Why:** The optimiser config declares `candidate_min`, `candidate_max`,
`candidate_steps` and `structure_mode`. The cache classifies them, recovery
validates `candidate_steps`, and the Optimiser page lists the first three as
required, but the ratebook solve builds `price_contour`'s `RatebookOptimiser`
without any of them, and no editor field sets them. The candidate values come
from the scored scenario grid and the factor structure from the Factors pane,
so a value written for these keys has no effect.

**Plan:** Delete the four fields from `OptimiserConfig`, the cache field
classification, the recovery validation, and the Optimiser page.

**Acceptance:** No reference to the four keys remains in `src/`,
`frontend/src/`, `specs/` or `docs/`; the config round-trip and cache field
classification tests pass.

**Dependencies:** None.

**Evidence:** `src/haute/_types.py` (`OptimiserConfig` and its field list);
`src/haute/_cache.py` (the optimiser field classification);
`src/haute/_node_config_recovery.py`;
`src/haute/routes/_optimiser_solver.py` (`RatebookOptimiser(`);
`docs/building-models/nodes/optimiser.md`.
