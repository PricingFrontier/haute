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
