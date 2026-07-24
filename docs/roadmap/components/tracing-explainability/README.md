# Tracing and explainability improvement backlog

## Scope

Owns expression-evaluation fidelity, row correlation and lineage,
waterfall membership/reconciliation, trace enrichment failure semantics, and
trace-specific performance evidence. Current contracts live in the
[tracing](../../../specs/tracing/high-level.md) and
[expression-parsing](../../../specs/expression-parsing/high-level.md)
specifications.

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| AUD-C07 | Reverify | P0 | Stop laundering evaluator failures into plausible observed values and close known Polars semantic gaps. | [Audit cluster C7](../../../review/REMEDIATION-PLAN.md#c7-trace-expression-evaluator-re-implements-polars-and-launders-failures-wrong-explanation-shown-loudly-clean) |
| AUD-C08 | Reverify | P0 | Require unique, scale-aware, structurally justified row correlation instead of positional/tolerance guesses. | [Audit cluster C8](../../../review/REMEDIATION-PLAN.md#c8-trace-correlation-positionaltolerance-heuristics-relocate-to-the-wrong-row-wrong-explanation) |
| AUD-TRACE-01 | Reverify | P1 | Make integer/null semantics and parse/enrichment cost agree with real Polars under seeded differential tests. | [Re-verification Wave 3](../../../review/06-reverification/REPORT.md#wave-3--parser-structure-conservation--expression-numerical-fidelity--7-items) |

## Dependencies

- [Execution engine](../execution-engine/README.md) owns projection/execution
  correctness; tracing consumes the frames/lineage it produces.
- [Caching](../caching/README.md) owns shared preview/trace cache identity.
- [Engineering quality](../engineering-quality/README.md) owns the shared
  seeded differential-test convention.

## Evidence and retirement

The [dated performance baseline](../../tracing-performance-baseline-2026-07-23.md)
is measurement evidence, not a work package. The retired tracing Fable review
is recoverable through Git; current behaviour is in the tracing specs and
ordinary tests. Retire audit packages only after real-Polars agreement,
fail-loud behaviour, and row/waterfall fidelity are regression-proved.
