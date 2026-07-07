# Mutation Testing

This repo uses Cosmic Ray for bounded mutation-testing runs against high-value modules.
The checked-in Cosmic Ray config is treated as a template; the runner materialises
it with the current project Python interpreter so the mutated test runs execute
inside the active Haute environment instead of the isolated `uvx` tool env.

Current targets (budgets + rationale are owned by [`targets.json`](targets.json)):

- `cosmic-ray.job-store.toml`: [src/haute/routes/_job_store.py](../src/haute/routes/_job_store.py)
- `cosmic-ray.path-resolution.toml`: [src/haute/_path_resolution.py](../src/haute/_path_resolution.py)
- `cosmic-ray.registry.toml`: [src/haute/_registry.py](../src/haute/_registry.py)
- `cosmic-ray.output-assembler.toml`: [src/haute/_output_assembler.py](../src/haute/_output_assembler.py)
- `cosmic-ray.jsonpath.toml`: [src/haute/_jsonpath.py](../src/haute/_jsonpath.py)
- `cosmic-ray.json-shred.toml`: [src/haute/_json_shred.py](../src/haute/_json_shred.py)
- `cosmic-ray.json-cache.toml`: [src/haute/routes/json_cache.py](../src/haute/routes/json_cache.py)
- `cosmic-ray.executor.toml`: [src/haute/executor.py](../src/haute/executor.py)

Run the default mutation suite locally:

```bash
uv run python scripts/run_mutation_suite.py
```

List the resolved targets and thresholds without writing artifacts:

```bash
uv run python scripts/run_mutation_suite.py --list
```

Write artifacts somewhere explicit:

```bash
uv run python scripts/run_mutation_suite.py --output-dir .mutation-artifacts
```

Each run gets its own UTC timestamp subdirectory so interrupted or concurrent
runs do not fight over the same SQLite session files.

Run a single target:

```bash
uv run python scripts/run_mutation_suite.py --config mutation/cosmic-ray.job-store.toml
```

Preview the PR-smoke selection for changed files without invoking Cosmic Ray:

```bash
uv run python scripts/run_mutation_suite.py --dry-run --changed-file src/haute/_path_resolution.py
```

Current CI ratchet:

- mutation target configs are owned by the `mutation/cosmic-ray*.toml` files
- target rationale and survival budgets are owned in [`targets.json`](targets.json)
- PR CI selects and runs the touched target subset for configured high-risk modules
- the scheduled/manual mutation workflow runs the full Cosmic Ray suite and uploads
  the run manifest, per-target logs, HTML, rates, and session dumps
- current maximum estimated survivor rates (budgets — authoritative in `targets.json`):
  - `registry`: `0%`
  - `jsonpath`: `4%`
  - `path-resolution`: `5%`
  - `json-shred`: `5%`
  - `job-store`: `6%`
  - `output-assembler`: `10%`
  - `json-cache`: `11%`
  - `executor`: `15%`
- latest local bounded runs:
  - `registry`: `0.00%`
  - `path-resolution`: `3.89%`
  - `json-shred`: `2.32%`
  - `job-store`: `4.90%`
  - `json-cache`: `9.65%`
  - `executor`: `13.43%`
  - (`output-assembler`, `jsonpath` measured under budget during the OUTPUT initiative — see their `targets.json` rationale)

Artifacts include:

- `baseline.stdout.txt` / `baseline.stderr.txt`
- `init.stdout.txt` / `init.stderr.txt`
- `filter-pragma.stdout.txt` / `filter-pragma.stderr.txt`
- `exec.stdout.txt` / `exec.stderr.txt`
- `report.txt`
- `rate.txt`
- `report.html`
- `session.jsonl` when Cosmic Ray's `dump` command succeeds for the session
- per-target `target-summary.json`
- run-level `manifest.json` with selected targets, all configured targets, thresholds, and changed files
- run-level `mutation-summary.json` / `mutation-summary.md` with target pass/fail,
  survival rates, thresholds, and all failures found before the final non-zero exit

The mutation lane runs the configured target set on the weekly schedule and on
manual dispatch. Pull requests run the touched subset when they change a target
module, the target's tests, or the mutation gate itself. This keeps the main
feedback loop fast while still making high-risk correctness changes visible
before merge.
