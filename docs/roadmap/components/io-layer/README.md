# I/O layer improvement backlog

## Scope

Owns Data Source, API Input, Data Sink, and Output node correctness; JSON
shredding and per-port input caches; format dispatch; file-picker/editor
workflow; and data-integration boundaries. Current contracts span
[I/O layer](../../../specs/io-layer/high-level.md),
[JSON shredding](../../../specs/json-shredding/high-level.md), and
[Databricks I/O](../../../specs/databricks-io/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| IO-IO03 | Reverify | P0 | Prevent Output documents from dropping fields that are null in early rows. | [Output schema drop](../../../fable-Review/io-nodes/IO03-output-document-schema-drop.md) |
| IO-IO04 | Reverify | P0 | Make API Input inference and cache build accept/reject the same values without fabricated loss. | [API Input infer/build contract](../../../fable-Review/io-nodes/IO04-apiinput-infer-build-contract.md) |
| AUD-C02 | Reverify | P0 | Close JSON-shred conservation/fingerprint residuals not already owned by IO-IO04 or IO-IO10. | [Audit cluster C2](../../../review/REMEDIATION-PLAN.md#c2-json-shred-conservation--fingerprint-integrity-the-wave-2-cache-spine) |
| AUD-C12 | Reverify | P0 | Close parser/cache-build validation residuals not already owned by an I/O package. | [Audit cluster C12](../../../review/REMEDIATION-PLAN.md#c12-cache-build-vs-parser-duplicate-key--unbounded-revalidate-divergence) |
| IO-IO01 | Reverify | P0 | Advertise only readable formats and surface actionable picker/read errors. | [Picker format honesty](../../../fable-Review/io-nodes/IO01-picker-format-honesty.md) |
| IO-IO02 | Reverify | P0 | Remove two-copy Data Source configuration and preserve deliberate hand edits. | [Two-copy config](../../../fable-Review/io-nodes/IO02-two-copies-config-hand-edits.md) |
| IO-IO07 | Reverify | P0 | Make generated Output behaviour agree with canvas/executor output assembly. | [Output standalone parity](../../../fable-Review/io-nodes/IO07-output-standalone-parity.md) |
| IO-IO05 | Reverify | P1 | Make sink format selection, atomic replacement, overwrite, and CSV output semantics explicit. | [Sink write correctness](../../../fable-Review/io-nodes/IO05-sink-write-correctness.md) |
| IO-IO08 | Reverify | P1 | Give users the schema/dtype declaration surface needed for bounded CSV deploy. | [Schema declaration surface](../../../fable-Review/io-nodes/IO08-schema-declaration-surface.md) |
| IO-IO06 | Reverify | P1 | Make Sink/Output destination, write state, and editor lifecycle visible and durable. | [Sink and Output editor UX](../../../fable-Review/io-nodes/IO06-sink-output-editor-ux.md) |
| IO-IO09 | Reverify | P1 | Surface input inference, schema, and validation feedback in the editor. | [Input editor feedback](../../../fable-Review/io-nodes/IO09-input-editor-feedback.md) |
| IO-IO10 | Reverify | P1 | Remove redundant shred/load work without weakening correctness checks. | [Shred and load performance](../../../fable-Review/io-nodes/IO10-shred-and-load-performance.md) |
| IO-IO11 | Reverify | P2 | Batch low-risk I/O hygiene and maintainability fixes. | [I/O hygiene batch](../../../fable-Review/io-nodes/IO11-io-hygiene-batch.md) |
| IO-IO12 | Decision | P2 | Centralize format capabilities, then add formats one deliberate entry at a time. | [Format registry and extensions](../../../fable-Review/io-nodes/IO12-format-registry-and-new-formats.md) |

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
ordered design/TDD evidence and rejected-scope decisions. Audit C2/C12 own only
residual findings after overlaps are folded into the more specific IO package.
Reverify each package; retire it
only after round-trip, conservation, bounded-memory, and user-visible contracts
are captured by current specs/tests.
