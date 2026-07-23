# Tracing performance baseline — 2026-07-23

This is the post-roadmap baseline for the tracing implementation delivered on 2026-07-23. It
replaces the July 6 point-in-time numbers as evidence for future tracing work; it is not a promise
that every graph or model will have the same latency.

## Environment and commands

- Windows 10 build 26200, Python 3.11.13, Polars 1.39.3.
- Backend evidence:
  `uv run python scripts/run_perf_suite.py --output-dir .cache/perf/tracing-2026-07-23 --pytest-target tests/performance/test_preview_trace_perf.py`
- Browser evidence:
  `npm --prefix frontend run test:e2e:benchmark -- trace-render.benchmark.spec.ts`
- Backend runner result: 7 passed (2.79 s lane time; 1.48 s reported by pytest);
  independently sampled peak process RSS was 157,532,160 bytes.
- Browser result: 1 Chromium benchmark passed in 18.8 s.

The backend timings below are one local run of the 3,000-row CI shape. The test retains generous
regression ceilings so ordinary machine noise does not turn point measurements into flaky gates;
the exact measurements are attached as `haute_perf_evidence` by the performance runner.

## Backend stage evidence

| Shape and path | Total | Correlation | Serialization + response validation |
| --- | ---: | ---: | ---: |
| Linear, cold trace, 9 steps | 40.342 ms | 8.983 ms | 0.299 ms |
| Join, cold trace, 6 steps | 40.772 ms | 6.090 ms | 0.161 ms |
| Join, full-preview reuse | 10.353 ms | 4.055 ms | 0.164 ms |
| Join, trace-cache hit | 14.181 ms | 8.158 ms | 0.162 ms |
| Multi-frame, two-frame correlation only | — | 1.525 ms | — |

The representative join preview executed cold in 33.029 ms and hit its preview cache in 3.471 ms.
The route supersession scenarios for both preview and trace also passed: six same-key requests
entered at most one worker concurrently, with obsolete work rejected.

The full-preview reuse case now performs exactly one preview-cache lookup. A target-only preview
retains only the selected node and therefore cannot satisfy the trace's full-ancestor evidence
invariant; the guaranteed target-only lookup miss was removed while the shared full-lineage reuse
path remains covered.

## Browser rendering evidence

The Chromium benchmark measures from an in-page preview-cell click through trace response
validation and React rendering, ending after the target step card is mounted and two animation
frames have completed. It runs six times per shape and discards the first warm-up sample.

| Payload shape | Steady-state samples | p95 |
| --- | --- | ---: |
| Linear: 24 expanded steps, 32 values per row | 245.5, 144.8, 290.6, 122.0, 302.8 ms | 302.8 ms |
| Join/multi-frame: 16 expanded steps, 32 values per row | 389.4, 319.9, 115.0, 271.3, 103.9 ms | 389.4 ms |

Both deliberately heavy panel shapes remain below the 500 ms exceptional-latency threshold.
Consequently a normal fast trace does not replace the node panel with progress UI; only a request
still pending beyond that threshold does.

## Decisions and remaining hypotheses

- No row-value payload projection was introduced. Serialization plus explicit response-contract
  validation is at or below 0.3 ms in these shapes, and export plus the expanded all-columns view
  deliberately consume the exact validated payload.
- No client trace cache, short-TTL runtime-input fingerprint, or combined preview/trace byte budget
  was introduced. Current evidence does not justify duplicating invalidation logic or weakening
  immediate runtime-input invalidation.
- A real cold model-score trace is not represented because this repository has no stable model
  artifact for a deterministic performance fixture. Model loading/scoring therefore remains a
  separate measured prerequisite for any model-specific optimisation.
- The multi-frame backend number isolates correlation; cold multi-frame JSON shredding and source
  loading are owned by their source/cache components and are not attributed to trace assembly
  here.

Any future optimisation must add a failing structural or performance regression first, preserve
the exact trace/export semantics, and append a new dated baseline rather than editing these
measurements in place.
