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
- `--baseline-report`
- `--max-total-regression-fraction`
- `--max-rss-regression-fraction`
- `--max-test-regression-fraction`
- `--min-test-baseline-seconds`

To compare with a prior report on a compatible machine and scale:

```powershell
uv run python scripts/run_perf_suite.py --baseline-report .cache/perf/perf-report.json
```

Historical comparisons require identical Polars scale, Python major/minor, platform
system and machine, and exact Polars version. Total wall time and measurable RSS
default to a 25% allowance when the collected test identities also match; matching
passed call-phase tests default to 35% even when the suite has gained other tests, and
baseline tests below the 0.10-second noise floor are excluded. An incompatible
baseline is recorded but does not fail the lane; a compatible baseline with no
material matching tests does fail it.
Only a complete successful report can become a baseline: all collected tests must
have one passed call-phase record, identities must be unique, outcome/count/slowest
summaries and wall-time partitions must reconcile, and the independent RSS sampler
must have produced finite evidence. A malformed retained report fails the lane
instead of being ignored or partially compared.
Suite-wide wall-time and RSS comparisons require identical passed call-phase test
identities; added or removed tests skip those suite metrics while still comparing
each material matching test. The Performance workflow keeps a scale-specific
cache and, on a cache miss, downloads the most recent successful same-branch,
same-scale performance artifact as its baseline. Failed runs are never retained
as baselines.

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
  columns from a wide Parquet cache; releasing the execution lease leaves only
  the bounded verification-cache pin, and explicit cache cleanup removes it;
- a 32 MiB source-signature control proving that the first observation performs
  one complete content hash while unchanged warm observations use the native
  revision proof at no more than 5% of the cold-hash median latency;
- a 32 MiB cached-Parquet artifact control proving that one fully verified,
  private snapshot is reused behind the unchanged native revision, within the
  configured entry/byte bounds, at no more than 5% of cold-hash latency;
- a small cached JSON apiInput through a downstream Polars `group_by`, proving
  target-only preview strategy estimation, cache-key construction, and runtime
  loading reuse the cache-build SHA-256 proof after clearing process state and make
  neither a new source hash nor a generic runtime xxHash call; the same run records
  the deliberately favourable upper bound for removing
  all repeated request-local graph preparation and rejects that candidate unless
  it clears the common 20% end-to-end materiality gate;
- repeated complete preview-cache hits proving the producing strategy is reused
  with one planner invocation total and a warm median no greater than 50% of
  the cold materialising request;
- an uncached 20,000-row wide JSONL input projected to two fields, including
  the minimum cooperative-checkpoint count and proof that no cache was created;
- the generated join-to-modelling scenario, whose demand is derived from the
  real target/weight/id/exclude menu configuration before planning;
- an extreme many-to-many join whose proven `10^18`-row upper bound is rejected
  by admission without attempting to materialise the join;
- JSON-array and XML persistent cache builds at 10,000 and 120,000 rows, proving
  that an input-size increase of at least 8× stays within the bounded incremental-
  RSS growth contract;
- a fresh-process restart pair proving committed cache reuse, unchanged generation
  identity, cache-proof telemetry, sanitized terminal telemetry, and process-owned
  snapshot cleanup; and
- the configurable resilience soak (`ci`, `1m`, or `10m`): repeated warm-worker
  calls and forced replacements with RSS/handle-or-fd plateau gates, five cache-
  publication crash points, ENOSPC preservation/recovery, and concurrent builders
  leaving exactly one valid generation and no staging siblings.

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

The ordinary pull-request suite excludes `pytest.mark.perf`. A manual
Performance workflow dispatch defaults to the small `ci` scale and can select
either larger scale explicitly. The scheduled Performance workflow runs the
one-million-row scale weekly and the ten-million-row stress scale monthly,
retaining the same structured artifacts. Locally, use `--polars-scale 1m` or
`--polars-scale 10m` to reproduce those scheduled lanes.

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

The external-scaling certificate was captured on 2026-08-31 using Windows 10,
Python 3.11.13, and Polars 1.39.3:

| Path | Representative load | Network/provider | Local/serialization | Bounded result |
|---|---:|---:|---:|---:|
| MLflow run discovery | 100 candidates | 101 calls; 0.207 ms in-process fake | 0.444 ms handler; 0.769 ms JSON | 19,191 bytes |
| UC complete bundle | 10 commits | 4.709 ms fake Files API | 191.724 ms create; 45.213 ms verify | 2,709 bytes |
| UC complete bundle | 100 commits | 4.960 ms fake Files API | 173.531 ms create; 51.915 ms verify | 24,199 bytes |
| UC complete bundle | 500 commits | 4.539 ms fake Files API | 185.946 ms create; 46.949 ms verify | 119,803 bytes |
| Thread cancellation | one held permit, one waiter | 65.643 ms limiter wait | 117.594 ms cleanup; 0.225 ms bookkeeping tail | permit returned |

The MLflow and Files API providers are deterministic in-process stand-ins: their
times prove phase attribution and call counts, not service latency. Production
`mlflow_run_discovery_completed` and `uc_publish_measurement` events supply live
network evidence without identifiers or payload data. The certificate separates
provider/network, response serialization, executor queue, worker execution, and
post-response cleanup in its retained JSON evidence. Operational re-evaluation
gates are 5,000 ms p95 total MLflow discovery at the 100-candidate cap and
30,000 ms p95 UC upload for bundles at or below 25 MiB.

These measurements retire all three speculative redesigns. MLflow keeps its
one-search-plus-at-most-100-artifact-call path; provider-side metadata/filtering
or a bounded index requires a live response-budget breach. UC keeps independently
restorable complete bundles and five-generation retention: its 500-commit sample
is about 0.46% of the 25 MiB gate, so incremental/checkpoint recovery complexity
is not justified. Compatibility threads continue holding admission and limiter
ownership until real worker exit; in the gated sample the permit was reusable
0.754 ms after worker release. Heavy production execution already defaults to
killable process isolation, so a generic cooperative-cancellation protocol would
add an unusable signal to callables that cannot honour it.

Reproduce the certificate and retain its JSON, Markdown, and JUnit artifacts with:

```powershell
uv run python scripts/run_perf_suite.py --output-dir .cache/perf/cx12 --pytest-target tests/performance/test_external_scaling_perf.py --max-total-seconds 60 --max-test-seconds 20
```

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
When the wrapper observes RSS for the child PID, that exact sample series owns
the reported child peak. POSIX `RUSAGE_CHILDREN` is a process-wide high-water
mark and is used only when no live child sample was available; this prevents an
earlier high-memory child from being attributed to a later command.
