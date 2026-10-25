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
| FNED-R02 | Planned | P2 | Every text control goes through the committed-input kit, so a value changed by undo, redo or a document broadcast is shown, no field commits per keystroke, and every request failure shows the server's detail. |
| FNED-R03 | Planned | P3 | The pickers, cards and grouping helpers written twice exist once, and the vestigial `errorLine` prop and the dead exports are gone. |

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
