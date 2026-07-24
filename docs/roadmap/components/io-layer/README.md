# I/O layer improvement backlog

## Scope

Owns canonical Data Input and Data Output nodes, retained API Input and
response Output nodes, JSON shredding and input caches, format/provider
dispatch, file-picker/editor workflow, and data-integration boundaries.
Current contracts span
[I/O layer](../../../specs/io-layer/high-level.md),
[JSON shredding](../../../specs/json-shredding/high-level.md), and
[Databricks I/O](../../../specs/databricks-io/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| IO-IO03 | Reverify | P0 | Prevent Output documents from dropping fields that are null in early rows. | [Output schema drop](../../../fable-Review/io-nodes/IO03-output-document-schema-drop.md) |
| IO-IO04 | Reverify | P0 | Make API Input inference and cache build accept/reject the same values without fabricated loss. | [API Input infer/build contract](../../../fable-Review/io-nodes/IO04-apiinput-infer-build-contract.md) |
| AUD-C12 | Reverify | P0 | Stop the dataframe execution cache reopening and potentially evicting a just-written Parquet artifact; parser/cache-build duplicate-key validation is already aligned. | [Audit cluster C12](../../../review/REMEDIATION-PLAN.md#c12-cache-build-vs-parser-duplicate-key--unbounded-revalidate-divergence) |
| IO-IO01 | Reverify | P0 | Reverify picker/read error quality on canonical Data Input; format availability is now capability-driven. | [Picker format honesty](../../../fable-Review/io-nodes/IO01-picker-format-honesty.md) |
| IO-IO02 | Reverify | P0 | Finish source-of-truth parity for retained API Input and External File paths; canonical Data Input is already sidecar-driven. | [Two-copy config](../../../fable-Review/io-nodes/IO02-two-copies-config-hand-edits.md) |
| IO-IO07 | Reverify | P0 | Make generated Output behaviour agree with canvas/executor output assembly. | [Output standalone parity](../../../fable-Review/io-nodes/IO07-output-standalone-parity.md) |
| IO-IO05 | Reverify | P1 | Rebaseline Data Output write hardening around BOMs, overwrite policy, durability, and row-count observability; strict capability dispatch and unique staging are shipped. | [Sink write correctness](../../../fable-Review/io-nodes/IO05-sink-write-correctness.md) |
| IO-IO08 | Reverify | P1 | Prove the canonical Data Input schema/argument authoring surface supports bounded CSV end to end; the core declaration surface is shipped. | [Schema declaration surface](../../../fable-Review/io-nodes/IO08-schema-declaration-surface.md) |
| IO-IO06 | Reverify | P1 | Rebaseline destination, progress, and write-lifecycle UX against Data Output; the legacy Sink editor is gone. | [Sink and Output editor UX](../../../fable-Review/io-nodes/IO06-sink-output-editor-ux.md) |
| IO-IO09 | Reverify | P1 | Surface inference, schema, and validation feedback consistently in canonical Data Input and retained API Input editors. | [Input editor feedback](../../../fable-Review/io-nodes/IO09-input-editor-feedback.md) |
| IO-IO10 | Reverify | P1 | Reverify shred/snapshot/load work against the shared input cache without weakening correctness checks. | [Shred and load performance](../../../fable-Review/io-nodes/IO10-shred-and-load-performance.md) |
| IO-IO11 | Reverify | P2 | Batch low-risk I/O hygiene and maintainability fixes. | [I/O hygiene batch](../../../fable-Review/io-nodes/IO11-io-hygiene-batch.md) |
| IO-IO12 | Decision | P2 | The registry and capability API are shipped; decide which optional formats to add one registered capability at a time. | [Format registry and extensions](../../../fable-Review/io-nodes/IO12-format-registry-and-new-formats.md) |

## Dependencies

- IO-IO07 depends on IO-IO03 and coordinates with
  [Pipeline authoring](../pipeline-authoring/README.md) for generated/runtime
  equivalence.
- [Caching](../caching/README.md) owns generic fingerprint and artifact-lifetime
  policy; this component owns input-cache conservation/validation.
- [Deploy and platform](../deploy-platform/README.md) consumes declared I/O
  schemas but does not own editor or format semantics.

## Evidence and retirement

The [I/O Fable review](../../../fable-Review/io-nodes/README.md) supplies the
ordered design/TDD evidence and rejected-scope decisions. Audit C2 was retired
after conservation, schema validation, and content-hash freshness moved into
the current JSON-shred implementation and regression tests. Audit C12 now owns
only the remaining dataframe-cache post-write revalidation gap. Reverify each
package; retire it only after round-trip, conservation, bounded-memory, and
user-visible contracts are captured by current specs/tests.
