# Frontend node editors roadmap

## Scope

The node configuration panel's editors: one per node type, the shared pickers
and form kit they compose, the client-side mirrors of backend rules (paths,
labels, output mapping), and the Explore configuration cards. Current behaviour
is specified in
[the frontend node editors specification](../frontend-node-editors/high-level.md).
These packages come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FNED-R01 | Planned | P2 | An editor writes only what the engine reads, shows values as the engine will match them, resolves a registered alias's version, flags only the conflicts the save refuses, and refuses a non-integer step count instead of coercing it. |
| FNED-R02 | Planned | P2 | Every text control goes through the committed-input kit, so a value changed by undo, redo or a document broadcast is shown, no field commits per keystroke, and every request failure shows the server's detail. Returning to a cached directory clears the previous directory's error. |
| FNED-R03 | Planned | P3 | The pickers, cards and grouping helpers written twice exist once, and the vestigial `errorLine` prop and the dead exports are gone. |
| FNED-R04 | Planned | P2 | A banding rule the sidecar writer refuses is marked in the grid and refused by the save validator with a 400, a typed relativity that does not parse is refused rather than replaced, and the step builder's argument controls refuse what the renderer refuses with a visible message. |
| FNED-R05 | Planned | P3 | The step builder's open card follows its step through undo, the pivot editor carries no unknown keys the backend refuses, the two banding grids, the two rating grids, the params pickers and the zone cards exist once, and the internal-only exports are un-exported. |
| FNED-R06 | Planned | P1 | A schema inference reply cannot replace a newer input file, table configuration or document. |

## Planned improvements

### FNED-R01 — Editors write what the engine reads
**Why:** The Output editor's "Output format" select writes an `outputFormat` key
that no backend code reads (it is hashed into the cache identity and copied into
the config, never consulted by the assembler), so the control and the
placeholder it makes the user pass do nothing; the node documentation already
says the response is JSON either way. Before a node's data is cached, Banding's
categorical value chips are built with JavaScript `String(raw)` over preview
rows while execution matches on Polars `cast(pl.Utf8)` text, so on a float
column a chip writes `"1"` where the data reads `"1.0"` and the rule never
matches. Optimiser Apply resolves a registered version by hand and ignores an
alias, where Model Score uses the shared `resolveLoadedVersion`; with an alias
chosen, the mode is taken from the newest version and the mirror effect writes
that `optimiser_mode` into the config, clearing `ratebook_input` when it reads
`online`, so codegen wires the node for the wrong mode without the user touching
anything. The Output editor's conflict warning counts rows with a blank source
column that the assembler skips, so an added row is flagged although the save
accepts it. The API Input editor persists two column keys (`origin`, `key`) the
backend's `ColumnV2` does not declare. The Scenario Expander's Steps control
silently rewrites `0`, `abc` and `2.7` to `1`, `1` and `2` where the backend
refuses a non-integer loudly.

**Plan:** Drop the format control and key until a second format exists
(updating the node documentation's key table) or have the assembler read it;
format preview values with the server's cast rule or withhold chips for
non-string columns; use `resolveLoadedVersion` in Optimiser Apply; require a
non-blank source column in `detectConflicts` or share one `isActiveEntry`
predicate; declare `origin` and `key` in `ColumnV2`, the specification and the
docs, or move them to the editor-state field `PCFG-R08` introduces; make Steps a
validated field refusing non-integers and values below 1.

**Acceptance:** No editor writes a key the backend ignores (a test over every
editor's writes against the node's typed config and its readers); a float
column's chip matches its rows; an alias pointing at an older version drives
the mirrored mode; an added row with a blank column raises no conflict; `2.7` in
Steps is refused with a message.

**Dependencies:** `PCFG-R08` for the editor-state field.

**Evidence:** `frontend/src/panels/editors/OutputEditor.tsx`;
`frontend/src/panels/editors/outputMappingSchema.ts`;
`frontend/src/panels/editors/BandingEditor.tsx`; `src/haute/_rating.py`;
`frontend/src/panels/editors/OptimiserApplyEditor.tsx`;
`frontend/src/panels/editors/ModelScoreEditor.tsx`;
`frontend/src/panels/editors/apiInputSchema.ts`;
`src/haute/_api_input_schema.py::ColumnV2`;
`frontend/src/panels/editors/ScenarioExpanderEditor.tsx`.

### FNED-R02 — Drafts in step with the document, errors with their detail
**Why:** Four Rating Step fields are uncontrolled inputs committing on blur
instead of the committed-input primitives, so Enter does not commit and an undo,
redo or broadcast that changes the value leaves the stale text on screen. The
pivot name draft is seeded once and never re-synced, so after an undo the field
shows the old name and a blur with no typing re-commits it, silently undoing the
undo. The chart editor's axis title commits a graph edit on every keystroke, one
undo entry and one document update per character. Four hand-rolled
draft-then-commit controls (the chart stack group, the axis bounds, the pivot
name, the Scenario min and max with their hundred-line draft machine)
re-implement `ValidatedTextField`. Four request failures are shown as
`error.message` or a fixed string: an `ApiError`'s message is only
`HTTP <status>`, so Infer Tables shows "HTTP 422" for a schema error, while every
other editor uses `apiErrorMessage`; the lint that rejects local copies does not
catch the ternary form.

**Plan:** Replace the uncontrolled inputs and the hand-rolled drafts with
`CommittedTextField` and `ValidatedTextField`; key or re-sync the pivot name;
commit the axis title on blur; route the four error sites through
`apiErrorMessage` and widen the lint selector to the ternary form.

**Acceptance:** Undo while a Rating Step field or the pivot name is on screen
updates the field and a blur commits nothing; typing an axis title records one
undo entry; Infer Tables shows the server's detail; the lint fails on
`e instanceof Error ? e.message : ...`.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/RatingStepEditor.tsx`;
`frontend/src/panels/editors/ExplorePivotsConfig.tsx`;
`frontend/src/panels/editors/ExploreChartsConfig.tsx`;
`frontend/src/panels/editors/ScenarioExpanderEditor.tsx`;
`frontend/src/panels/editors/ApiInputEditor.tsx`;
`frontend/src/panels/editors/_shared.tsx`;
`frontend/src/panels/editors/_ioFormats.ts`; `frontend/src/api/errors.ts`;
`frontend/eslint.config.js`.

**28 October full-pass addition:** The shared FileBrowser cache-hit branch returns before
clearing an earlier directory error. After a child returns Permission denied, Up restores
cached parent items but continues rendering the error instead of files. **Plan/acceptance:**
cached navigation resets request/error/loading state coherently; a real picker test visits a
failed child then returns to a cached parent and can select its file. Evidence:
`frontend/src/panels/editors/_shared.tsx::FileBrowser` (cache-hit effect) and its error-first render. The witness measured
the lingering error; selection was blocked before its assertion.

### FNED-R03 — One copy of each picker, card and helper
**Why:** The Workbench Output's table card re-implements `WorkbenchTableCard`,
whose export then has no consumer; the two Databricks pickers each carry their
own catalog and schema fetch helpers and cascading selects; `buildInheritGroups`
and `buildAllKeyGroups` are the same grouping loop; the destination-change
handler that clears the MLflow selection is written in both Model Score and
Optimiser Apply; four editors declare an `errorLine` prop they never read since
the code box moved to the shared Transform tab; `jsonpath.ts::NAME`,
`outputMappingSchema.ts::NAME_RE`, `outputPathTools.ts::composePrefix` and two
duplicate default exports have no consumer (verified against the
repository-wide standard).

**Plan:** Compose the card, lift a `CatalogSchemaSelects` component, one
grouping helper, one `clearedMlflowSelection`, drop the prop and the dead
exports.

**Acceptance:** jscpd reports none of the pairs; knip reports none of the named
exports; the editor tests pass unchanged.

**Dependencies:** `ENGQ-R01` owns the editors' barrel and the unlisted
dependency.

**Evidence:** `frontend/src/panels/editors/WorkbenchOutputEditor.tsx`;
`frontend/src/panels/editors/WorkbenchInputEditor.tsx`;
`frontend/src/panels/editors/_DatabricksSelector.tsx`;
`frontend/src/panels/editors/apiInputInherit.ts`;
`frontend/src/panels/editors/jsonpath.ts`;
`frontend/src/panels/editors/outputMappingSchema.ts`;
`frontend/src/panels/editors/outputPathTools.ts`.

### FNED-R04 — What the grids let through, the save refuses with a reason
**Why:** The banding grids leave three rule states unmarked that the save
validator accepts but the sidecar writer refuses: a breakpoint row with a blank
Band name (what "Add" creates), a categorical row with a blank Value or Maps To
(what "Add manually" creates), and a repeated categorical Value. The writer's
`ConfigSettingError` is a `HauteValidationError` with no HTTP handler, and the
save's only enclosing handler rolls back and re-raises, so Save fails with a 500
whose reason reaches only the server log, against the specification's "refused
with a 400 naming the node and the setting, before anything is written"
(validator-versus-writer split probed; the 500 traced statically). A typed
rating relativity is parsed with `parseFloat` and anything unparseable is
silently replaced by the table default (`1,5` writes 1, `abc` writes 1.0) while
the paste path uses strict `Number` and refuses with a toast. The step builder's
`integer` arguments have no minimum, so `-1` is committed and refused only by the
render; the formula parser accepts negative and fractional integers the same
way; a date `format` typed without `%` is thrown away with no message; a
quantile above 1 is clamped to 1 silently; and a Change-types entry keeps a
`format` the form then hides when the column's type stops being text, so the run
fails with "remove the format" while the control to remove it is not shown.

**Plan:** Have `validate_banding_config` run the writer's row checks so the
refusal is the documented 400, mark the offending row inline in both grids, and
give `HauteValidationError` a 400 handler so a writer refusal can never be a
500; parse typed relativities with the paste path's strict parser and mark the
cell invalid; `min={0}` on integer arguments and a `FormulaError` for a negative
or fractional integer literal; a visible refusal for a `%`-less format and a
`max` on the quantile field; show the Format field whenever a format is set or
strip it when the schema says the column is not text.

**Acceptance:** Saving a banding node with a blank Band name is answered 400
naming the node and the row, and the row is marked before the save; typing
`1,5` into a relativity cell shows an invalid mark and writes nothing; `-1`
decimal places, a `%`-less format and a quantile of 1.5 each show a message;
a Change-types entry with a stale format shows its Format field.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/banding/BreakpointGrid.tsx`;
`frontend/src/panels/editors/banding/BandingRulesGrid.tsx`;
`src/haute/_rating.py::validate_banding_config`;
`src/haute/_banding_config.py::compact_banding_config_for_sidecar`;
`src/haute/_validation_error.py::ConfigSettingError`;
`frontend/src/panels/editors/rating/TwoWayGrid.tsx`;
`frontend/src/panels/editors/rating/OneWayEditor.tsx`;
`frontend/src/panels/editors/polarsSteps/forms.tsx`;
`frontend/src/panels/editors/polarsSteps/fields.tsx`;
`frontend/src/panels/editors/polarsSteps/formula.ts`.

### FNED-R05 — Positions, passthroughs and copies in the editor subdirectories
**Why:** The step builder holds its open card and pointed step as positions,
adjusted only by its own moves and deletes, so an undo that re-inserts an
earlier step opens a different step's form in place of the one being edited.
`futurePlacementFields` carries unknown pivot placement keys through every move
while the backend validator refuses any key outside its known set, a
forward-compatibility branch the canonical-only policy forbids and a frontend
specification sentence that contradicts the backend's rule. The two banding
grids each carry a copy of the header recogniser, pasted-range applier, TSV
builder and cell-paste handler; the one-way and two-way rating grids
re-implement the same selection state machine and copy handler; Banding's Import
from params and the Workbench's Allowed values each own the params-folder
listing and header cache; the formula card and the field card in a pivot zone
repeat the draggable-card scaffolding; two private dtype classifiers re-implement
the shared `castFamily`; twenty-odd exports are referenced only inside their own
module. The numeric Generate dialog suggests a step of 1 for any range below 10,
so rates and factors get a single band; the aggregation column box and the type
select offer two different type lists.

**Plan:** Hold the open and pointed steps by id; delete the passthrough and the
specification sentence (or make the backend carry unknown keys); one grid
clipboard hook, one cell-selection hook, one params-files hook, one placement
card, `castFamily` everywhere; un-export the internal symbols; a
magnitude-aware suggested step; one type list.

**Acceptance:** Undo while a card is open keeps that card's form; a placement
with an unknown key is refused by the editor as by the backend; jscpd reports
none of the pairs; knip reports none of the exports; a range of 0.2–0.9 suggests
about ten bands.

**Dependencies:** `EDA-R02` requires the defaulted card fields on the backend.

**Evidence:** `frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx`;
`frontend/src/panels/editors/explorePivots/placements.ts::futurePlacementFields`;
`frontend/src/panels/editors/banding/BreakpointGrid.tsx`;
`frontend/src/panels/editors/banding/BandingRulesGrid.tsx`;
`frontend/src/panels/editors/rating/TwoWayGrid.tsx`;
`frontend/src/panels/editors/banding/ImportParamValues.tsx`;
`frontend/src/workbench/schema/AllowedField.tsx`;
`frontend/src/panels/editors/explorePivots/ZoneSection.tsx`;
`frontend/src/panels/editors/banding/GenerateBandsDialog.tsx`;
`frontend/src/utils/polarsDtypes.ts`.

### FNED-R06 — Inference stays bound to its source and draft
**Why:** The [28 October review](codebase-review-2026-10-28.md) started Infer
Tables on `first.json` with no tables, changed the editor to `second.json` with
a new table, and released the old response. `inferTables` used the old closure's
empty-table test and `v2` config to write back `first.json` and its inferred
table, erasing the new configuration. It has no request generation, source or
document fence; its replace confirmation is also based on the earlier draft.

**Plan:** Bind inference and any staged confirmation to the initiating document,
node, source and relevant draft version. Invalidate/abort outdated work and
discard stale replies. Recheck the current draft before automatic first-time
application; never decide that there is nothing to overwrite from a captured
empty config. Specify how a still-current inference can be deliberately merged
after the draft changes, preserving the existing confirmation rules.

**Acceptance:** A delayed response after a file change, same-file table edit,
node/document change or loss of edit capability changes no newer config and
leaves no usable stale confirmation. An unchanged empty draft still applies
the inference, and an unchanged populated draft requires its existing explicit
confirmation. Test with the actual editor and controlled request completion.

**Dependencies:** None. `FSH-R10` owns a different race in ordinary config
commit resolution; fixing that controller alone does not authenticate inference.

**Evidence:** `frontend/src/panels/editors/ApiInputEditor.tsx`;
`frontend/src/panels/editors/apiInputSchema.ts`.

**28 October full-pass addition:** Completed Data Input schema is also source-bound:
`useSchemaFetch` keeps A's schema while B loads, and `DataInputEditor` leaves Use detected
schema enabled. The real editor commits `{a_only: Int64}` into B's arguments in the witness.
**Plan/acceptance:** associate schema with path and reader settings, clear or render stale
results explicitly, and disable application until that identity matches the current draft.
Cover file changes, reader-option changes, retries and clearing a path. Existing source/draft
fencing must cover already-completed schema as well as late inference replies. Evidence:
`frontend/src/hooks/useSchemaFetch.ts::useSchemaFetch.fetchForPath`;
`frontend/src/panels/editors/DataInputEditor.tsx::DataInputEditor` (Use detected schema button).
