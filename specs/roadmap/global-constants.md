# Global constants roadmap

## Scope

Named, typed values that every code box and every structured step in a
pipeline reads as `global_constants.<name>`, without an edge, with one value
per source where the pipeline needs it. Decided with the user on 2 October
2026: the toolbar's Imports button becomes Constants; each constant is either
the same for every source or split by source; the Constant node stays as it
is; the step editor offers a constant wherever it offers a variable; and code
reads a constant through the one reserved name `global_constants`, so the
generated `.py` stays lint-clean and no constant can collide with an input
name.

The behaviour is specified in
[the pipeline-config approved change contract](../pipeline-config/high-level.md#approved-change-contract--global-constants)
and [its low-level counterpart](../pipeline-config/low-level.md#approved-change-contract--global-constants).
Each package folds its part into the present-tense specification of the
component that owns it, and the last package to land removes the contract.

Out of scope: config fields outside the step editor reading constants;
list, map or frame constants; utility modules reading constants; automatic
imports and the Functions and Libraries panes (the preamble editor moves into
the Utility pane until they exist); and assistant support for constants.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| GCONST-04 | Planned | P2 | The toolbar's Constants button opens a pane that edits the constants, per source where split. |
| GCONST-05 | Planned | P2 | The step editor offers constants wherever it offers variables, and in typed function arguments. |

## Planned improvements

### GCONST-04 — The Constants pane
**Why:** Analysts set and compare constants per source in the editor, without
editing JSON, and the toolbar slot the user chose for them is the Imports
button's.

**Plan:** The toolbar's Imports button becomes Constants, and the Utility
pane's file list gains a fixed `Imports` entry that edits the preamble as the
Imports pane did. The graph store holds the constants beside the preamble, in
the dirty fingerprint and the save request, and both graph builders read them
from the store when called, so the graph of every execution request (preview,
training, optimiser, Explore, Data Output and Output) carries them and their
load error. The pane edits names, types and values as the contract states, marks
missing and invalid values, lists the nodes that read each constant, confirms
a delete or rename of a constant that is read, and shows a constants file that
failed to load, read-only. Removing a source confirms and then removes its
values from split constants.

**Acceptance:** Component tests cover the toolbar slot, the Utility pane's
`Imports` entry, adding, editing, splitting and joining constants, the type
change rules, the missing and invalid markers, the read-only load error and the
confirmations; graph store tests prove a constants edit marks the pipeline
unsaved and reaches the save and execution payloads; request tests prove the
training, optimiser and Explore requests carry a constant edited after their
panel mounted; and a browser test defines
a split constant, previews a node that reads it under `live` and `nb_batch`,
and sees each value.

**Dependencies:** None.

**Evidence:** `frontend/src/components/Toolbar.tsx`;
`frontend/src/panels/ImportsPanel.tsx`;
`frontend/src/panels/UtilityPanel.tsx`;
`frontend/src/stores/useGraphStore.ts`;
`frontend/src/stores/useSettingsStore.ts`.

### GCONST-05 — Constants in the step editor
**Why:** The user asked for constants in the Polars and Transform step
sections, not only in code, and a constant has a declared type, so it can also
fill the typed function arguments a variable cannot.

**Plan:** The step renderer accepts a `constant` operand wherever it accepts a
`variable` operand, and in typed function arguments; it renders the bare
`global_constants.<name>` in value position and wraps it in `pl.lit(...)` in
expression position, and it reports each reference with the types its slot
takes. Save, and the render route when given the pipeline's constants, refuse
an undefined constant or one whose type the slot does not take. The editor's
value control offers Constant with the constants that fit the slot, the
formula box parses and prints `global_constants.<name>`, code editors complete
constant names after `global_constants.`, and free-code column results are keyed
on the source and the read constants' values as well as the steps.

**Acceptance:** Renderer tests cover each slot kind and its type rules,
including a non-negative integer argument checked against every source's
value; the step catalogue parity test includes the new operand kind; field,
formula and completion component tests pass; and a browser test filters on a
split constant and previews a different row count under each source.

**Dependencies:** GCONST-04.

**Evidence:** `src/haute/_polars_steps.py::render_polars_steps`;
`frontend/src/panels/editors/polarsSteps/fields.tsx::OperandField`;
`frontend/src/panels/editors/polarsSteps/formula.ts::parseFormula`;
`tests/test_polars_steps_catalogue.py`.
