# Tracing and explainability roadmap

## Scope

Owns expression-evaluation fidelity, row correlation and lineage, waterfall
membership/reconciliation, enrichment failure semantics, and trace-specific
performance evidence. Current behaviour is specified in
[tracing](../tracing/high-level.md) and
[expression parsing](../expression-parsing/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `AUD-C07` | Reverify | P0 | Match real Polars expression semantics and never launder evaluator failures into plausible values. |
| `AUD-C08` | Reverify | P0 | Require unique, structurally justified row correlation and honest waterfall membership. |

`AUD-TRACE-01` is folded into `AUD-C07`: its integer/null differential and
parse-cost work share the same evaluator contract and acceptance suite.

## Planned improvements

### AUD-C07 — Polars-faithful, fail-loud evaluation

**Why:** The explanation evaluator re-implements part of Polars and can hide an
unsupported/wrong calculation by substituting the already-observed row value.
Repeated parsing also turns chain enrichment into avoidable superlinear work.

**Plan:**

- Return a typed unresolved/error result when parsing or evaluation is
  unsupported; never use the target's observed value as a catch-all computed
  result.
- Match real Polars for integer overflow/casts, Kleene Boolean/null logic,
  `concat_str`, `replace_strict`, horizontal functions, and known expression
  families.
- Parse and locate the target expression once, reuse its AST for compute and
  chain enrichment, and bound work by expression/tree size.
- Preserve an explicit distinction between parse failure, unsupported
  expression, evaluation failure, and a legitimate null result in the API/UI.

**Acceptance:**

- A seeded real-Polars differential corpus covers scalar, null, non-finite,
  integer-boundary, Boolean, string, replacement, conditional, and horizontal
  expressions.
- Injected parser/evaluator bugs produce visible typed errors and never a
  self-consistent-looking observed-value fallback.
- Chain enrichment parses once per expression/step and has a scale regression
  for wide/deep shapes.
- Export and UI render the same error/value semantics.

**Dependencies:** [Engineering quality](engineering-quality.md) owns the
generic differential-test convention; tracing owns the expression corpus.

**Evidence:** `src/haute/_expression_parser.py`,
`src/haute/_trace_enrichment.py`, `tests/test_expression_parser.py`,
`tests/test_trace_enrichment.py`, `tests/test_trace_matches_preview.py`, and
`tests/test_trace_hero_tdd.py`.

### AUD-C08 — Unique row correlation and complete waterfalls

**Why:** Position, absolute tolerance, or a single row's observed schema delta
can attach an explanation to the wrong parent row or omit a real step.

**Plan:**

- Prefer explicit stable row identity/lineage carried through execution.
- Remove positional acceptance when a transform can reorder rows and no shared
  key proves identity.
- Require a unique match with scale-relative numeric comparison before
  relocating upstream values; otherwise return an unresolved diagnostic.
- Gate waterfall membership on structural expression targets, not whether the
  operation happened to be a no-op for the selected row.
- Surface the correlation strategy and ambiguity reason in the response.

**Acceptance:**

- Reorder, duplicate-key, join, filter, explode, multi-frame, tiny/large float,
  and row-local no-op fixtures never produce a wrong attribution.
- Ambiguous evidence yields an explicit unresolved state, never the first or
  positional candidate.
- Waterfalls include structurally contributing ×1/+0 steps and reconcile to
  the displayed result within the documented numeric contract.
- Trace, preview, and export agree on row identity and diagnostics.

**Dependencies:** [Execution](execution-engine.md) owns lineage propagation;
tracing owns correlation and explanation policy.

**Evidence:** `src/haute/_trace_correlation.py`,
`src/haute/_trace_enrichment.py`, `src/haute/_trace_waterfall.py`,
`tests/test_trace_correlation_remediation.py`,
`tests/test_trace_integration.py`, `tests/test_trace_matches_preview.py`, and
`tests/test_trace.py`.

## Performance baseline

The last retained local baseline was captured on 2026-07-23 using Windows 10,
Python 3.11.13, and Polars 1.39.3:

| Shape | Total | Correlation | Serialization/validation |
|---|---:|---:|---:|
| Linear cold trace, 9 steps | 40.342 ms | 8.983 ms | 0.299 ms |
| Join cold trace, 6 steps | 40.772 ms | 6.090 ms | 0.161 ms |
| Join with full-preview reuse | 10.353 ms | 4.055 ms | 0.164 ms |
| Join trace-cache hit | 14.181 ms | 8.158 ms | 0.162 ms |
| Multi-frame correlation only | — | 1.525 ms | — |

The browser p95 was 302.8 ms for a 24-step linear payload and 389.4 ms for a
16-step join/multi-frame payload. Both were below the 500 ms exceptional-latency
threshold. Serialization was at most 0.3 ms in these shapes, so payload
projection, a client trace cache, and a weakened runtime-input fingerprint were
not justified. Future optimisation must add reproducible evidence for its
target shape and preserve trace/export semantics.

**Evidence commands:** `uv run python scripts/run_perf_suite.py --output-dir
.cache/perf/tracing --pytest-target
tests/performance/test_preview_trace_perf.py` and `npm --prefix frontend run
test:e2e:benchmark -- trace-render.benchmark.spec.ts`.
