# Pipeline authoring improvement backlog

## Scope

Owns the decorator DSL, parser/submodel structure, graph round-tripping,
generated code, standalone `Pipeline.run()`/`score()` semantics, registry
wiring, and authored configuration contracts. Current behaviour spans
[pipeline config](../../../specs/pipeline-config/high-level.md),
[codegen](../../../specs/codegen/high-level.md), and
[submodels](../../../specs/submodels/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| AUD-C05 | Reverify | P0 | Enforce structure conservation so authored nodes, edges, and submodels survive parsing or fail loudly. | [Audit cluster C5](../../../review/REMEDIATION-PLAN.md#c5-parser-silently-loses-graph-structure-fail-loud-mandate-violations) |
| AUD-PIPE-01 | Reverify | P0 | Make public `run()`/`score()` output, source seeding, wiring, and instance semantics explicit instead of guessed. | [Re-verification Wave 0](../../../review/06-reverification/REPORT.md#wave-0--criticals--near-free-fail-loud-quick-wins--25-items) |
| AUD-C01 | Reverify | P0 | Share stateful apply helpers so generated standalone execution matches the executor. | [Audit cluster C1](../../../review/REMEDIATION-PLAN.md#c1-codegen-standalone-execution-non-equivalence-passthroughbranch-bodies) |

## Dependencies

- [Execution engine](../execution-engine/README.md) owns the runtime semantics
  used as the differential oracle.
- [I/O layer](../io-layer/README.md), [Modelling](../modelling/README.md), and
  [Optimiser](../optimiser/README.md) own their stateful helper semantics while
  this component owns generated wiring/equivalence.
- Parser structure work precedes broad differential generation so fixtures do
  not silently omit authored shapes.

## Evidence and retirement

The audit cluster plans are candidate designs tied to older code. Reproduce
each structural/equivalence failure against `HEAD` before editing. AUD-C01
includes the seeded parse-to-codegen-to-execute differential property rather
than tracking that evidence as a second package. Retire a
package only when authored structure is conserved, invalid shapes fail
actionably, and generated/runtime equivalence is protected by executable
properties and reconciled specs.
