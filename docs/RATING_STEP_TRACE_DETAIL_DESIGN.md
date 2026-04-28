# Rating Step Trace Details

## Problem

Rating step nodes can create several table output columns and combined output columns inside one node. Before this change, the trace could show a rating-step output as `computed` or `calculated` without explaining which table factors were used, which table value was selected, or whether the value came from a default. That made clicked table-output cells hard to audit even though the underlying preview value was correct.

## Approach

The backend trace enrichment adds a structured `rating_step` node detail payload for real rating-step configs. Each table detail records the table name, output column, factor columns and row values, selected value, match status, matched entry, and default value. Combined outputs record their output column, operation, base value, named table input values, and final value.

The frontend treats that payload as part of the calculation routine. The Calculation tab renders the rating tables and combined outputs directly below the result, and marks the clicked output as the traced column. The Nodes tab uses the same renderer so the two trace views stay consistent. Markdown export uses the same structured fields instead of raw JSON.

Validation stays in the rating helper layer. Unsupported combine operations fail loudly for both legacy `combinedColumn` and new `combinedOutputs` configs rather than silently behaving like multiplication.

## Alternatives Considered

### Keep Details Only In Nodes

Rejected because the primary user action is clicking a preview cell and reading the Calculation tab. Hiding the table explanation in Nodes makes the trace look incomplete for opaque rating-step outputs.

### Build A Separate Rating-Step Calculation Component

Rejected for now. The existing trace panel only needs a small structured node-detail renderer shared between Calculation and Nodes. A separate component would add indirection without a second use case.

### Derive Table Matches In The Frontend

Rejected because the backend has the runtime config and the row snapshots needed to match execution semantics. The frontend should render trace facts, not reimplement lookup behaviour.

## Open Questions

- Whether combined-output traces should eventually show a step-by-step arithmetic expression in addition to the current base/table-input summary.
- Whether future table editors should expose a stable table ID separate from `outputColumn`; the trace currently uses output column/name to identify the clicked table.

## Verification

The regression coverage includes direct enrichment tests, execute-trace integration tests, frontend render tests for matched/default/no-match tables, markdown export tests, and a Calculation-tab regression for opaque rating-step outputs.
