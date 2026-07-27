# Tracing and explainability roadmap

## Scope

Owns expression-evaluation fidelity, row correlation and lineage, waterfall
membership/reconciliation, enrichment failure semantics, and trace-specific
performance evidence. Current behaviour is specified in
[tracing](../tracing/high-level.md) and
[expression parsing](../expression-parsing/high-level.md).

The `AUD-C07` (including the folded `AUD-TRACE-01`) and `AUD-C08` packages are
delivered. Fail-loud Polars-faithful evaluation with single-parse enrichment,
unique row correlation with surfaced relaxation reasons, and structural
waterfall membership are present-tense contracts in the specifications above,
enforced by ordinary regressions including
`tests/test_expression_parser_polars_parity.py`,
`tests/test_expression_parser_w3_fixes.py`,
`tests/test_trace_correlation_remediation.py`, and
`tests/test_trace_waterfall.py`, so they no longer appear as roadmap work.

## Priorities

There are no active tracing and explainability improvement packages.

## Planned improvements

There are no queued tracing improvements. New work must enter this catalogue
as a concrete package with evidence reproduced against `HEAD`.

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
