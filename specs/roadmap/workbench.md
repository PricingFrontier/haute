# Workbench roadmap

## Scope

The workbench inside Haute: the form file and its tables, the Workbench Input and Workbench
Output, the Workbench view with its schema editor, its sheets and the sample priced live,
and the form on the save ledger. Current behaviour is specified in
[the workbench specification](../workbench/high-level.md), and the save ledger and
milestones in [the git integration specification](../git-integration/high-level.md). The
packages below bring the rest of the workbench into Haute one slice at a time, each useful
on its own.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-06 | Planned | P3 | Preview: an underwriter's quote keyed into the sheets and priced on the pipeline. |

## Planned improvements

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
