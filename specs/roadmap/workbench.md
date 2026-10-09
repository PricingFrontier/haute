# Workbench roadmap

## Scope

The workbench inside Haute: the form file and its tables, the Workbench Input and Workbench
Output, and the Workbench view with its schema editor and its sheets. Current behaviour is
specified in
[the workbench specification](../workbench/high-level.md), and the save ledger and
milestones in [the git integration specification](../git-integration/high-level.md). The
packages below bring the rest of the form's editing into Haute one slice at a time, each
useful on its own; until they land, a form's sample is typed outside Haute, by Obverse's
standalone builder, which writes the same `forms/form.json`, or by hand.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-04 | Planned | P3 | The sample typed while building, saved with the form and priced live on the pipeline. |
| WB-05 | Planned | P2 | The form on the save ledger: saved, committed and reread on a branch switch like the pipeline. |
| WB-06 | Planned | P3 | Preview: an underwriter's quote keyed into the sheets and priced on the pipeline. |

## Planned improvements

### WB-04 — The sample, typed while building and priced live
**Why:** The Workbench Input's previews run on the sample typed into the form's Tables and
Collections, and a Collection's output columns should show what the pipeline gives that sample
as it is typed, as an Excel rater shows its result.

**Plan:** Typing into a Table's or Collection's input columns while building edits the form's
`sample`, undone like any edit and saved with the form. Whenever the schema or the sample
changes, once typing pauses, the view prices the sample on the pipeline open in the editor:
the workbench nodes of the document's top level take the form's tables and sample for that
request alone (`WORKBENCH_COPY_PATCHES`), the Workbench Output is previewed a table at a time
through the preview route, and each output column shows its table's value, dimmed until the
next answer arrives, with the reason in the toolbar when pricing fails.

**Acceptance:** A value typed into a Collection changes a downstream calculation's preview
once the form is saved, and the Collection's output column shows the pipeline's value for
the sample as it stands, saved or not.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_tables.py::sample_quote`;
`src/haute/_workbench_output.py::workbench_response`.

### WB-05 — The form on the save ledger
**Why:** The pipeline's Save commits exactly the files it wrote to the clone's ledger, and
Commit's sweep takes only files Git already tracks, so a form the view saves never enters
history on its own, the view has no Commit, and a branch switch leaves the view showing
the old branch's form until its Reload.

**Plan:** `PUT /api/workbench/form` records `forms/form.json` on the ledger through the same
capture the pipeline save uses, so Commit's sweep includes it once it is tracked; the view's
toolbar gets Commit, which saves the view's unsaved edits first, as it saves the pipeline
first; and the view rereads the form on the same document-adoption signal that reloads the
pipeline after a branch switch or a change on disk, keeping its refusal to overwrite a form
that changed on disk since it was read.

**Acceptance:** In the Workbench view, Save records `forms/form.json` on the ledger and Commit
includes it in the milestone, a new file included; a developer's direct edit to it is included
at Commit; after a branch switch the view shows that branch's form.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_save_pipeline.py::_capture_save_in_ledger`;
`src/haute/routes/workbench.py::put_workbench_form`.

### WB-06 — Preview: an underwriter's quote priced on the pipeline
**Why:** The view shows the sheets as an underwriter sees them, but the quote keyed into them
is neither sent nor priced; the form's own price button and payload were the proof of
concept's stub.

**Plan:** Preview keeps an underwriter's quote apart from the sample, validates it against the
columns' rules (required, ranges, allowed values) before pricing, and prices it on the
pipeline open in the editor through the same route the sample uses, the output columns of
Tables matched to their rows by key.

**Acceptance:** A quote keyed into Preview is priced on the pipeline, each output column
showing its value, and a cell that breaks its column's rule is marked and stops the pricing.

**Dependencies:** `WB-04`.

**Evidence:** `frontend/src/utils/workbenchTables.ts::WORKBENCH_COPY_PATCHES`.
