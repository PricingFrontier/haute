# Workbench roadmap

## Scope

The workbench inside Haute: the form file and its tables, the Workbench Input and Workbench
Output, and the Workbench view with its schema editor, its sheets and the sample priced
live. Current behaviour is specified in
[the workbench specification](../workbench/high-level.md), and the save ledger and
milestones in [the git integration specification](../git-integration/high-level.md). The
packages below bring the rest of the workbench into Haute one slice at a time, each useful
on its own.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-05 | Planned | P2 | The form on the save ledger: saved, committed and reread on a branch switch like the pipeline. |
| WB-06 | Planned | P3 | Preview: an underwriter's quote keyed into the sheets and priced on the pipeline. |

## Planned improvements

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
**Why:** The view's sheets take the sample a pricing analyst types while building, priced
live; an underwriter's quote keyed into them is neither kept apart nor priced, and the proof
of concept's own price button and payload were a stub.

**Plan:** Preview keeps an underwriter's quote apart from the sample, validates it against the
columns' rules (required, ranges, allowed values) before pricing, and prices it on the
pipeline open in the editor through the same pricing the sample uses, the output columns of
Tables matched to their rows by key.

**Acceptance:** A quote keyed into Preview is priced on the pipeline, each output column
showing its value, and a cell that breaks its column's rule is marked and stops the pricing.

**Dependencies:** None.

**Evidence:** `frontend/src/workbench/priceSample.ts::priceSample`;
`frontend/src/stores/useWorkbenchPricingStore.ts::useWorkbenchPricingStore`.
