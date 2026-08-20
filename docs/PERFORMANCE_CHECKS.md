# Local Performance Checks

Use these checks when a change touches execution speed, memory use, bundle
size, graph responsiveness, preview/trace caching, or frontend rendering.
The default backend test run excludes benchmark-style tests with the
`pytest.mark.perf` marker through this `pyproject.toml` setting:

```toml
addopts = "-m 'not perf' --strict-markers --strict-config"
```

Run the opt-in Python performance lane explicitly:

```powershell
uv run python scripts/run_perf_suite.py
```

The runner supports these long options:

- `--output-dir`
- `--max-total-seconds`
- `--max-test-seconds`
- `--rss-poll-interval`
- `--pytest-arg`
- `--pytest-target`
- `--polars-scale`

For a focused preview/trace run with tighter local budgets:

```powershell
uv run python scripts/run_perf_suite.py --pytest-target tests/performance/test_preview_trace_perf.py --max-total-seconds 120 --max-test-seconds 30
```

Run the execution-engine certification added for projection hardening with:

```powershell
uv run python scripts/run_perf_suite.py --output-dir .cache/perf/execution-engine-certification --pytest-target tests/performance/test_execution_engine_certification.py --pytest-target tests/performance/test_polars_scale_scenario.py --max-total-seconds 300 --max-test-seconds 120
```

That certificate is comparative rather than a machine-specific throughput
claim. It retains structured evidence for all of these cases:

- an isolated 50,000-row, 256-column Parquet control and four-column projected
  scan, with semantic parity, an explicit physical `PROJECT 4/256 COLUMNS`
  plan, and a bounded incremental-RSS ratio plus fixed sampler allowance;
- a two-port cached API input where the selected port physically scans two
  columns from a wide Parquet cache and its leased runtime snapshot disappears
  when the execution context is released;
- a 32 MiB source-signature control proving that the first observation performs
  one complete content hash while unchanged warm observations use the native
  revision proof at no more than 5% of the cold-hash median latency;
- a small cached JSON apiInput through a downstream Polars `group_by`, proving
  target-only preview strategy estimation, cache-key construction, and runtime
  loading share one SHA-256 raw-source proof and make no generic runtime xxHash
  call;
- an uncached 20,000-row wide JSONL input projected to two fields, including
  the minimum cooperative-checkpoint count and proof that no cache was created;
- the generated join-to-modelling scenario, whose demand is derived from the
  real target/weight/id/exclude menu configuration before planning.

The certificate is valid only when the runner exits successfully and writes
`perf-report.json`, `perf-report.md`, and `perf-junit.xml` containing every
named scenario. Preserve that output directory with the change evidence.

Run the cache-identity decision gates independently with:

```powershell
uv run python scripts/run_perf_suite.py --pytest-target tests/performance/test_cache_identity_perf.py --max-total-seconds 60 --max-test-seconds 15
```

That lane records the representative row-hash encoding comparison, bounded LRU and
stat-gate operations, and 100-node lineage serialization in the normal performance
report. A relative median improvement must clear 20% before an optimization is accepted.
The versioned little-endian UInt64 row-hash buffer clears that gate; LRU/stat hot-path and
cross-request lineage-memo candidates remain explicit no-change decisions. Stat-cache
retention is bounded as a memory-safety invariant, independently of lookup speed.

The preview/trace lane currently enforces these Phase 9 latency budgets on a
representative multi-branch graph:

- cached target preview: `< 0.5s`
- first trace backed by a full preview cache: `< 0.8s`
- trace-cache hit: `< 0.3s`

CI runs the small Polars scale scenario by default. The larger generated
scenarios are opt-in: use `--polars-scale 1m` for a local one-million-row run
or `--polars-scale 10m` for the ten-million-row run.
The 10m run is not part of default CI.

```powershell
uv run python scripts/run_perf_suite.py --polars-scale 1m
uv run python scripts/run_perf_suite.py --polars-scale 10m
```

Generated scale fixtures are never committed. Keep the artifacts under the
chosen output directory instead. They record semantic evidence and product metrics
for the scenario, alongside the independent runner RSS baseline; they are not a
claim that one machine's raw memory reading is a portable limit.
Numeric thresholds are calibrated only against a baseline, not copied as
hardware-independent constants.

The default artifact directory is `.cache/perf`. A completed run writes:

- `.cache/perf/perf-report.json`
- `.cache/perf/perf-report.md`
- `.cache/perf/perf-junit.xml`

## Retained Performance Decisions

Linux RSS-sampler setup remains uncached. Sampling occurs at coarse admitted
stage boundaries rather than in a row or solver loop, and no representative
profile has shown its setup cost to be material. Reconsider that decision only
if a real-Linux profile measures sampler setup p95 above 1 ms in a
representative run.

The last retained tracing baseline was captured on 2026-07-23 using Windows 10,
Python 3.11.13, and Polars 1.39.3:

| Shape | Total | Correlation | Serialization/validation |
|---|---:|---:|---:|
| Linear cold trace, 9 steps | 40.342 ms | 8.983 ms | 0.299 ms |
| Join cold trace, 6 steps | 40.772 ms | 6.090 ms | 0.161 ms |
| Join with full-preview reuse | 10.353 ms | 4.055 ms | 0.164 ms |
| Join trace-cache hit | 14.181 ms | 8.158 ms | 0.162 ms |
| Multi-frame correlation only | — | 1.525 ms | — |

The corresponding browser p95 was 302.8 ms for a 24-step linear payload and
389.4 ms for a 16-step join/multi-frame payload. Both were below the 500 ms
exceptional-latency threshold. Serialization was at most 0.3 ms in these
shapes, so payload projection, a client trace cache, and a weakened
runtime-input fingerprint were not justified. Future optimisation must add
reproducible evidence for its target shape and preserve trace/export semantics.

The backend baseline command was:

```powershell
uv run python scripts/run_perf_suite.py --output-dir .cache/perf/tracing --pytest-target tests/performance/test_preview_trace_perf.py
```

The frontend baseline command, run from `frontend/`, was:

```powershell
npm run test:e2e:benchmark -- trace-render.benchmark.spec.ts
```

## Frontend Benchmarks

Frontend benchmark specs are tagged with `@benchmark` and excluded from the
normal e2e lane. Run the normal e2e lane with:

```powershell
npm run test:e2e
```

That script maps to:

```text
playwright test --grep-invert @benchmark
```

Run benchmark-only e2e checks from `frontend/` with:

```powershell
npm run test:e2e:benchmark
```

That script maps to:

```text
playwright test --grep @benchmark --project=chromium --retries=0
```

Current benchmark specs:

- `data-preview-scroll.benchmark.spec.ts`
- `job-progress-render.benchmark.spec.ts`
- `large-graph-drag.benchmark.spec.ts`
- `trace-render.benchmark.spec.ts`

Build and bundle checks:

```powershell
npm run build
npm run check:bundle
```

Use `npm run analyze:bundle` when you need source-map detail for bundle
growth, especially after adding frontend dependencies.

## Memory Smoke

Wrap a command with the memory smoke helper when you need a lightweight
process-memory summary:

```powershell
uv run python scripts/memory_smoke.py -- uv run pytest tests/performance/test_preview_trace_perf.py -m perf -q
```

The wrapped command output is mirrored to stderr, and the wrapper emits
JSON to stdout so CI or local scripts can parse the memory summary directly.
