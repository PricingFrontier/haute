# PR #227 cache evidence inventory

Evidence inventory for branch `banding-rating-ui`, based on the PR merge-base range `git diff a71c947000e46f3b9331e96ec1a034eaa3739c97...97f3e9909998460bae5dbdfedf821eb9189581a6`. The local `main` ref was stale at `0f555f7`; earlier `main...HEAD` counts covered an older range. This note records observed behavior only; it contains no recommendations or review conclusions.

## Changed versus pre-existing

Exact status from `git diff a71c947000e46f3b9331e96ec1a034eaa3739c97...97f3e9909998460bae5dbdfedf821eb9189581a6 --name-status -- src/haute/_source_cache.py src/haute/_node_snapshots.py tests/test_node_snapshot_retention.py tests/test_node_snapshot_cross_process.py` is: `M src/haute/_source_cache.py`; `A src/haute/_node_snapshots.py`; `A tests/test_node_snapshot_retention.py`; `A tests/test_node_snapshot_cross_process.py`. Thus `_source_cache.py` is modified, while the node-output store, its retention implementation, and both cited retention/cross-process test files are added on this branch.

## Store, identity, lease, and publication

| Area | Verified implementation | Tests asserting behavior |
|---|---|---|
| Shared store and identity root | `SourceCacheStore` owns `root/.haute_cache/inputs`; input byte default is 20 GiB and generation default is 64; process-local coordination is keyed by resolved inputs root (`src/haute/_source_cache.py:501-565`). | `tests/test_node_snapshot_retention.py:112-131` (mapping/identity setup); `tests/test_node_snapshot_retention.py:1111-1157` (quota defaults and invalid values). |
| Node-output store and separate budgets | `NodeSnapshotStore` extends the shared store, has separate node-output byte/generation limits, creates `.locks`, `.processes`, and `.node-slots`, and initializes a file lease lock (`src/haute/_node_snapshots.py:839-899`). `usage_report` reports both buckets (`src/haute/_node_snapshots.py:903-930`). | `tests/test_node_snapshot_retention.py:805-858`, `860-952`, `1111-1187`. |
| Staging and cancellation cleanup handoff | `stage_node_output` creates request-owned `.staging-{token}`; `discard_node_output_staging` removes the token-specific staging directories (`src/haute/_node_snapshots.py:1629-1655`). | `tests/test_node_snapshot_cross_process.py:147-174`, `tests/test_node_snapshot_cross_process.py:345-381`; `tests/test_node_snapshot_retention.py:1036-1077`. |
| Publication identity/dependencies | Staged metadata records slot, signature, columns, and sorted dependency generation IDs; invalid/missing/self dependencies fail (`src/haute/_node_snapshots.py:1657-1710`). Publication rechecks dependency freshness and current generation; only explicit builds replace corruption or refresh (`src/haute/_node_snapshots.py:1712-1736`, `1738-1765`). | `tests/test_node_snapshot_retention.py:493-520`, `527-627`; `tests/test_node_snapshot_cross_process.py:345-381`. |
| Publication lease ordering | Publication admits under the coordination lease lock, moves staging to final generation, creates the publisher lease marker and in-process lease before writing the current pointer; failures in that initial pointer/lease try block release the lease and delete the final directory (`src/haute/_node_snapshots.py:1792-1835`). Later slot-index publication failures have a separate cleanup path and can leave the pointer (`src/haute/_node_snapshots.py:1836-1870`). | `tests/test_node_snapshot_retention.py:628-683`; `tests/test_node_snapshot_cross_process.py:147-174`. |
| Input lease lifecycle | `lease` and `lease_generation` increment process-local counts before yielding and decrement/remove them in `finally`, then retire unleased generations (`src/haute/_source_cache.py:1025-1065`). `leased_generation_ids` exposes parent-held generations for child admission (`src/haute/_source_cache.py:1067-1080`). | `tests/test_node_snapshot_retention.py:428-490`; `tests/test_node_snapshot_cross_process.py:176-341`. |
| Input versus node-output coordination | Source-cache input leases are process-local counts (`src/haute/_source_cache.py:548-565`, `1025-1065`). Node-output publication uses the shared `.locks/leases.lock`, filesystem holder markers, and cross-process holder checks (`src/haute/_node_snapshots.py:883-899`, `1792-1835`, `1980-2035`). | `tests/test_node_snapshot_cross_process.py:176-341` exercises node-output readers and cross-process eviction/clear behavior; the cited node-output tests do not assert cross-process input leases. |
| Retirement and grace | `retire_unleased` preserves current and leased generations; retirement uses current-pointer mtime grace and removes only unleased generations (`src/haute/_source_cache.py:1082-1122`). | `tests/test_node_snapshot_retention.py:316-389`, `454-490`; `tests/test_node_snapshot_cross_process.py:176-341`. |

## Locks and process coordination

| Mechanism | Verified lifecycle/effect | Tests |
|---|---|---|
| Per-root/per-identity process locks | Source store shares an `RLock` and identity lock table by resolved root (`src/haute/_source_cache.py:504-565`). Node store adds `.locks/leases.lock` and per-process coordination (`src/haute/_node_snapshots.py:883-899`). | `tests/test_node_snapshot_cross_process.py:147-174`, `345-381`. |
| Cross-process holder markers | Cross-process tests cover paused readers surviving eviction/clear, dead reader markers becoming evictable, lease marker timing, and a waiting lease failing cleanly (`tests/test_node_snapshot_cross_process.py:176-341`). | Same cited tests. |

## Budgets, units, and eviction

| Budget/operation | Verified evidence | Tests |
|---|---|---|
| Input cache | `max_bytes` is a positive integer in bytes, default `20 * 1024 * 1024 * 1024`; `max_generations` is a positive integer, default `64` (`src/haute/_source_cache.py:527-540`). | `tests/test_node_snapshot_retention.py:1111-1187`. |
| Node-output cache | Node-output defaults come from environment variables/constants; invalid values are rejected (`src/haute/_node_snapshots.py:858-882`). Tests pin defaults at 40 GiB and 512 generations, environment overrides, constructor overrides, and separation from input limits (`tests/test_node_snapshot_retention.py:1111-1187`). | Same cited tests. |
| Usage accounting | `_bucket_usage` counts generation directories with metadata and sums part files plus staging files, classifying unknown identities into both buckets (`src/haute/_source_cache.py:787-863`). | `tests/test_node_snapshot_retention.py:693-755`, `860-1035`, `1079-1109`. |
| Node-output admission | Admission projects byte and generation counts, selects unleased automatic candidates, retires superseded/LRU candidates, and rejects if pinned/in-use candidates cannot make room (`src/haute/_node_snapshots.py:1880-1965`). | `tests/test_node_snapshot_retention.py:345-400`, `693-755`, `833-952`, `1036-1109`. |

## Cancellation/publication evidence and known literals

The literal implementation messages/limits observed are: `"node-output snapshots are published, not built here"` (`src/haute/_source_cache.py:910-913`); `"only an explicit build can refresh a node-output snapshot"` (`src/haute/_node_snapshots.py:1758-1759`); and quota rejection text stating pinned and in-use snapshots remain until explicitly refreshed or cleared (`src/haute/_node_snapshots.py:1917-1921`, `1959-1963`). The source contains no `TODO` or `FIXME` in the cited store/retention implementation files (verified with `rg -n "TODO|FIXME" src/haute/_source_cache.py src/haute/_node_snapshots.py`).

Cancellation-related publication cleanup is represented by token-specific staging discard and failed publication cleanup (`src/haute/_node_snapshots.py:1650-1655`, `1792-1835`); the added cross-process tests cover worker publication serialization and cleanup (`tests/test_node_snapshot_cross_process.py:147-174`, `345-381`).

## Targeted verification

Exact command:

`uv run pytest tests/test_node_snapshot_retention.py tests/test_node_snapshot_cross_process.py tests/test_seed_plans.py tests/test_snapshot_seeding.py tests/test_preview_snapshot_seeding.py tests/test_training_seeding.py tests/test_optimiser_seeding.py -q`

Result: exit status 0; `253 passed, 7 warnings in 135.13s (0:02:15)`.

## Frontend cache invalidation inventory

Frontend targeted verification command:

`npm --prefix frontend test -- src/hooks/__tests__/useNodeDataCache.test.tsx src/hooks/__tests__/useNodeDataCache.delegation.test.tsx src/hooks/__tests__/usePipelineAPI.nodeDataEpoch.test.ts src/stores/__tests__/useNodeDataStore.test.ts src/__tests__/editors/BandingEditorStats.test.tsx src/__tests__/editors/RatingStepEditorLevels.test.tsx src/components/__tests__/CacheSettingsModal.test.tsx`

Result: exit status 0; `7 passed` test files and `124 passed` tests; Vitest duration `32.26s` (transform 4.44s, setup 20.37s, import 13.84s, tests 15.13s, environment 147.15s).

## PR #227 CI snapshot

Commands:

`gh pr checks 227 --json name,state,bucket,link`

`gh pr view 227 --json headRefOid,baseRefOid,state,mergeStateStatus`

The observed PR metadata is `baseRefOid=a71c947000e46f3b9331e96ec1a034eaa3739c97`, `headRefOid=97f3e9909998460bae5dbdfedf821eb9189581a6`, `state=OPEN`, `mergeStateStatus=UNSTABLE`.

At that current head, exact check-state counts were: `SUCCESS=27`, `FAILURE=1`, `IN_PROGRESS=40`, `QUEUED=278`, `SKIPPED=2`. The failing check was `perf` (`FAILURE`), job `106692105251`, run `35711247240`; URL: https://github.com/PricingFrontier/haute/actions/runs/35711247240/job/106692105251. Read-only log retrieval via `gh api repos/PricingFrontier/haute/actions/jobs/106692105251/logs` reported one failing test: `tests/performance/test_write_strategy_memory.py::test_write_strategy_memory`, where native `passthrough_native` incremental peak ratio was `1.5974555202455845`, below the required `1.6` (`361.4 MB -> 577.3 MB`); the perf suite ended with `1 failed, 56 passed, 21322 deselected` and exit code 1.

The exact PR range contains 327 files, 58,085 additions, and 13,701 deletions. Its `git diff a71c947000e46f3b9331e96ec1a034eaa3739c97...97f3e9909998460bae5dbdfedf821eb9189581a6 --name-only` paths group as: production backend (`src/haute/**`) 63; production frontend (`frontend/src/**`, `frontend/scripts/**`, `frontend/package.json`) 108; tests/support (`tests/**`, `frontend/e2e/**`, `.github/workflows/**`, `scripts/**`) 116; specs/docs (`specs/**`, `docs/**`) 39; other 1.

### Latest CI refresh — 2026-09-22

`gh pr checks 227 --json name,state,bucket,link` reported: `SUCCESS=114`, `FAILURE=6`, `IN_PROGRESS=39`, `QUEUED=187`, `SKIPPED=3`. Failed jobs and read-only log evidence:

| Job | Result evidence |
|---|---|
| `perf` | `tests/performance/test_write_strategy_memory.py::test_write_strategy_memory`; native `passthrough_native` ratio `1.5974555202455845 < 1.6` (`361.4 MB -> 577.3 MB`); `1 failed, 56 passed, 21322 deselected`. |
| `backend-compat (3.11)` | `tests/test_write_sandbox_lint.py::test_write_apis_derive_from_scratch_fixture`; four new write violations were reported in `tests/test_cache_nodes_routes.py`: `Path.write_text` at lines 538 and 641, and `Path.write_bytes` at lines 545 and 548; `1 failed, 21187 passed, 22 skipped`. |
| `backend-compat (3.13)` | Same sandbox-lint assertion and four `tests/test_cache_nodes_routes.py` violations: `Path.write_text` at lines 538 and 641, and `Path.write_bytes` at lines 545 and 548; `1 failed, 21187 passed, 22 skipped`. |
| `backend-coverage-shard (2)` | Two failures: `tests/test_workflow_coverage.py::test_workflow_coverage_ledger_is_valid`, missing scenario `W10-S02` test title `renders the shared cache button state of the data it reads` in `frontend/src/panels/__tests__/ExplorePreview.test.tsx`; and `tests/test_write_sandbox_lint.py::test_write_apis_derive_from_scratch_fixture` with four `tests/test_cache_nodes_routes.py` violations at lines 538, 545, 548, and 641 (write APIs as above). Result `2 failed, 10646 passed, 13 skipped`. The same log also contains an unhandled thread warning for missing job `bb928d4172f3`, followed by `KeyError`. |
| `frontend` | The failing stage is the bundle budget: `Initial JS gzip size 292.8 KiB exceeds budget 292 KiB`, followed by `FAIL Frontend bundle budget` at 09:37:46 UTC. Generated contracts, typecheck, ESLint, build, PR benchmark, frontend tests and critical coverage passed. Vitest reported `367 passed` files and `6687 passed | 1 expected fail (6688)` tests; the expected-fail count is not the cause of this job failure. |
| `browser-e2e` | 70 passed, 2 failed, 3 did not run. `frontend/e2e/canvas-assurance.spec.ts:146:3` failed at screenshot assertion `expect(locator).toHaveScreenshot(expected)` (`canvas-assurance.spec.ts:67:25`, called at line 182) for `mixed-banding-desktop-1440x900-linux-chromium.png`; `frontend/e2e/explore.spec.ts:50:3` failed because `getByRole('region', { name: 'Pivot 1' }).getByRole('table')` was not visible after 60s (`element(s) not found`) at line 190. |

CI job URLs: `perf` https://github.com/PricingFrontier/haute/actions/runs/35711247240/job/106692105251; `backend-compat (3.11)` https://github.com/PricingFrontier/haute/actions/runs/35711247240/job/106692105420; `backend-compat (3.13)` https://github.com/PricingFrontier/haute/actions/runs/35711247240/job/106692105321; `backend-coverage-shard (2)` https://github.com/PricingFrontier/haute/actions/runs/35711247240/job/106692105326; `frontend` https://github.com/PricingFrontier/haute/actions/runs/35711247240/job/106692105151; `browser-e2e` https://github.com/PricingFrontier/haute/actions/runs/35711247240/job/106692105593.

The backend compatibility and coverage failures are assertion/test-lint outputs after dependency installation and test execution; the retrieved logs show no dependency-install failure. The browser failures are a screenshot assertion and a Playwright locator timeout. The frontend failure is the bundle-budget gate, not its passing unit tests.

## Frontend invalidation fields

| Entry point | Verified fields/behavior | File:line |
|---|---|---|
| Shared node-data slot | Slot fields include `dataVersion`, `retention`, `generation`, `buildEndpoint`, and `clearEndpoint`; delegated builds carry a token and canceller. | `frontend/src/stores/useNodeDataStore.ts:26-46`, `53-63` |
| Epoch invalidation | `epoch` increments when a generation/version changes; terminal jobs, delegated completion, forgotten slots, preview captures, and reset also advance it. | `frontend/src/stores/useNodeDataStore.ts:117-130`, `273-284`, `407-420`, `465-494`, `518-531` |
| Bounded capture record | Announced generation IDs are deduplicated and truncated to `ANNOUNCED_CAPTURES_CAP`. | `frontend/src/stores/useNodeDataStore.ts:127-130`, `496-515` |
| Identity and stale response gate | The node-data hook hashes graph identity plus active source; asynchronous publication requires the current identity and current document fence. | `frontend/src/hooks/useNodeDataCache.ts:157-182`, `212-225` |
| Node-data request cancellation | Point inspection uses an optional `AbortSignal`; delegated builds create an `AbortController`; node-data build cancellation calls `cancelNodeData`; clear calls `clearNodeData` and delegated clear. | `frontend/src/hooks/useNodeDataCache.ts:199-209`, `224-257`, `265-332`, `459-497` |
| Refresh/build behavior | `build(false)` runs ordinary cache build; `build(true)` refreshes; missing state runs, while stale/partial/corrupt state refreshes. | `frontend/src/hooks/useNodeDataCache.ts:356-423`, `428-451` |
| Preview stale-response checks | Preview requests use a monotonic request sequence, document fence, structural version, and abort controller; success and failure handlers ignore superseded requests. | `frontend/src/hooks/usePipelineAPI.ts:618-672`, `745-814` |
| Preview selection cancellation/refresh | Selection increments the request sequence and aborts prior work; explicit cancel aborts and clears debounce; refresh repeats this and checks stale upstream source tags. | `frontend/src/hooks/usePipelineAPI.ts:821-872`, `875-930` |
| Node-data epoch refetch | A displayed preview is fetched again when its recorded frame/cache epoch differs from the store epoch; unmount invalidates and aborts preview work. | `frontend/src/hooks/usePipelineAPI.ts:1317-1351` |
| Cache clear/build client endpoints | Cache usage is `GET /api/cache/usage`; identity clear is `POST /api/cache/clear`; input build/status/cancel/clear are `/api/input-cache/*`; node point/run/status/cancel endpoints are `/api/node-data/*`. All cited functions accept abort signals; build/run also expose timeout options. | `frontend/src/api/client.ts:1189-1224`, `1231-1264`, `1270-1320` |
