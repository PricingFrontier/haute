# Mutation Testing

This repo uses Cosmic Ray for bounded mutation-testing runs against high-value modules.
The checked-in Cosmic Ray config is treated as a template; the runner materialises
it with the current project Python interpreter so the mutated test runs execute
inside the active Haute environment instead of the isolated `uvx` tool env.
Each invocation goes through `scripts/run_mutation_pytest.py`, which keeps test
modules and pytest configuration rooted at the repository but gives relative
runtime state a fresh disposable working directory. This prevents one mutant's
`.haute_cache` generations from warming, slowing, or otherwise influencing the
next mutant. Pytest's own temporary tree is a sibling of that synthetic project,
matching the real containment boundary rather than making `tmp_path` appear to
be project data. The script is guarded for Windows `spawn`, so multiprocessing
tests re-import it without recursively starting pytest.

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

## Sharding

Cosmic Ray 8.4.6 executes mutants sequentially, so a large target such as
`json-shred` (~1000 mutants) takes over an hour on one core. As a single 90-min
CI job this sat right at the wall-clock edge and flaked. The fix is **matrix
sharding**: `init` the shared work order once, split its mutants into disjoint
shards that run as independent CI jobs, then merge the per-shard result sessions
and check total survival.

Sharding preserves survival exactly. Each mutant runs the identical
`test-command` with the identical `timeout` exactly once, and a shard executes
its mutants **one at a time** on a fresh, unloaded runner — so per-mutant timing
keeps the configured timeout headroom and every outcome is deterministic. The
merged survival therefore equals an unsharded single-run survival, and the
per-target budgets in [`targets.json`](targets.json) stay valid unchanged. This
equivalence is covered by `tests/test_mutation_sharding.py` (database level) and
verified end to end against real targets (unsharded == sharded survival on
`path-resolution` and `json-cache`, both matching their documented budgets).

Every target explicitly declares a positive-integer `max_pending_per_shard` in
[`targets.json`](targets.json). The planner counts executable (pending) mutants
only and creates `max(1, ceil(pending / cap))` shards for each target. Current
caps are 80 for every target except `json-shred`, which is capped at 20. The
JSON/cache/runtime command currently collects 557 tests. The isolated command
measures 37.5 seconds in pytest and 40.1 seconds end to end on the Windows
development baseline. Its 90-second
per-mutant ceiling and at most 20 mutants bound the test portion of a worst-case
shard to 30 minutes. The workflow allows 40 minutes so checkout, environment
setup, and artifact upload retain explicit headroom. The plan-stage baseline
runs the exact materialised command on the current hosted runner and fails
closed before scheduling shards if that calibration no longer has headroom;
local wall time is platform-dependent because this suite deliberately exercises
native process spawning. The planner rejects a total plan above GitHub Actions'
256-job matrix limit instead of silently overpacking shards.

Run a target sharded locally (each shard sequential, exactly as CI runs it):

```bash
uv run python scripts/run_mutation_suite.py \
  --config mutation/cosmic-ray.json-shred.toml --shards 10
```

> **Why sharding and not per-runner concurrency?** The parallelism here is
> across runners, one shard each, *not* several witness suites at once on one
> runner. Cosmic Ray applies mutations to one source tree and records into one
> session, so concurrent executors over that state are not safe. Within a shard,
> each mutant therefore runs sequentially but receives a fresh synthetic project
> directory from `run_mutation_pytest.py`; its runtime/cache state cannot leak to
> the next mutant, while pytest's temporary inputs remain outside that project
> boundary just as they do in the normal suite.

Timeouts are target-specific upper bounds for one witness-suite invocation.
Most targets use 30 seconds. `json-shred` uses 90 seconds for its maintained
557-test streaming, publication, recovery, and lifecycle command.
`json-cache` uses 60 seconds for its 74-test cold-cache route command, measured
at 32.2 seconds in pytest and 41.1 seconds end to end on the Windows development
baseline. The exact command is measured again during every plan; the extra
target-specific headroom prevents normal hosted-runner variance from classifying
a passing baseline as a mutant timeout, while an actual calibration regression
stops before any shard work is dispatched.

Current CI ratchet:

- mutation target configs are owned by the `mutation/cosmic-ray*.toml` files
- target rationale, survival budgets, and target-calibrated shard caps are owned in [`targets.json`](targets.json)
- PR CI selects and runs the touched target subset for configured high-risk modules
- the mutation workflow runs as three jobs — `plan` builds the shared Cosmic Ray
  work order once and emits a `(target, shard)` matrix; parallel `shard` jobs each
  execute a disjoint mutant slice sequentially; the `mutation` gate job merges the
  shard sessions and checks total survival against the per-target thresholds.
  Target-calibrated shard caps retain wall-clock and artifact-upload headroom,
  which is what makes the baseline per-mutant timeout robust.
- the scheduled/manual run covers the full configured target set; PR runs cover
  the touched subset. Both upload the plan, per-shard/per-target logs, HTML,
  rates, and session dumps
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
  - `json-cache`: `8.56%`
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
